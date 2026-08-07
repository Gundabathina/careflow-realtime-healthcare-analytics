#!/usr/bin/env python3
"""Report the status of the most recent CareFlow Airflow DAG run(s).

Shells out to `airflow dags list-runs` inside the webserver container
(argument list, never shell=True) and prints a compact summary. Exits
non-zero if the most recent run for the DAG failed or hasn't finished.

Usage:
    PYTHONPATH=src python3 scripts/check_pipeline_status.py
    PYTHONPATH=src python3 scripts/check_pipeline_status.py --dag-id careflow_daily_analytics
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from careflow.config import get_project_root  # noqa: E402
from careflow.logging_config import get_logger  # noqa: E402

logger = get_logger(__name__)

ALLOWED_DAG_IDS = ("careflow_end_to_end", "careflow_daily_analytics")
TERMINAL_STATES = ("success", "failed")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dag-id", default="careflow_end_to_end", choices=ALLOWED_DAG_IDS)
    parser.add_argument(
        "--webserver-service", default="airflow-webserver",
        help="Compose service name to exec into (default: airflow-webserver)",
    )
    return parser.parse_args(argv)


def fetch_dag_runs(dag_id: str, webserver_service: str, root: Path) -> list[dict]:
    cmd = [
        "docker", "compose", "exec", "-T", webserver_service,
        "airflow", "dags", "list-runs", "-d", dag_id, "-o", "json",
    ]
    result = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "unknown error").strip())
    return json.loads(result.stdout)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = get_project_root()

    try:
        runs = fetch_dag_runs(args.dag_id, args.webserver_service, root)
    except (RuntimeError, json.JSONDecodeError) as exc:
        logger.error("Could not fetch DAG runs for '%s': %s", args.dag_id, exc)
        return 1

    if not runs:
        logger.info("No runs found yet for DAG '%s'.", args.dag_id)
        return 1

    latest = runs[0]
    state = latest.get("state")
    run_id = latest.get("run_id") or latest.get("dag_run_id")
    logger.info("Latest run of '%s': run_id=%s state=%s", args.dag_id, run_id, state)
    logger.info("Total tracked runs: %d", len(runs))

    if state == "success":
        return 0
    if state == "failed":
        logger.error("Latest run of '%s' failed.", args.dag_id)
        return 1
    logger.warning("Latest run of '%s' is still '%s' (not yet terminal).", args.dag_id, state)
    return 1


if __name__ == "__main__":
    sys.exit(main())
