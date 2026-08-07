"""Tests for careflow.data_generation.synthea_generator.

All tests run against temporary directories with subprocess calls mocked
out. None of them require Java, Git, network access, or a real Synthea
installation.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from careflow.config import SyntheaSettings
from careflow.data_generation import synthea_generator as sg

# Captured before the autouse fixture below patches it, so the two tests
# that exercise the real implementation can restore it explicitly.
_REAL_CHECK_JAVA_AVAILABLE = sg.check_java_available


def make_settings(tmp_path: Path, **overrides) -> SyntheaSettings:
    defaults = dict(
        population_size=50,
        state="Massachusetts",
        seed=42,
        export_csv=True,
        export_fhir=False,
        overwrite=False,
        repository_url="https://github.com/synthetichealth/synthea.git",
        install_dir=tmp_path / "tools" / "synthea",
        temp_output_dir=tmp_path / "data" / "tmp" / "synthea_generation",
        manifest_path=tmp_path / "data" / "raw" / "synthea" / "generation_manifest.json",
        raw_csv_dir=tmp_path / "data" / "raw" / "synthea" / "csv",
        raw_fhir_dir=tmp_path / "data" / "raw" / "synthea" / "fhir",
    )
    defaults.update(overrides)
    return SyntheaSettings(**defaults)


def install_fake_synthea(settings: SyntheaSettings) -> None:
    settings.install_dir.mkdir(parents=True, exist_ok=True)
    (settings.install_dir / sg.RUN_SCRIPT_NAME).write_text("#!/bin/sh\necho fake\n")


def make_fake_runner(create_csv=False, create_fhir=False, returncode=0, stderr=""):
    def _runner(command, cwd=None, capture_output=True, text=True):
        base_dir = None
        for arg in command:
            if arg.startswith("--exporter.baseDirectory="):
                base_dir = Path(arg.split("=", 1)[1])
        assert base_dir is not None

        if returncode == 0:
            if create_csv:
                csv_dir = base_dir / "csv"
                csv_dir.mkdir(parents=True, exist_ok=True)
                (csv_dir / "patients.csv").write_text("Id,NAME\n1,Alice\n2,Bob\n")
            if create_fhir:
                fhir_dir = base_dir / "fhir"
                fhir_dir.mkdir(parents=True, exist_ok=True)
                (fhir_dir / "Alice.json").write_text('{"resourceType": "Bundle"}')

        return subprocess.CompletedProcess(
            args=command, returncode=returncode, stdout="", stderr=stderr
        )

    return _runner


@pytest.fixture(autouse=True)
def _default_java_available(monkeypatch):
    monkeypatch.setattr(
        sg, "check_java_available", lambda: sg.VersionCheck(True, "openjdk 17.0.1 fake")
    )


# -- Java / Git availability checks -----------------------------------------


def test_check_java_available_true(monkeypatch):
    monkeypatch.setattr(sg, "check_java_available", _REAL_CHECK_JAVA_AVAILABLE)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr='openjdk version "17.0.1"\n')

    monkeypatch.setattr(sg.subprocess, "run", fake_run)
    check = sg.check_java_available()
    assert check.available is True
    assert "17.0.1" in check.version


def test_check_java_available_false(monkeypatch):
    monkeypatch.setattr(sg, "check_java_available", _REAL_CHECK_JAVA_AVAILABLE)

    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("java not found")

    monkeypatch.setattr(sg.subprocess, "run", fake_run)
    check = sg.check_java_available()
    assert check.available is False
    assert check.version is None


def test_generate_raises_when_java_missing(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    install_fake_synthea(settings)
    monkeypatch.setattr(sg, "check_java_available", lambda: sg.VersionCheck(False, None))

    generator = sg.SyntheaGenerator(settings=settings)
    with pytest.raises(sg.JavaNotFoundError):
        generator.generate()


def test_get_synthea_commit_hash_returns_none_without_git_dir(tmp_path):
    install_dir = tmp_path / "tools" / "synthea"
    install_dir.mkdir(parents=True)
    assert sg.get_synthea_commit_hash(install_dir) is None


def test_get_synthea_commit_hash_returns_hash(tmp_path, monkeypatch):
    install_dir = tmp_path / "tools" / "synthea"
    (install_dir / ".git").mkdir(parents=True)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="abc123\n", stderr="")

    monkeypatch.setattr(sg.subprocess, "run", fake_run)
    assert sg.get_synthea_commit_hash(install_dir) == "abc123"


# -- Installation validation -------------------------------------------------


def test_missing_synthea_directory_raises(tmp_path):
    settings = make_settings(tmp_path)
    generator = sg.SyntheaGenerator(settings=settings)
    with pytest.raises(sg.SyntheaNotInstalledError):
        generator.generate()


# -- Command construction -----------------------------------------------------


def test_build_command_is_safe_arg_list(tmp_path):
    settings = make_settings(tmp_path, export_csv=True, export_fhir=True)
    install_fake_synthea(settings)
    generator = sg.SyntheaGenerator(settings=settings)
    run_script = settings.install_dir / sg.RUN_SCRIPT_NAME

    command = generator.build_command(run_script, tmp_path / "out")

    assert isinstance(command, list)
    assert all(isinstance(part, str) for part in command)
    assert command[0] == str(run_script)
    assert "-p" in command and "50" in command
    assert "-s" in command and "42" in command
    assert "Massachusetts" in command
    assert "--exporter.csv.export=true" in command
    assert "--exporter.fhir.export=true" in command
    assert any(part.startswith("--exporter.baseDirectory=") for part in command)


# -- Full generation runs -----------------------------------------------------


def test_generate_csv_and_fhir(tmp_path):
    settings = make_settings(tmp_path, export_csv=True, export_fhir=True)
    install_fake_synthea(settings)
    runner = make_fake_runner(create_csv=True, create_fhir=True)
    generator = sg.SyntheaGenerator(settings=settings, careflow_version="0.1.0", subprocess_runner=runner)

    manifest = generator.generate()

    assert (settings.raw_csv_dir / "patients.csv").is_file()
    assert (settings.raw_fhir_dir / "Alice.json").is_file()
    assert settings.manifest_path.is_file()
    assert len(manifest["files"]) == 2
    assert {f["format"] for f in manifest["files"]} == {"csv", "fhir"}
    assert list(settings.temp_output_dir.iterdir()) == []


def test_generate_csv_only(tmp_path):
    settings = make_settings(tmp_path, export_csv=True, export_fhir=False)
    install_fake_synthea(settings)
    runner = make_fake_runner(create_csv=True, create_fhir=False)
    generator = sg.SyntheaGenerator(settings=settings, subprocess_runner=runner)

    manifest = generator.generate()

    assert (settings.raw_csv_dir / "patients.csv").is_file()
    assert not settings.raw_fhir_dir.exists() or list(settings.raw_fhir_dir.iterdir()) == []
    assert all(f["format"] == "csv" for f in manifest["files"])


def test_generate_fhir_only(tmp_path):
    settings = make_settings(tmp_path, export_csv=False, export_fhir=True)
    install_fake_synthea(settings)
    runner = make_fake_runner(create_csv=False, create_fhir=True)
    generator = sg.SyntheaGenerator(settings=settings, subprocess_runner=runner)

    manifest = generator.generate()

    assert (settings.raw_fhir_dir / "Alice.json").is_file()
    assert not settings.raw_csv_dir.exists() or list(settings.raw_csv_dir.iterdir()) == []
    assert all(f["format"] == "fhir" for f in manifest["files"])


# -- Overwrite behavior --------------------------------------------------------


def test_overwrite_conflict_raises_and_skips_generation(tmp_path):
    settings = make_settings(tmp_path, export_csv=True, export_fhir=False, overwrite=False)
    install_fake_synthea(settings)
    settings.raw_csv_dir.mkdir(parents=True, exist_ok=True)
    (settings.raw_csv_dir / "existing.csv").write_text("Id\n1\n")

    calls = []

    def runner(*args, **kwargs):
        calls.append(args)
        raise AssertionError("Synthea should not run when an overwrite conflict exists")

    generator = sg.SyntheaGenerator(settings=settings, subprocess_runner=runner)
    with pytest.raises(sg.OverwriteConflictError):
        generator.generate()

    assert calls == []
    assert (settings.raw_csv_dir / "existing.csv").read_text() == "Id\n1\n"


def test_overwrite_enabled_replaces_existing(tmp_path):
    settings = make_settings(tmp_path, export_csv=True, export_fhir=False, overwrite=True)
    install_fake_synthea(settings)
    settings.raw_csv_dir.mkdir(parents=True, exist_ok=True)
    (settings.raw_csv_dir / "old.csv").write_text("Id\n1\n")

    runner = make_fake_runner(create_csv=True, create_fhir=False)
    generator = sg.SyntheaGenerator(settings=settings, subprocess_runner=runner)

    generator.generate()

    assert (settings.raw_csv_dir / "patients.csv").is_file()
    # Overwrite replaces same-named files; it must never delete unrelated ones.
    assert (settings.raw_csv_dir / "old.csv").is_file()


# -- Failure handling -----------------------------------------------------------


def test_subprocess_failure_raises_and_leaves_no_output(tmp_path):
    settings = make_settings(tmp_path, export_csv=True, export_fhir=True, overwrite=False)
    install_fake_synthea(settings)
    runner = make_fake_runner(create_csv=True, create_fhir=True, returncode=1, stderr="boom")
    generator = sg.SyntheaGenerator(settings=settings, subprocess_runner=runner)

    with pytest.raises(sg.SyntheaExecutionError):
        generator.generate()

    assert not settings.raw_csv_dir.exists() or list(settings.raw_csv_dir.iterdir()) == []
    assert not settings.raw_fhir_dir.exists() or list(settings.raw_fhir_dir.iterdir()) == []
    assert not settings.manifest_path.exists()
    assert list(settings.temp_output_dir.iterdir()) == []


def test_missing_generated_output_raises(tmp_path):
    settings = make_settings(tmp_path, export_csv=True, export_fhir=False)
    install_fake_synthea(settings)
    runner = make_fake_runner(create_csv=False, create_fhir=False, returncode=0)
    generator = sg.SyntheaGenerator(settings=settings, subprocess_runner=runner)

    with pytest.raises(sg.MissingOutputError):
        generator.generate()

    assert not settings.raw_csv_dir.exists() or list(settings.raw_csv_dir.iterdir()) == []
    assert not settings.manifest_path.exists()
    assert list(settings.temp_output_dir.iterdir()) == []


def test_missing_output_for_one_of_two_formats_copies_neither(tmp_path):
    settings = make_settings(tmp_path, export_csv=True, export_fhir=True)
    install_fake_synthea(settings)
    # CSV output produced, FHIR output missing -> nothing should be copied.
    runner = make_fake_runner(create_csv=True, create_fhir=False, returncode=0)
    generator = sg.SyntheaGenerator(settings=settings, subprocess_runner=runner)

    with pytest.raises(sg.MissingOutputError):
        generator.generate()

    assert not settings.raw_csv_dir.exists() or list(settings.raw_csv_dir.iterdir()) == []
    assert not settings.manifest_path.exists()


# -- CSV row counting -----------------------------------------------------------


def test_count_csv_rows_excluding_header(tmp_path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text("Id,Name\n1,Alice\n2,Bob\n3,Carol\n")
    assert sg._count_csv_rows_excluding_header(csv_path) == 3


def test_count_csv_rows_excluding_header_empty_file(tmp_path):
    csv_path = tmp_path / "empty.csv"
    csv_path.write_text("")
    assert sg._count_csv_rows_excluding_header(csv_path) == 0


# -- Checksums --------------------------------------------------------------------


def test_sha256_of_file_matches_hashlib(tmp_path):
    path = tmp_path / "data.txt"
    content = b"careflow synthea checksum test"
    path.write_bytes(content)
    expected = hashlib.sha256(content).hexdigest()
    assert sg._sha256_of_file(path) == expected


# -- Manifest sanitization ---------------------------------------------------------


def test_relative_to_root_for_path_under_root():
    root = sg.get_project_root()
    path = root / "data" / "raw" / "synthea" / "csv" / "patients.csv"
    assert sg._relative_to_root(path) == "data/raw/synthea/csv/patients.csv"


def test_relative_to_root_for_path_outside_root(tmp_path):
    outside = tmp_path / "patients.csv"
    assert sg._relative_to_root(outside) == "<external-path-redacted>"


def test_sanitize_command_args_relativizes_project_paths():
    root = sg.get_project_root()
    abs_path = root / "tools" / "synthea" / sg.RUN_SCRIPT_NAME
    sanitized = sg._sanitize_command_args([str(abs_path), "-p", "50"])
    assert sanitized[0] == "tools/synthea/run_synthea"
    assert "-p" in sanitized and "50" in sanitized


def test_sanitize_command_args_redacts_home_path():
    home = str(Path.home())
    outside_path = f"{home}/some-other-project/secret-data"
    sanitized = sg._sanitize_command_args([outside_path])
    assert home not in sanitized[0]
    assert "<home>" in sanitized[0]


def test_sanitize_command_args_relativizes_keyed_flag():
    root = sg.get_project_root()
    flag = f"--exporter.baseDirectory={root / 'data' / 'tmp' / 'run_1'}"
    sanitized = sg._sanitize_command_args([flag])
    assert sanitized[0] == "--exporter.baseDirectory=data/tmp/run_1"


def test_sanitize_command_args_leaves_non_path_args_untouched():
    sanitized = sg._sanitize_command_args(["-p", "50", "Massachusetts", "--exporter.csv.export=true"])
    assert sanitized == ["-p", "50", "Massachusetts", "--exporter.csv.export=true"]


# -- Manifest contents and write-after-success ----------------------------------------


def test_manifest_contains_required_fields_and_no_leaked_paths(tmp_path):
    settings = make_settings(
        tmp_path, export_csv=True, export_fhir=True, seed=7, population_size=50
    )
    install_fake_synthea(settings)
    runner = make_fake_runner(create_csv=True, create_fhir=True)
    generator = sg.SyntheaGenerator(settings=settings, careflow_version="0.1.0", subprocess_runner=runner)

    manifest = generator.generate()

    assert manifest["request"]["population"] == 50
    assert manifest["request"]["state"] == "Massachusetts"
    assert manifest["request"]["seed"] == 7
    assert manifest["request"]["export_formats"] == {"csv": True, "fhir": True}
    assert manifest["careflow_version"] == "0.1.0"
    assert "generated_at_utc" in manifest
    assert "synthea_commit" in manifest
    assert isinstance(manifest["command"], list)

    home = str(Path.home())
    for arg in manifest["command"]:
        assert home not in arg
        assert "password" not in arg.lower()
        assert "secret" not in arg.lower()

    csv_file = next(f for f in manifest["files"] if f["format"] == "csv")
    assert csv_file["filename"] == "patients.csv"
    assert csv_file["row_count"] == 2
    assert len(csv_file["sha256"]) == 64
    assert csv_file["size_bytes"] > 0
    assert home not in csv_file["destination_path"]

    fhir_file = next(f for f in manifest["files"] if f["format"] == "fhir")
    assert fhir_file["row_count"] is None

    with settings.manifest_path.open() as fh:
        saved = json.load(fh)
    assert saved == manifest


def test_manifest_written_only_after_success(tmp_path):
    settings = make_settings(tmp_path, export_csv=True, export_fhir=False)
    install_fake_synthea(settings)
    runner = make_fake_runner(create_csv=False, create_fhir=False, returncode=1, stderr="boom")
    generator = sg.SyntheaGenerator(settings=settings, subprocess_runner=runner)

    with pytest.raises(sg.SyntheaExecutionError):
        generator.generate()

    assert not settings.manifest_path.exists()
