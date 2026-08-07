#!/usr/bin/env python3
"""Load the Gold star schema into the CareFlow PostgreSQL warehouse.

Transactional and incremental: each table is staged, row-count validated,
then atomically swapped into place; a table is skipped when its Gold
checksum is unchanged since the last successful load. Reads only from
data/gold/; writes only to PostgreSQL.

Usage:
    PYTHONPATH=src python3 scripts/load_postgres_warehouse.py
    PYTHONPATH=src python3 scripts/load_postgres_warehouse.py --force
    PYTHONPATH=src python3 scripts/load_postgres_warehouse.py --table dim_patient --table fact_encounter
    PYTHONPATH=src python3 scripts/load_postgres_warehouse.py --schema-only
    PYTHONPATH=src python3 scripts/load_postgres_warehouse.py --truncate-and-load
    PYTHONPATH=src python3 scripts/load_postgres_warehouse.py --fail-fast
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from careflow.config import load_config  # noqa: E402
from careflow.logging_config import get_logger  # noqa: E402
from careflow.warehouse.gold_loader import (  # noqa: E402
    GoldManifestNotFoundError,
    run_gold_load,
    write_load_report_json,
    write_table_counts_csv,
)
from careflow.warehouse.postgres_client import MissingCredentialsError, WarehouseConnectionError  # noqa: E402

logger = get_logger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Reprocess every selected table even if unchanged")
    parser.add_argument("--table", action="append", dest="tables", default=None, help="Restrict to this table (repeatable)")
    parser.add_argument("--schema-only", action="store_true", help="Only create/update schema, indexes, and views")
    parser.add_argument("--truncate-and-load", action="store_true", help="Force a full truncate-and-reload of every selected table")
    parser.add_argument("--fail-fast", action="store_true", help="Stop the run immediately after the first table failure")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config()
    gold_dir = config.get_path("data", "gold")
    reports_dir = config.get_path("reports") / "warehouse"

    try:
        report = run_gold_load(
            gold_dir=gold_dir,
            tables=args.tables,
            force=args.force,
            schema_only=args.schema_only,
            truncate_and_load=args.truncate_and_load,
            fail_fast=args.fail_fast,
        )
    except MissingCredentialsError as exc:
        logger.error("PostgreSQL credentials not configured: %s", exc)
        return 1
    except WarehouseConnectionError as exc:
        logger.error("Could not connect to PostgreSQL: %s", exc)
        return 1
    except GoldManifestNotFoundError as exc:
        logger.error(str(exc))
        return 1

    write_load_report_json(report, reports_dir / "postgres_load_report.json")
    write_table_counts_csv(report, reports_dir / "postgres_table_counts.csv")

    summary = report["summary"]
    logger.info(
        "Warehouse load complete: %d table(s) (processed=%d, skipped=%d, failed=%d)",
        summary["total_tables"], summary["processed"], summary["skipped"], summary["failed"],
    )
    for entry in report["tables"]:
        if entry["status"] == "failed":
            logger.warning("Table '%s' failed: %s", entry["table_name"], entry["error_message"])

    logger.info("Load report: %s", reports_dir / "postgres_load_report.json")
    logger.info("Table counts: %s", reports_dir / "postgres_table_counts.csv")

    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
