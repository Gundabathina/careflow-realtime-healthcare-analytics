#!/usr/bin/env python3
"""Validate the loaded PostgreSQL warehouse against Gold's own outputs.

Every check compares live PostgreSQL state to Gold Parquet files or
reports/profiling/gold_kpi_summary.json -- Gold remains the source of
truth. Read-only against the warehouse and all upstream layers.

Usage:
    PYTHONPATH=src python3 scripts/validate_postgres_warehouse.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from careflow.config import load_config  # noqa: E402
from careflow.logging_config import get_logger  # noqa: E402
from careflow.warehouse.postgres_client import MissingCredentialsError, WarehouseConnectionError  # noqa: E402
from careflow.warehouse.warehouse_validator import (  # noqa: E402
    run_validation,
    write_orphan_summary_csv,
    write_validation_report_json,
)

logger = get_logger(__name__)


def main() -> int:
    config = load_config()
    gold_dir = config.get_path("data", "gold")
    profiling_reports_dir = config.get_path("reports_profiling")
    warehouse_reports_dir = config.get_path("reports") / "warehouse"

    try:
        report, orphan_rows = run_validation(gold_dir=gold_dir, reports_dir=profiling_reports_dir)
    except MissingCredentialsError as exc:
        logger.error("PostgreSQL credentials not configured: %s", exc)
        return 1
    except WarehouseConnectionError as exc:
        logger.error("Could not connect to PostgreSQL: %s", exc)
        return 1

    write_validation_report_json(report, warehouse_reports_dir / "postgres_validation_report.json")
    write_orphan_summary_csv(orphan_rows, warehouse_reports_dir / "postgres_orphan_summary.csv")

    summary = report["summary"]
    logger.info(
        "Warehouse validation complete: %d check(s) (pass=%d, warning=%d, fail=%d, skipped=%d)",
        summary["total_checks"], summary["passed"], summary["warnings"], summary["failed"], summary["skipped"],
    )
    logger.info("Validation report: %s", warehouse_reports_dir / "postgres_validation_report.json")
    logger.info("Orphan summary: %s", warehouse_reports_dir / "postgres_orphan_summary.csv")

    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
