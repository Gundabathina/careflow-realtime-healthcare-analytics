"""Logging configuration utilities for CareFlow Analytics."""

from __future__ import annotations

import logging
import logging.config
from pathlib import Path
from typing import Any

import yaml

from careflow.config import get_project_root

DEFAULT_LOGGING_CONFIG_RELATIVE_PATH = "config/logging.yaml"

_configured = False


def _load_logging_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Logging configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Logging configuration must be a mapping: {path}")
    return data


def _ensure_handler_directories(config: dict[str, Any]) -> None:
    root = get_project_root()
    for handler in config.get("handlers", {}).values():
        filename = handler.get("filename")
        if not filename:
            continue
        log_path = Path(filename)
        if not log_path.is_absolute():
            log_path = root / log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)


def setup_logging(
    config_path: str | Path | None = None,
    default_level: int = logging.INFO,
    force: bool = False,
) -> None:
    """Configure application logging from the project's logging.yaml.

    Safe to call multiple times; subsequent calls are no-ops unless
    ``force`` is True. Falls back to ``logging.basicConfig`` if the
    configuration file is missing or invalid.
    """
    global _configured
    if _configured and not force:
        return

    path = (
        Path(config_path).resolve()
        if config_path
        else get_project_root() / DEFAULT_LOGGING_CONFIG_RELATIVE_PATH
    )

    try:
        config = _load_logging_yaml(path)
        _ensure_handler_directories(config)
        logging.config.dictConfig(config)
    except (FileNotFoundError, ValueError, KeyError, TypeError) as exc:
        logging.basicConfig(
            level=default_level,
            format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        )
        logging.getLogger(__name__).warning(
            "Falling back to basic logging configuration: %s", exc
        )

    _configured = True


def reset_logging_state() -> None:
    """Reset the internal 'configured' flag. Primarily useful for tests."""
    global _configured
    _configured = False


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger, initializing logging on first use."""
    if not _configured:
        setup_logging()
    return logging.getLogger(name)
