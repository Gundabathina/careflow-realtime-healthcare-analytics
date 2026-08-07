#!/usr/bin/env python3
"""Trigger a CareFlow Airflow DAG run via `docker compose exec`.

Every parameter is parsed and validated (booleans, a bounded integer for
--population) before being serialized to JSON and passed to `airflow
dags trigger --conf` -- never built by interpolating raw strings into a
shell command (subprocess is always called with an argument list).

Usage:
    PYTHONPATH=src python3 scripts/trigger_careflow_dag.py
    PYTHONPATH=src python3 scripts/trigger_careflow_dag.py --dag-id careflow_daily_analytics
    PYTHONPATH=src python3 scripts/trigger_careflow_dag.py --generate-data --population 200
    PYTHONPATH=src python3 scripts/trigger_careflow_dag.py --force-silver --force-gold --force-warehouse
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "airflow" / "plugins"))

from careflow.config import get_project_root  # noqa: E402
from careflow.logging_config import get_logger  # noqa: E402
from careflow_operators import validate_dag_run_params  # noqa: E402

logger = get_logger(__name__)

ALLOWED_DAG_IDS = ("careflow_end_to_end", "careflow_daily_analytics")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dag-id", default="careflow_end_to_end", choices=ALLOWED_DAG_IDS)
    parser.add_argument("--generate-data", action="store_true")
    parser.add_argument("--population", type=int, default=None)
    parser.add_argument("--force-bronze", action="store_true")
    parser.add_argument("--force-silver", action="store_true")
    parser.add_argument("--force-gold", action="store_true")
    parser.add_argument("--force-warehouse", action="store_true")
    parser.add_argument("--run-dbt-snapshot", action="store_true")
    parser.add_argument("--run-dbt-docs", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument(
        "--webserver-service", default="airflow-webserver",
        help="Compose service name to exec into (default: airflow-webserver)",
    )
    return parser.parse_args(argv)


def build_conf(args: argparse.Namespace) -> dict:
    raw = {
        "generate_data": args.generate_data,
        "population": args.population,
        "force_bronze": args.force_bronze,
        "force_silver": args.force_silver,
        "force_gold": args.force_gold,
        "force_warehouse": args.force_warehouse,
        "run_dbt_snapshot": args.run_dbt_snapshot,
        "run_dbt_docs": args.run_dbt_docs,
        "fail_fast": args.fail_fast,
    }
    return validate_dag_run_params(raw)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        conf = build_conf(args)
    except ValueError as exc:
        logger.error("Invalid DAG run parameters: %s", exc)
        return 1

    root = get_project_root()
    conf_json = json.dumps(conf)
    cmd = [
        "docker", "compose", "exec", "-T", args.webserver_service,
        "airflow", "dags", "trigger", args.dag_id, "--conf", conf_json,
    ]
    logger.info("Triggering %s with conf=%s", args.dag_id, conf)
    result = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True)

    if result.stdout.strip():
        logger.info(result.stdout.strip())
    if result.stderr.strip():
        logger.warning(result.stderr.strip())

    if result.returncode != 0:
        logger.error("Failed to trigger DAG '%s' (exit code %d)", args.dag_id, result.returncode)
        return 1

    logger.info("Triggered '%s' successfully.", args.dag_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
