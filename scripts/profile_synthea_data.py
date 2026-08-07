#!/usr/bin/env python3
"""Profile every Synthea CSV file in data/raw/synthea/csv.

Dynamically discovers all CSV files (no filenames or schemas assumed) and
writes reports/profiling/dataset_profile.json and
reports/profiling/column_profile.csv. Never modifies data/raw.

Usage:
    PYTHONPATH=src python3 scripts/profile_synthea_data.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from careflow.config import load_config  # noqa: E402
from careflow.logging_config import get_logger  # noqa: E402
from careflow.profiling.file_profiler import (  # noqa: E402
    COLUMN_PROFILE_FILENAME,
    DATASET_PROFILE_FILENAME,
    run_profiling,
)

logger = get_logger(__name__)


def main() -> int:
    config = load_config()
    csv_dir = config.get_path("data", "raw_synthea_csv")
    output_dir = config.get_path("reports_profiling")

    if not csv_dir.is_dir():
        logger.error("Synthea CSV directory not found: %s", csv_dir)
        return 1

    manifest = run_profiling(csv_dir=csv_dir, output_dir=output_dir)
    summary = manifest["dataset_summary"]

    logger.info(
        "Profiled %d file(s) (%d ok, %d empty, %d error), %d row(s) total.",
        summary["total_files"],
        summary["files_ok"],
        summary["files_empty"],
        summary["files_error"],
        summary["total_rows"],
    )
    logger.info("Dataset profile: %s", output_dir / DATASET_PROFILE_FILENAME)
    logger.info("Column profile: %s", output_dir / COLUMN_PROFILE_FILENAME)

    if summary["total_files"] == 0:
        logger.warning("No CSV files found in %s.", csv_dir)

    return 0


if __name__ == "__main__":
    sys.exit(main())
