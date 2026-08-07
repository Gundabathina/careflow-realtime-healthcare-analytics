"""Configuration loading utilities for CareFlow Analytics."""

from __future__ import annotations

import functools
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROJECT_CONFIG_ENV_VAR = "CAREFLOW_PROJECT_CONFIG"
DEFAULT_CONFIG_FILENAME = "project_config.yaml"


class ConfigError(Exception):
    """Raised when the project configuration cannot be loaded or is invalid."""


@dataclass(frozen=True)
class SyntheaSettings:
    """Resolved settings for Synthea installation and synthetic data generation."""

    population_size: int
    state: str
    seed: int | None
    export_csv: bool
    export_fhir: bool
    overwrite: bool
    repository_url: str
    install_dir: Path
    temp_output_dir: Path
    manifest_path: Path
    raw_csv_dir: Path
    raw_fhir_dir: Path


def get_project_root() -> Path:
    """Return the absolute path to the project root directory.

    Resolved as the parent of the ``src`` directory that contains this
    package (``.../careflow-realtime-healthcare-analytics``).
    """
    return Path(__file__).resolve().parents[2]


def get_config_path() -> Path:
    """Return the path to the project configuration file.

    Honors the ``CAREFLOW_PROJECT_CONFIG`` environment variable if set,
    otherwise defaults to ``config/project_config.yaml`` under the
    project root.
    """
    override = os.environ.get(PROJECT_CONFIG_ENV_VAR)
    if override:
        return Path(override).resolve()
    return get_project_root() / "config" / DEFAULT_CONFIG_FILENAME


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if data is None:
        raise ConfigError(f"Configuration file is empty: {path}")
    if not isinstance(data, dict):
        raise ConfigError(
            f"Configuration file must contain a mapping at the top level: {path}"
        )
    return data


class Config:
    """Typed accessor around the CareFlow project configuration."""

    def __init__(self, data: dict[str, Any], config_path: Path):
        self._data = data
        self._config_path = config_path

    @property
    def raw(self) -> dict[str, Any]:
        return self._data

    @property
    def config_path(self) -> Path:
        return self._config_path

    @property
    def project_name(self) -> str:
        return self._get_required("project", "name")

    @property
    def project_phase(self) -> str:
        return self._get_required("project", "phase")

    @property
    def project_version(self) -> str:
        return self._get_required("project", "version")

    @property
    def logging_config_path(self) -> Path:
        relative = self._get_required("logging", "config_path")
        return get_project_root() / relative

    def get_path(self, *keys: str) -> Path:
        """Resolve a configured path (relative to project root) by key sequence.

        Example: ``config.get_path("data", "raw")``
        """
        relative = self._get_required("paths", *keys)
        return get_project_root() / relative

    @property
    def synthea(self) -> SyntheaSettings:
        """Resolved Synthea installation and generation settings."""
        node = self._get_required("data_generation", "synthea")
        root = get_project_root()
        export_formats = node.get("export_formats", {})

        def _resolve(key: str, default: str) -> Path:
            return root / node.get(key, default)

        return SyntheaSettings(
            population_size=int(node.get("population_size", 100)),
            state=str(node.get("state", "Massachusetts")),
            seed=node.get("seed"),
            export_csv=bool(export_formats.get("csv", True)),
            export_fhir=bool(export_formats.get("fhir", False)),
            overwrite=bool(node.get("overwrite", False)),
            repository_url=str(
                node.get(
                    "repository_url",
                    "https://github.com/synthetichealth/synthea.git",
                )
            ),
            install_dir=_resolve("install_dir", "tools/synthea"),
            temp_output_dir=_resolve("temp_output_dir", "data/tmp/synthea_generation"),
            manifest_path=_resolve(
                "manifest_path", "data/raw/synthea/generation_manifest.json"
            ),
            raw_csv_dir=self.get_path("data", "raw_synthea_csv"),
            raw_fhir_dir=self.get_path("data", "raw_synthea_fhir"),
        )

    def get(self, *keys: str, default: Any = None) -> Any:
        """Look up a nested key sequence, returning ``default`` if missing."""
        node: Any = self._data
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def _get_required(self, *keys: str) -> Any:
        node: Any = self._data
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                raise ConfigError(f"Missing required configuration key: {'.'.join(keys)}")
            node = node[key]
        return node


@functools.lru_cache(maxsize=None)
def load_config(config_path: str | None = None) -> Config:
    """Load and cache the project configuration.

    Parameters
    ----------
    config_path:
        Optional explicit path to a configuration file. When omitted,
        the default project configuration path is used.
    """
    path = Path(config_path).resolve() if config_path else get_config_path()
    data = _load_yaml(path)
    return Config(data=data, config_path=path)


def reset_config_cache() -> None:
    """Clear the cached configuration. Primarily useful for tests."""
    load_config.cache_clear()
