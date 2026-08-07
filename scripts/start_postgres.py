#!/usr/bin/env python3
"""Start the CareFlow PostgreSQL warehouse container via Docker Compose.

Runs `docker compose up -d postgres` and waits for its health check to
report healthy before returning. Requires Docker to be installed and the
daemon running; POSTGRES_PASSWORD must be set (via .env or the shell
environment) or Compose refuses to start the container.

Falls back to a connectivity-only readiness check when the Docker CLI
can't reach a daemon at all -- e.g. running *inside* the Phase 4A
Airflow container, which deliberately has no Docker socket mounted (the
CareFlow container's lifecycle is a host-level concern, started once by
scripts/start_airflow.py before Airflow itself comes up; a DAG task
running inside that container only needs to confirm PostgreSQL is
reachable, not manage the container).

Usage:
    PYTHONPATH=src python3 scripts/start_postgres.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from careflow.config import get_project_root  # noqa: E402
from careflow.logging_config import get_logger  # noqa: E402
from careflow.warehouse.postgres_client import check_connectivity  # noqa: E402

logger = get_logger(__name__)

CONTAINER_NAME = "careflow-postgres"
HEALTH_TIMEOUT_SECONDS = 60
HEALTH_POLL_INTERVAL_SECONDS = 2


def _docker_available() -> bool:
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=10, check=True)
        return True
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def _wait_for_connectivity_only() -> int:
    """Poll PostgreSQL directly (no Docker access) until it accepts connections."""
    logger.info("Docker daemon not reachable from here -- checking PostgreSQL connectivity directly instead.")
    deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        ok, reason = check_connectivity()
        if ok:
            logger.info("PostgreSQL is reachable and ready.")
            return 0
        time.sleep(HEALTH_POLL_INTERVAL_SECONDS)
    logger.error("PostgreSQL was not reachable within %d seconds: %s", HEALTH_TIMEOUT_SECONDS, reason)
    return 1


def main() -> int:
    if not _docker_available():
        return _wait_for_connectivity_only()

    root = get_project_root()
    try:
        result = subprocess.run(
            ["docker", "compose", "up", "-d", "postgres"],
            cwd=str(root), capture_output=True, text=True,
        )
    except OSError as exc:
        logger.error("Failed to run 'docker compose up': %s", exc)
        return 1

    if result.returncode != 0:
        logger.error("docker compose up failed: %s", (result.stderr or "").strip())
        return 1

    logger.info("Waiting for PostgreSQL health check...")
    deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        health = subprocess.run(
            ["docker", "inspect", f"--format={{{{.State.Health.Status}}}}", CONTAINER_NAME],
            capture_output=True, text=True,
        )
        status = health.stdout.strip()
        if status == "healthy":
            logger.info("PostgreSQL is healthy and ready.")
            return 0
        time.sleep(HEALTH_POLL_INTERVAL_SECONDS)

    logger.error("PostgreSQL did not become healthy within %d seconds.", HEALTH_TIMEOUT_SECONDS)
    return 1


if __name__ == "__main__":
    sys.exit(main())
