#!/usr/bin/env python3
"""Set up the Synthea synthetic patient data generator for CareFlow Analytics.

Checks for Git and Java, then clones the official Synthea repository into
the configured installation directory if it is not already present. This
script never modifies or deletes an existing Synthea installation.

Usage:
    python3 scripts/setup_synthea.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from careflow.config import load_config  # noqa: E402
from careflow.data_generation.synthea_generator import (  # noqa: E402
    check_git_available,
    check_java_available,
)
from careflow.logging_config import get_logger  # noqa: E402

logger = get_logger(__name__)


def main() -> int:
    settings = load_config().synthea

    git_check = check_git_available()
    if not git_check.available:
        logger.error("Git was not found on PATH. Install Git and re-run this script.")
        return 1
    logger.info("Detected Git: %s", git_check.version)

    java_check = check_java_available()
    if not java_check.available:
        logger.error(
            "Java was not found on PATH. Install a JDK (11+) and re-run this script."
        )
        return 1
    logger.info("Detected Java: %s", java_check.version)

    install_dir = settings.install_dir
    if install_dir.is_dir() and any(install_dir.iterdir()):
        logger.info(
            "Synthea already installed at %s; leaving the existing installation untouched.",
            install_dir,
        )
        return 0

    install_dir.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Cloning Synthea from %s into %s", settings.repository_url, install_dir)

    try:
        result = subprocess.run(
            ["git", "clone", settings.repository_url, str(install_dir)],
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        logger.error("Failed to run 'git clone': %s", exc)
        return 1

    if result.returncode != 0:
        logger.error("git clone failed: %s", (result.stderr or "").strip())
        return 1

    logger.info("Synthea installed successfully at %s", install_dir)
    logger.info(
        "First generation run will take longer while Synthea builds itself via Gradle."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
