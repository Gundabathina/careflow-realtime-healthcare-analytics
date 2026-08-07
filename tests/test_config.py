"""Tests for careflow.config and careflow.logging_config."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml

from careflow import config as config_module
from careflow import logging_config as logging_config_module
from careflow.config import Config, ConfigError, get_project_root, load_config


@pytest.fixture(autouse=True)
def _clear_config_cache():
    """Ensure each test starts with a clean lru_cache and logging state."""
    config_module.load_config.cache_clear()
    logging_config_module.reset_logging_state()
    yield
    config_module.load_config.cache_clear()
    logging_config_module.reset_logging_state()


def test_get_project_root_points_at_repo_root():
    root = get_project_root()
    assert root.is_dir()
    assert (root / "config" / "project_config.yaml").is_file()
    assert (root / "src" / "careflow").is_dir()


def test_load_config_returns_config_instance():
    cfg = load_config()
    assert isinstance(cfg, Config)


def test_project_metadata_fields():
    cfg = load_config()
    assert cfg.project_name == "CareFlow Analytics"
    assert cfg.project_phase == "2A"
    assert cfg.project_version


def test_get_path_resolves_relative_to_project_root():
    cfg = load_config()
    data_path = cfg.get_path("data", "raw")
    assert data_path == get_project_root() / "data" / "raw"


def test_get_with_default_for_missing_key():
    cfg = load_config()
    assert cfg.get("nonexistent", "key", default="fallback") == "fallback"
    assert cfg.get("project", "name") == "CareFlow Analytics"


def test_load_config_is_cached():
    cfg1 = load_config()
    cfg2 = load_config()
    assert cfg1 is cfg2


def test_load_config_missing_file_raises(tmp_path: Path):
    missing = tmp_path / "does_not_exist.yaml"
    with pytest.raises(ConfigError):
        load_config(str(missing))


def test_load_config_empty_file_raises(tmp_path: Path):
    empty_file = tmp_path / "empty.yaml"
    empty_file.write_text("", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(str(empty_file))


def test_load_config_non_mapping_raises(tmp_path: Path):
    bad_file = tmp_path / "list.yaml"
    bad_file.write_text("- one\n- two\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(str(bad_file))


def test_missing_required_key_raises_config_error(tmp_path: Path):
    partial_file = tmp_path / "partial.yaml"
    partial_file.write_text(yaml.safe_dump({"project": {}}), encoding="utf-8")
    cfg = load_config(str(partial_file))
    with pytest.raises(ConfigError):
        _ = cfg.project_name


def test_logging_config_yaml_is_valid():
    logging_yaml_path = get_project_root() / "config" / "logging.yaml"
    assert logging_yaml_path.is_file()
    with logging_yaml_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert data["version"] == 1
    assert "handlers" in data
    assert "loggers" in data


def test_setup_logging_configures_root_and_careflow_loggers():
    logging_config_module.setup_logging(force=True)
    careflow_logger = logging.getLogger("careflow")
    assert careflow_logger.level == logging.DEBUG


def test_get_logger_returns_logger_instance():
    logger = logging_config_module.get_logger("careflow.tests")
    assert isinstance(logger, logging.Logger)
    assert logger.name == "careflow.tests"
