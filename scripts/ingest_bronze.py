#!/usr/bin/env python3
"""Ingest validated Synthea CSVs into the Bronze layer as typed Parquet files.

Re-runs the Phase 2D relationship and data quality validation (refreshing
reports/profiling/ as a side effect) and uses it as a promotion gate: any
CSV with a blocking rule or relationship status is skipped rather than
ingested. Everything that passes the gate is streamed, in chunks, into a
typed Parquet file under data/bronze/ with a bronze_manifest.json
recording per-file row counts, schema, and checksums. Never modifies
data/raw.

Usage:
    PYTHONPATH=src python3 scripts/ingest_bronze.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from careflow.bronze.ingest import BRONZE_MANIFEST_FILENAME, run_bronze_ingestion  # noqa: E402
from careflow.config import load_config  # noqa: E402
from careflow.logging_config import get_logger  # noqa: E402

logger = get_logger(__name__)


def main() -> int:
    config = load_config()
    csv_dir = config.get_path("data", "raw_synthea_csv")
    bronze_dir = config.get_path("data", "bronze")
    reports_dir = config.get_path("reports_profiling")

    if not csv_dir.is_dir():
        logger.error("Synthea CSV directory not found: %s", csv_dir)
        return 1

    manifest = run_bronze_ingestion(csv_dir=csv_dir, bronze_dir=bronze_dir, reports_dir=reports_dir)
    summary = manifest["summary"]

    logger.info(
        "Bronze ingestion complete: %d file(s) (ingested=%d, blocked=%d, skipped=%d)",
        summary["total_files"],
        summary["ingested"],
        summary["blocked"],
        summary["skipped"],
    )
    logger.info("Bronze manifest: %s", bronze_dir / BRONZE_MANIFEST_FILENAME)

    if summary["blocked"]:
        logger.warning(
            "%d file(s) were blocked by the validation gate; see bronze_manifest.json for reasons.",
            summary["blocked"],
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
