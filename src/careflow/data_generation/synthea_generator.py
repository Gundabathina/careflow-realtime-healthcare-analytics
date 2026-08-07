"""Synthea-based synthetic patient data generation for CareFlow Analytics.

Runs the Synthea patient simulator from a local installation and imports
its CSV/FHIR output into the project's raw data layer, recording a
generation manifest for provenance.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from careflow.config import Config, SyntheaSettings, get_project_root, load_config
from careflow.logging_config import get_logger

logger = get_logger(__name__)

SUBPROCESS_TIMEOUT_SECONDS = 60
RUN_SCRIPT_NAME = "run_synthea"


class SyntheaError(Exception):
    """Base exception for Synthea setup and generation errors."""


class JavaNotFoundError(SyntheaError):
    """Raised when a usable Java runtime cannot be found."""


class SyntheaNotInstalledError(SyntheaError):
    """Raised when the configured Synthea installation is missing or incomplete."""


class OverwriteConflictError(SyntheaError):
    """Raised when generated output already exists and overwrite is disabled."""


class SyntheaExecutionError(SyntheaError):
    """Raised when the Synthea subprocess exits with a non-zero status."""


class MissingOutputError(SyntheaError):
    """Raised when Synthea completes but expected output files are absent."""


@dataclass(frozen=True)
class VersionCheck:
    """Result of probing for an external tool's availability and version."""

    available: bool
    version: str | None


@dataclass(frozen=True)
class GeneratedFile:
    """A single file copied from a Synthea run into the raw data layer."""

    filename: str
    format: str
    destination_relative_path: str
    size_bytes: int
    sha256: str
    row_count: int | None


def check_java_available() -> VersionCheck:
    """Return whether a Java runtime is available, and its reported version."""
    try:
        result = subprocess.run(
            ["java", "-version"],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return VersionCheck(available=False, version=None)
    if result.returncode != 0:
        return VersionCheck(available=False, version=None)
    output = (result.stderr or result.stdout or "").strip()
    first_line = output.splitlines()[0] if output else None
    return VersionCheck(available=True, version=first_line)


def check_git_available() -> VersionCheck:
    """Return whether Git is available, and its reported version."""
    try:
        result = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return VersionCheck(available=False, version=None)
    if result.returncode != 0:
        return VersionCheck(available=False, version=None)
    output = (result.stdout or "").strip()
    return VersionCheck(available=True, version=output or None)


def get_synthea_commit_hash(install_dir: Path) -> str | None:
    """Best-effort lookup of the installed Synthea repository's commit hash.

    Returns ``None`` if the installation is not a Git checkout or the hash
    cannot be determined; this is informational only and never fatal.
    """
    if not (install_dir / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(install_dir), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    commit = result.stdout.strip()
    return commit or None


def _relative_to_root(path: Path) -> str:
    """Render ``path`` relative to the project root, or a redacted marker."""
    root = get_project_root()
    try:
        return str(Path(path).resolve().relative_to(root))
    except ValueError:
        return "<external-path-redacted>"


def _sanitize_command_args(args: Sequence[str]) -> list[str]:
    """Strip absolute home/system paths out of a command for manifest storage.

    Paths inside the project root are rewritten relative to the root.
    Absolute paths elsewhere have the user's home directory redacted, or
    are fully redacted if no home-relative form is available.
    """
    root = get_project_root()
    home = str(Path.home())
    sanitized: list[str] = []

    for raw in args:
        text = str(raw)
        key_prefix = None
        value = text
        if text.startswith("--") and "=" in text:
            key_prefix, value = text.split("=", 1)

        candidate = Path(value)
        if candidate.is_absolute():
            try:
                value = str(candidate.resolve().relative_to(root))
            except ValueError:
                if home and home in value:
                    value = value.replace(home, "<home>")
                else:
                    value = "<external-path-redacted>"

        text = f"{key_prefix}={value}" if key_prefix else value
        sanitized.append(text)

    return sanitized


def _sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _count_csv_rows_excluding_header(path: Path) -> int:
    with path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header is None:
            return 0
        return sum(1 for _ in reader)


class SyntheaGenerator:
    """Orchestrates running Synthea and importing its output into CareFlow."""

    def __init__(
        self,
        settings: SyntheaSettings,
        careflow_version: str = "unknown",
        subprocess_runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ) -> None:
        self._settings = settings
        self._careflow_version = careflow_version
        self._run = subprocess_runner

    # -- validation ----------------------------------------------------

    def validate_java(self) -> VersionCheck:
        check = check_java_available()
        if not check.available:
            raise JavaNotFoundError(
                "Java runtime not found. Install a JDK (11+) and ensure "
                "'java' is on PATH before generating data."
            )
        logger.info("Detected Java: %s", check.version)
        return check

    def validate_installation(self) -> Path:
        install_dir = self._settings.install_dir
        run_script = install_dir / RUN_SCRIPT_NAME
        if not install_dir.is_dir() or not run_script.is_file():
            raise SyntheaNotInstalledError(
                f"Synthea installation not found at {install_dir}. "
                "Run scripts/setup_synthea.py first."
            )
        return run_script

    # -- command construction -------------------------------------------

    def build_command(self, run_script: Path, output_dir: Path) -> list[str]:
        settings = self._settings
        command: list[str] = [str(run_script), "-p", str(settings.population_size)]
        if settings.seed is not None:
            command += ["-s", str(settings.seed)]
        command.append(settings.state)
        command.append(
            f"--exporter.csv.export={'true' if settings.export_csv else 'false'}"
        )
        command.append(
            f"--exporter.fhir.export={'true' if settings.export_fhir else 'false'}"
        )
        command.append(f"--exporter.baseDirectory={output_dir}")
        return command

    # -- overwrite handling -----------------------------------------------

    def _check_overwrite(self) -> None:
        settings = self._settings
        if settings.overwrite:
            return
        targets = []
        if settings.export_csv:
            targets.append(settings.raw_csv_dir)
        if settings.export_fhir:
            targets.append(settings.raw_fhir_dir)
        for target in targets:
            if target.is_dir() and any(target.iterdir()):
                raise OverwriteConflictError(
                    f"Destination {target} already contains generated data. "
                    "Set overwrite: true in configuration to replace it."
                )

    # -- output collection --------------------------------------------------

    def _collect_and_copy_output(self, run_dir: Path) -> list[GeneratedFile]:
        """Validate all requested output exists, then copy it atomically.

        Both format checks run before any file is copied so a missing
        FHIR (or CSV) directory never leaves a partially-imported result.
        """
        settings = self._settings
        planned: list[tuple[Path, Path, str]] = []

        if settings.export_csv:
            csv_source = run_dir / "csv"
            csv_files = sorted(csv_source.glob("*.csv")) if csv_source.is_dir() else []
            if not csv_files:
                raise MissingOutputError(
                    f"Synthea reported success but no CSV output was found in {csv_source}."
                )
            planned += [(f, settings.raw_csv_dir / f.name, "csv") for f in csv_files]

        if settings.export_fhir:
            fhir_source = run_dir / "fhir"
            fhir_files = sorted(fhir_source.glob("*.json")) if fhir_source.is_dir() else []
            if not fhir_files:
                raise MissingOutputError(
                    f"Synthea reported success but no FHIR output was found in {fhir_source}."
                )
            planned += [(f, settings.raw_fhir_dir / f.name, "fhir") for f in fhir_files]

        collected: list[GeneratedFile] = []
        for src, dest, fmt in planned:
            dest.parent.mkdir(parents=True, exist_ok=True)
            collected.append(self._copy_file(src, dest, fmt))
        return collected

    def _copy_file(self, src: Path, dest: Path, fmt: str) -> GeneratedFile:
        shutil.copy2(src, dest)
        row_count = _count_csv_rows_excluding_header(dest) if fmt == "csv" else None
        return GeneratedFile(
            filename=dest.name,
            format=fmt,
            destination_relative_path=_relative_to_root(dest),
            size_bytes=dest.stat().st_size,
            sha256=_sha256_of_file(dest),
            row_count=row_count,
        )

    # -- manifest ------------------------------------------------------------

    def _build_manifest(
        self,
        started_at: datetime,
        command: Sequence[str],
        files: Sequence[GeneratedFile],
    ) -> dict:
        settings = self._settings
        commit = get_synthea_commit_hash(settings.install_dir)
        return {
            "generated_at_utc": started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "careflow_version": self._careflow_version,
            "synthea_commit": commit,
            "request": {
                "population": settings.population_size,
                "state": settings.state,
                "seed": settings.seed,
                "export_formats": {
                    "csv": settings.export_csv,
                    "fhir": settings.export_fhir,
                },
                "overwrite": settings.overwrite,
            },
            "command": _sanitize_command_args(command),
            "files": [
                {
                    "filename": f.filename,
                    "format": f.format,
                    "destination_path": f.destination_relative_path,
                    "size_bytes": f.size_bytes,
                    "sha256": f.sha256,
                    "row_count": f.row_count,
                }
                for f in files
            ],
        }

    def _write_manifest(self, manifest: dict) -> None:
        path = self._settings.manifest_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
            fh.write("\n")

    # -- main entry point -----------------------------------------------------

    def generate(self) -> dict:
        """Run Synthea and import its output, returning the generation manifest.

        Raises on any failure; destination directories and the manifest are
        left untouched unless the entire run (validation, generation, and
        output collection) succeeds.
        """
        settings = self._settings
        if not settings.export_csv and not settings.export_fhir:
            raise ValueError("At least one export format (csv or fhir) must be enabled.")

        self.validate_java()
        run_script = self.validate_installation()
        self._check_overwrite()

        settings.temp_output_dir.mkdir(parents=True, exist_ok=True)
        started_at = datetime.now(timezone.utc)
        run_id = f"run_{started_at.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
        run_dir = settings.temp_output_dir / run_id

        try:
            run_dir.mkdir(parents=True, exist_ok=False)
            command = self.build_command(run_script, run_dir)
            logger.info(
                "Running Synthea: %s", " ".join(_sanitize_command_args(command))
            )

            result = self._run(
                command,
                cwd=str(settings.install_dir),
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                stderr = (result.stderr or "").strip()
                raise SyntheaExecutionError(
                    f"Synthea exited with status {result.returncode}: {stderr[:2000]}"
                )

            files = self._collect_and_copy_output(run_dir)
            manifest = self._build_manifest(started_at, command, files)
            self._write_manifest(manifest)
            logger.info(
                "Synthea generation complete: %d file(s) imported.", len(files)
            )
            return manifest
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)


def create_generator(config: Config | None = None) -> SyntheaGenerator:
    """Build a :class:`SyntheaGenerator` from the project configuration."""
    cfg = config or load_config()
    return SyntheaGenerator(settings=cfg.synthea, careflow_version=cfg.project_version)
