#!/usr/bin/env python3
"""Start the CareFlow Airflow stack (Docker Compose, `airflow` profile).

Brings up the CareFlow analytics PostgreSQL (unchanged, default
profile), then the Airflow metadata PostgreSQL, one-shot `airflow-init`
(DB migration + admin user), the scheduler, and the webserver -- all
under the `airflow` Compose profile, so a plain `docker compose up -d`
elsewhere in the project is completely unaffected.

Usage:
    PYTHONPATH=src python3 scripts/start_airflow.py
    PYTHONPATH=src python3 scripts/start_airflow.py --skip-careflow-postgres
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from careflow.config import get_project_root  # noqa: E402
from careflow.logging_config import get_logger  # noqa: E402

logger = get_logger(__name__)

# 8080 is Airflow's own image default, but is frequently already taken by
# another local project's Airflow -- AIRFLOW_WEBSERVER_PORT (.env) is the
# single source of truth, matching the port docker-compose.yml maps.
WEBSERVER_PORT = os.environ.get("AIRFLOW_WEBSERVER_PORT", "8080")
WEBSERVER_HEALTH_URL = f"http://localhost:{WEBSERVER_PORT}/health"
WEBSERVER_TIMEOUT_SECONDS = 180
WEBSERVER_POLL_INTERVAL_SECONDS = 5


def _docker_available() -> bool:
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=10, check=True)
        return True
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess:
    logger.info("Running: %s", " ".join(argv))
    return subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-careflow-postgres", action="store_true",
        help="Skip starting the careflow-postgres service (assume it's already running)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not _docker_available():
        logger.error("Docker is not installed, or the Docker daemon is not running.")
        return 1

    root = get_project_root()

    if not args.skip_careflow_postgres:
        result = _run(["docker", "compose", "up", "-d", "postgres"], cwd=root)
        if result.returncode != 0:
            logger.error("Failed to start careflow-postgres: %s", (result.stderr or "").strip())
            return 1
        logger.info("careflow-postgres is up.")

    init_result = _run(["docker", "compose", "--profile", "airflow", "up", "airflow-init"], cwd=root)
    if init_result.returncode != 0:
        logger.error("airflow-init failed: %s", (init_result.stderr or "").strip())
        return 1
    logger.info("airflow-init completed (metadata DB migrated, admin user ensured).")

    up_result = _run(
        ["docker", "compose", "--profile", "airflow", "up", "-d", "airflow-scheduler", "airflow-webserver"],
        cwd=root,
    )
    if up_result.returncode != 0:
        logger.error("Failed to start Airflow scheduler/webserver: %s", (up_result.stderr or "").strip())
        return 1

    logger.info("Waiting for the Airflow webserver health check...")
    deadline = time.monotonic() + WEBSERVER_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(WEBSERVER_HEALTH_URL, timeout=5) as response:
                if response.status == 200:
                    logger.info("Airflow webserver is healthy: %s", WEBSERVER_HEALTH_URL)
                    logger.info("UI: http://localhost:%s (see AIRFLOW_ADMIN_USERNAME/PASSWORD in .env)", WEBSERVER_PORT)
                    return 0
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(WEBSERVER_POLL_INTERVAL_SECONDS)

    logger.error("Airflow webserver did not become healthy within %d seconds.", WEBSERVER_TIMEOUT_SECONDS)
    return 1


if __name__ == "__main__":
    sys.exit(main())
