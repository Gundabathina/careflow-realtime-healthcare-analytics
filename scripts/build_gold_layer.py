#!/usr/bin/env python3
"""Build the Gold star schema and analytics marts from Silver Parquet datasets.

Incremental by default: each table's dependency signature combines the
Bronze->Silver checksums of every Silver dataset it transitively depends
on. Unchanged -> skipped; changed, or --force, -> rebuilt. Rebuilding a
dimension automatically triggers rebuilding every fact/mart that depends
on it. Reads only from data/silver/; writes only to data/gold/ and
reports/profiling/. Never modifies data/raw, data/bronze, or data/silver.

Usage:
    PYTHONPATH=src python3 scripts/build_gold_layer.py
    PYTHONPATH=src python3 scripts/build_gold_layer.py --force
    PYTHONPATH=src python3 scripts/build_gold_layer.py --table dim_patient --table fact_encounter
    PYTHONPATH=src python3 scripts/build_gold_layer.py --mart mart_readmission
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from careflow.config import load_config  # noqa: E402
from careflow.gold.gold_builder import (  # noqa: E402
    GOLD_KPI_SUMMARY_FILENAME,
    GOLD_MANIFEST_FILENAME,
    GOLD_QUALITY_REPORT_FILENAME,
    GOLD_QUALITY_SUMMARY_FILENAME,
    run_gold_pipeline,
)
from careflow.logging_config import get_logger  # noqa: E402

logger = get_logger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force", action="store_true",
        help="Reprocess every selected table/mart even if its dependency signature is unchanged",
    )
    parser.add_argument(
        "--table", action="append", dest="tables", default=None,
        help="Restrict the run to this dimension/fact table (repeatable). Omit for a full run.",
    )
    parser.add_argument(
        "--mart", action="append", dest="marts", default=None,
        help="Restrict the run to this analytics mart (repeatable). Combines with --table.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config()
    silver_dir = config.get_path("data", "silver")
    gold_dir = config.get_path("data", "gold")
    reports_dir = config.get_path("reports_profiling")

    if not (silver_dir / "silver_manifest.json").is_file():
        logger.error(
            "Silver manifest not found at %s. Run scripts/build_silver_layer.py first.",
            silver_dir / "silver_manifest.json",
        )
        return 1

    manifest, quality_report, kpi_summary = run_gold_pipeline(
        silver_dir=silver_dir,
        gold_dir=gold_dir,
        reports_dir=reports_dir,
        tables=args.tables,
        marts=args.marts,
        force=args.force,
    )

    summary = manifest["summary"]
    logger.info(
        "Gold build complete: %d table(s) (processed=%d, skipped=%d, failed=%d)",
        summary["total_tables"], summary["processed"], summary["skipped"], summary["failed"],
    )
    for entry in manifest["tables"]:
        if entry["status"] == "failed":
            logger.warning("Gold table '%s' failed: %s", entry["table"], entry["error"])

    quality_summary = quality_report["summary"]
    logger.info(
        "Gold quality checks: %d (pass=%d, warning=%d, fail=%d, skipped=%d)",
        quality_summary["total_checks"], quality_summary["passed"],
        quality_summary["warnings"], quality_summary["failed"], quality_summary["skipped"],
    )

    if kpi_summary.get("kpis"):
        logger.info("Gold KPIs computed: %d", len(kpi_summary["kpis"]))
    else:
        logger.warning("Gold KPI summary skipped: %s", kpi_summary.get("skipped_reason"))

    logger.info("Gold manifest: %s", gold_dir / GOLD_MANIFEST_FILENAME)
    logger.info("Gold quality report: %s", reports_dir / GOLD_QUALITY_REPORT_FILENAME)
    logger.info("Gold quality summary: %s", reports_dir / GOLD_QUALITY_SUMMARY_FILENAME)
    logger.info("Gold KPI summary: %s", reports_dir / GOLD_KPI_SUMMARY_FILENAME)

    return 0


if __name__ == "__main__":
    sys.exit(main())
