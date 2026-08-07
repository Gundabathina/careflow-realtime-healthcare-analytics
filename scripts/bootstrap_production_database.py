#!/usr/bin/env python3
"""Bootstrap a fresh production/cloud PostgreSQL database (e.g. Render).

Reuses the exact same pipeline components a local load uses -- no
second pipeline exists for "production":

    1. careflow.warehouse.postgres_client.load_connection_config()
       -- verifies POSTGRES_* env vars, never guesses/hard-codes them.
    2. careflow.warehouse.gold_loader.run_gold_load()
       -- the same function scripts/load_postgres_warehouse.py calls;
          creates schema/indexes/views (ensure_schema) and loads every
          Gold table transactionally. Checksum-based: re-running this
          script against an already-bootstrapped, unchanged database is
          a fast no-op, not a destructive reload.
    3. careflow.warehouse.warehouse_validator.run_validation()
       -- the same function scripts/validate_postgres_warehouse.py
          calls; compares the loaded warehouse back to Gold's own
          output.

Intended to be run manually, once, pointed at a fresh managed database
(e.g. via the POSTGRES_* values Render shows for a provisioned
database) -- never wired into a web service's start command, so a
service restart never re-triggers a warehouse reload.

Usage:
    export POSTGRES_HOST=... POSTGRES_PORT=... POSTGRES_DB=... \\
           POSTGRES_USER=... POSTGRES_PASSWORD=... POSTGRES_SSLMODE=require
    PYTHONPATH=src python3 scripts/bootstrap_production_database.py

    # Force a full reload even if checksums are unchanged:
    PYTHONPATH=src python3 scripts/bootstrap_production_database.py --force
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
from careflow.warehouse.postgres_client import (  # noqa: E402
    MissingCredentialsError,
    WarehouseConnectionError,
    load_connection_config,
)
from careflow.warehouse.warehouse_validator import (  # noqa: E402
    run_validation,
    write_orphan_summary_csv,
    write_validation_report_json,
)

logger = get_logger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--force", action="store_true",
        help="Reload every table even if its Gold checksum is unchanged (default: incremental, idempotent).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # 1. Verify required environment variables up front -- never a
    #    guessable default, and the error never includes a credential
    #    value (see MissingCredentialsError's message construction).
    try:
        config = load_connection_config()
    except MissingCredentialsError as exc:
        logger.error("Cannot bootstrap: %s", exc)
        return 1

    if config.sslmode == "prefer" and config.host not in ("localhost", "127.0.0.1", "postgres"):
        logger.warning(
            "POSTGRES_SSLMODE is unset (defaulting to 'prefer') while POSTGRES_HOST looks like a "
            "non-local host (%s). Managed PostgreSQL providers typically require SSL -- set "
            "POSTGRES_SSLMODE=require if the connection below fails.",
            config.host,
        )
    logger.info("Bootstrapping warehouse at %s (sslmode=%s).", config.safe_repr(), config.sslmode)

    project_config = load_config()
    gold_dir = project_config.get_path("data", "gold")
    warehouse_reports_dir = project_config.get_path("reports") / "warehouse"
    profiling_reports_dir = project_config.get_path("reports_profiling")

    # 2. Schema + load -- the exact function scripts/load_postgres_warehouse.py
    #    uses. ensure_schema() runs automatically as part of this, so a
    #    brand-new empty database gets its schema created here; no
    #    separate --schema-only step is required first.
    try:
        load_report = run_gold_load(gold_dir=gold_dir, force=args.force, config=config)
    except MissingCredentialsError as exc:
        logger.error("PostgreSQL credentials not configured: %s", exc)
        return 1
    except WarehouseConnectionError as exc:
        logger.error("Could not connect to PostgreSQL: %s", exc)
        return 1
    except GoldManifestNotFoundError as exc:
        logger.error(
            "%s -- run the local pipeline through Gold first (see "
            "README.md#15-running-the-pipeline); this script loads existing Gold "
            "output, it does not generate new synthetic data.",
            exc,
        )
        return 1

    write_load_report_json(load_report, warehouse_reports_dir / "postgres_load_report.json")
    write_table_counts_csv(load_report, warehouse_reports_dir / "postgres_table_counts.csv")

    load_summary = load_report["summary"]
    logger.info(
        "Load complete: %d table(s) (processed=%d, skipped=%d, failed=%d).",
        load_summary["total_tables"], load_summary["processed"], load_summary["skipped"], load_summary["failed"],
    )
    if load_summary["failed"]:
        for entry in load_report["tables"]:
            if entry["status"] == "failed":
                logger.error("Table '%s' failed: %s", entry["table_name"], entry["error_message"])
        return 1

    # 3. Validate -- the exact function scripts/validate_postgres_warehouse.py
    #    uses, comparing the now-loaded warehouse back to Gold's own output.
    try:
        validation_report, orphan_rows = run_validation(gold_dir=gold_dir, reports_dir=profiling_reports_dir, config=config)
    except WarehouseConnectionError as exc:
        logger.error("Could not connect to PostgreSQL for validation: %s", exc)
        return 1

    write_validation_report_json(validation_report, warehouse_reports_dir / "postgres_validation_report.json")
    write_orphan_summary_csv(orphan_rows, warehouse_reports_dir / "postgres_orphan_summary.csv")

    val_summary = validation_report["summary"]
    logger.info(
        "Validation complete: %d check(s) (pass=%d, warning=%d, fail=%d, skipped=%d).",
        val_summary["total_checks"], val_summary["passed"], val_summary["warnings"],
        val_summary["failed"], val_summary["skipped"],
    )

    if val_summary["failed"]:
        logger.error("Production database bootstrap completed with validation failures -- see report above.")
        return 1

    logger.info("Production database bootstrap succeeded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
