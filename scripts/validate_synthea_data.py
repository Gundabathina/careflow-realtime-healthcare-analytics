#!/usr/bin/env python3
"""Validate referential integrity and data quality of Synthea CSV files.

Runs the relationship validator and the data quality rule engine against
data/raw/synthea/csv and writes four report files under
reports/profiling/. Never modifies data/raw.

Usage:
    PYTHONPATH=src python3 scripts/validate_synthea_data.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from careflow.config import load_config  # noqa: E402
from careflow.logging_config import get_logger  # noqa: E402
from careflow.profiling.data_quality import (  # noqa: E402
    DATA_QUALITY_REPORT_FILENAME,
    DATA_QUALITY_SUMMARY_FILENAME,
    FAILED_RECORD_SAMPLES_FILENAME,
    run_data_quality_validation,
)
from careflow.profiling.relationship_profiler import (  # noqa: E402
    RELATIONSHIP_SUMMARY_FILENAME,
    run_relationship_validation,
)

logger = get_logger(__name__)


def main() -> int:
    config = load_config()
    csv_dir = config.get_path("data", "raw_synthea_csv")
    output_dir = config.get_path("reports_profiling")

    if not csv_dir.is_dir():
        logger.error("Synthea CSV directory not found: %s", csv_dir)
        return 1

    relationship_summary = run_relationship_validation(csv_dir=csv_dir, output_dir=output_dir)
    rel_counts = relationship_summary["summary"]
    logger.info(
        "Relationships checked: %d (pass=%d, warning=%d, fail=%d, skipped=%d)",
        rel_counts["total_relationships"],
        rel_counts["passed"],
        rel_counts["warnings"],
        rel_counts["failed"],
        rel_counts["skipped"],
    )

    dq_report = run_data_quality_validation(
        csv_dir=csv_dir, output_dir=output_dir, relationship_summary=relationship_summary
    )
    dq_counts = dq_report["summary"]
    logger.info(
        "Data quality rules executed: %d (pass=%d, warning=%d, fail=%d, skipped=%d)",
        dq_counts["total_rules"],
        dq_counts["passed"],
        dq_counts["warnings"],
        dq_counts["failed"],
        dq_counts["skipped"],
    )

    logger.info("Relationship summary: %s", output_dir / RELATIONSHIP_SUMMARY_FILENAME)
    logger.info("Data quality report: %s", output_dir / DATA_QUALITY_REPORT_FILENAME)
    logger.info("Data quality summary CSV: %s", output_dir / DATA_QUALITY_SUMMARY_FILENAME)
    logger.info("Failed record samples: %s", output_dir / FAILED_RECORD_SAMPLES_FILENAME)

    return 0


if __name__ == "__main__":
    sys.exit(main())
