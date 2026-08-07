#!/usr/bin/env python3
"""Build the Silver layer from validated Bronze Parquet datasets.

Incremental by default: a dataset is skipped when its Bronze source
checksum has not changed since the last successful Silver run. Reads
only from data/bronze/; writes only to data/silver/ and
reports/profiling/. Never modifies data/raw or data/bronze.

Usage:
    PYTHONPATH=src python3 scripts/build_silver_layer.py
    PYTHONPATH=src python3 scripts/build_silver_layer.py --force
    PYTHONPATH=src python3 scripts/build_silver_layer.py --dataset patients --dataset encounters
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from careflow.config import load_config  # noqa: E402
from careflow.logging_config import get_logger  # noqa: E402
from careflow.transformation.silver_transformer import (  # noqa: E402
    SILVER_MANIFEST_FILENAME,
    SILVER_QUALITY_REPORT_FILENAME,
    SILVER_QUALITY_SUMMARY_FILENAME,
    run_silver_pipeline,
)

logger = get_logger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force", action="store_true",
        help="Reprocess every selected dataset even if its Bronze checksum is unchanged",
    )
    parser.add_argument(
        "--dataset", action="append", dest="datasets", default=None,
        help="Restrict the run to this dataset (repeatable). Omit for a full run.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config()
    bronze_dir = config.get_path("data", "bronze")
    silver_dir = config.get_path("data", "silver")
    reports_dir = config.get_path("reports_profiling")

    if not (bronze_dir / "bronze_manifest.json").is_file():
        logger.error(
            "Bronze manifest not found at %s. Run scripts/ingest_bronze.py first.",
            bronze_dir / "bronze_manifest.json",
        )
        return 1

    manifest, quality_report = run_silver_pipeline(
        bronze_dir=bronze_dir,
        silver_dir=silver_dir,
        reports_dir=reports_dir,
        datasets=args.datasets,
        force=args.force,
    )

    summary = manifest["summary"]
    logger.info(
        "Silver build complete: %d dataset(s) (processed=%d, skipped=%d, failed=%d)",
        summary["total_datasets"], summary["processed"], summary["skipped"], summary["failed"],
    )

    for entry in manifest["datasets"]:
        if entry["status"] == "failed":
            logger.warning("Silver dataset '%s' failed: %s", entry["dataset"], entry["error"])

    quality_summary = quality_report["summary"]
    logger.info(
        "Silver quality checks: %d (pass=%d, warning=%d, fail=%d, skipped=%d)",
        quality_summary["total_checks"], quality_summary["passed"],
        quality_summary["warnings"], quality_summary["failed"], quality_summary["skipped"],
    )

    logger.info("Silver manifest: %s", silver_dir / SILVER_MANIFEST_FILENAME)
    logger.info("Silver quality report: %s", reports_dir / SILVER_QUALITY_REPORT_FILENAME)
    logger.info("Silver quality summary: %s", reports_dir / SILVER_QUALITY_SUMMARY_FILENAME)

    return 0


if __name__ == "__main__":
    sys.exit(main())
