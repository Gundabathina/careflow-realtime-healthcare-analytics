"""Bronze-layer ingestion for CareFlow Analytics.

Reads validated Synthea CSVs in chunks, applies explicit schema typing
(dates and configured numeric fields typed; everything else kept as
text so identifiers and ZIP codes are never corrupted), and streams the
result into columnar Parquet files. Before promoting a file, the Phase
2D relationship and data quality validation is re-run and used as a
gate: any file with a blocking rule/relationship status is skipped
rather than ingested. Never modifies data/raw.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from careflow.config import Config, get_project_root, load_config
from careflow.data_generation.synthea_generator import _sha256_of_file
from careflow.logging_config import get_logger
from careflow.profiling.data_quality import (
    DATA_QUALITY_REPORT_FILENAME,
    DATA_QUALITY_SUMMARY_FILENAME,
    FAILED_RECORD_SAMPLES_FILENAME,
    DataQualitySettings,
    build_data_quality_report,
    build_failed_record_samples,
    load_data_quality_settings,
    write_data_quality_report_json,
    write_data_quality_summary_csv,
    write_failed_record_samples_json,
)
from careflow.profiling.data_quality import _parse_date_series
from careflow.profiling.file_profiler import (
    DEFAULT_CHUNK_SIZE,
    _relative_to_root,
    discover_csv_files,
)
from careflow.profiling.relationship_profiler import (
    RELATIONSHIP_SUMMARY_FILENAME,
    build_relationship_summary,
    write_relationship_summary_json,
)

logger = get_logger(__name__)

BRONZE_VERSION = "1.0.0"
BRONZE_MANIFEST_FILENAME = "bronze_manifest.json"

DEFAULT_BLOCK_ON_STATUSES: tuple[str, ...] = ("fail",)

DEFAULT_DATE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("patients.csv", "BIRTHDATE"),
    ("patients.csv", "DEATHDATE"),
    ("encounters.csv", "START"),
    ("encounters.csv", "STOP"),
    ("conditions.csv", "START"),
    ("conditions.csv", "STOP"),
    ("procedures.csv", "START"),
    ("procedures.csv", "STOP"),
    ("medications.csv", "START"),
    ("medications.csv", "STOP"),
    ("careplans.csv", "START"),
    ("careplans.csv", "STOP"),
    ("devices.csv", "START"),
    ("devices.csv", "STOP"),
    ("immunizations.csv", "DATE"),
    ("observations.csv", "DATE"),
    ("imaging_studies.csv", "DATE"),
    ("claims.csv", "SERVICEDATE"),
    ("claims.csv", "CURRENTILLNESSDATE"),
)


@dataclass(frozen=True)
class BronzeSettings:
    """Resolved, configuration-driven settings for Bronze ingestion."""

    chunk_size: int
    gate_enabled: bool
    block_on_statuses: tuple[str, ...]
    date_columns: tuple[tuple[str, str], ...]


def load_bronze_settings(config: Config | None = None) -> BronzeSettings:
    """Load Bronze ingestion settings from ``bronze`` in config, with defaults."""
    cfg = config or load_config()

    def _pairs(key: str, default: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
        raw = cfg.get("bronze", key, default=None)
        if not raw:
            return tuple(default)
        return tuple((item["file"], item["column"]) for item in raw)

    return BronzeSettings(
        chunk_size=int(cfg.get("bronze", "chunk_size", default=DEFAULT_CHUNK_SIZE)),
        gate_enabled=bool(cfg.get("bronze", "gate", "enabled", default=True)),
        block_on_statuses=tuple(
            cfg.get("bronze", "gate", "block_on_statuses", default=list(DEFAULT_BLOCK_ON_STATUSES))
        ),
        date_columns=_pairs("date_columns", DEFAULT_DATE_COLUMNS),
    )


# ---------------------------------------------------------------------------
# Validation gate
# ---------------------------------------------------------------------------


def gate_reasons(
    filename: str, data_quality_report: dict, relationship_summary: dict, block_on: set[str]
) -> list[str]:
    """Return blocking reasons for ``filename``, or an empty list if clear to ingest."""
    reasons: list[str] = []

    for rule in data_quality_report["rules"]:
        source_files = [s.strip() for s in rule["source_file"].split(",")]
        if filename in source_files and rule["status"] in block_on:
            reasons.append(f"data quality rule '{rule['rule_id']}' status={rule['status']}")

    for rel in relationship_summary["relationships"]:
        if rel["child_file"] == filename and rel["status"] in block_on:
            reasons.append(f"relationship '{rel['relationship']}' status={rel['status']}")

    return reasons


# ---------------------------------------------------------------------------
# Schema typing
# ---------------------------------------------------------------------------


def _build_pyarrow_schema(columns: list[str], date_columns: set[str], numeric_columns: set[str]) -> pa.Schema:
    fields = []
    for col in columns:
        if col in date_columns:
            fields.append(pa.field(col, pa.timestamp("ns", tz="UTC")))
        elif col in numeric_columns:
            fields.append(pa.field(col, pa.float64()))
        else:
            fields.append(pa.field(col, pa.string()))
    return pa.schema(fields)


def _schema_labels(columns: list[str], date_columns: set[str], numeric_columns: set[str]) -> dict[str, str]:
    labels = {}
    for col in columns:
        if col in date_columns:
            labels[col] = "timestamp"
        elif col in numeric_columns:
            labels[col] = "double"
        else:
            labels[col] = "string"
    return labels


def _cast_chunk(
    chunk: pd.DataFrame, date_columns: set[str], numeric_columns: set[str]
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Cast configured columns in-place; return (chunk, {column: newly-failed count})."""
    failures: dict[str, int] = {}
    for col in chunk.columns:
        if col in date_columns:
            original_null = chunk[col].isna()
            parsed = _parse_date_series(chunk[col])
            newly_failed = int((parsed.isna() & ~original_null).sum())
            if newly_failed:
                failures[col] = newly_failed
            chunk[col] = parsed
        elif col in numeric_columns:
            original_null = chunk[col].isna()
            parsed = pd.to_numeric(chunk[col], errors="coerce")
            newly_failed = int((parsed.isna() & ~original_null).sum())
            if newly_failed:
                failures[col] = newly_failed
            chunk[col] = parsed
    return chunk, failures


# ---------------------------------------------------------------------------
# Per-file ingestion
# ---------------------------------------------------------------------------


def _empty_result(filename: str, status: str, reason: str) -> dict:
    return {
        "filename": filename,
        "bronze_filename": None,
        "status": status,
        "row_count": None,
        "column_count": None,
        "source_checksum": None,
        "bronze_checksum": None,
        "bronze_size_bytes": None,
        "schema": {},
        "cast_failures": {},
        "reason": reason,
    }


def ingest_file(
    csv_path: Path,
    bronze_path: Path,
    date_columns: set[str],
    numeric_columns: set[str],
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    blocked_reasons: list[str] | None = None,
) -> dict:
    """Convert one CSV file to a typed Parquet file, or skip/block it with a reason."""
    filename = csv_path.name

    if blocked_reasons:
        return _empty_result(filename, "blocked", "; ".join(blocked_reasons))

    if not csv_path.is_file():
        return _empty_result(filename, "skipped", "Source file missing")

    try:
        file_size = csv_path.stat().st_size
    except OSError as exc:
        return _empty_result(filename, "skipped", f"Could not stat source file: {exc}")

    if file_size == 0:
        return _empty_result(filename, "skipped", "Source file is empty (0 bytes)")

    try:
        header = pd.read_csv(csv_path, nrows=0)
    except pd.errors.EmptyDataError:
        return _empty_result(filename, "skipped", "Source file has no header or columns")
    except (pd.errors.ParserError, UnicodeDecodeError, OSError) as exc:
        return _empty_result(filename, "skipped", f"Could not read source file: {exc}")

    columns = list(header.columns)
    file_date_cols = {c for c in columns if c in date_columns}
    file_numeric_cols = {c for c in columns if c in numeric_columns}
    target_schema = _build_pyarrow_schema(columns, file_date_cols, file_numeric_cols)

    bronze_path.parent.mkdir(parents=True, exist_ok=True)
    row_count = 0
    cast_failures: dict[str, int] = {}
    writer: pq.ParquetWriter | None = None

    try:
        writer = pq.ParquetWriter(str(bronze_path), target_schema)
        for chunk in pd.read_csv(csv_path, chunksize=chunk_size, dtype=str):
            row_count += len(chunk)
            typed_chunk, chunk_failures = _cast_chunk(chunk, file_date_cols, file_numeric_cols)
            for col, n in chunk_failures.items():
                cast_failures[col] = cast_failures.get(col, 0) + n
            table = pa.Table.from_pandas(typed_chunk, schema=target_schema, preserve_index=False)
            writer.write_table(table)
    except (pd.errors.ParserError, UnicodeDecodeError, OSError, pa.ArrowException) as exc:
        if writer is not None:
            writer.close()
            writer = None
        bronze_path.unlink(missing_ok=True)
        return _empty_result(filename, "skipped", f"Error while converting to Parquet: {exc}")
    finally:
        if writer is not None:
            writer.close()

    if row_count == 0:
        bronze_path.unlink(missing_ok=True)
        return _empty_result(filename, "skipped", "Source file has a header but no data rows")

    return {
        "filename": filename,
        "bronze_filename": bronze_path.name,
        "status": "ingested",
        "row_count": row_count,
        "column_count": len(columns),
        "source_checksum": _sha256_of_file(csv_path),
        "bronze_checksum": _sha256_of_file(bronze_path),
        "bronze_size_bytes": bronze_path.stat().st_size,
        "schema": _schema_labels(columns, file_date_cols, file_numeric_cols),
        "cast_failures": cast_failures,
        "reason": None,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def build_bronze_manifest(
    csv_dir: Path,
    bronze_dir: Path,
    settings: BronzeSettings | None = None,
    data_quality_settings: DataQualitySettings | None = None,
    relationship_summary: dict | None = None,
    data_quality_report: dict | None = None,
) -> dict:
    """Re-run the validation gate and ingest every discovered CSV, returning the manifest.

    ``relationship_summary``/``data_quality_report`` may be supplied directly
    (e.g. by tests) to avoid recomputation; otherwise both are freshly built
    from ``csv_dir`` so the gate reflects the current state of the data.
    """
    settings = settings or load_bronze_settings()
    dq_settings = data_quality_settings or load_data_quality_settings()

    if relationship_summary is None:
        relationship_summary = build_relationship_summary(csv_dir, chunk_size=settings.chunk_size)
    if data_quality_report is None:
        data_quality_report = build_data_quality_report(csv_dir, settings=dq_settings)

    numeric_columns_by_file: dict[str, set[str]] = {}
    for file, column in dq_settings.cost_fields:
        numeric_columns_by_file.setdefault(file, set()).add(column)

    date_columns_by_file: dict[str, set[str]] = {}
    for file, column in settings.date_columns:
        date_columns_by_file.setdefault(file, set()).add(column)

    block_on = set(settings.block_on_statuses)
    files = discover_csv_files(csv_dir)
    results: list[dict] = []
    counts = {"ingested": 0, "blocked": 0, "skipped": 0}

    for csv_path in files:
        filename = csv_path.name
        blocked_reasons = (
            gate_reasons(filename, data_quality_report, relationship_summary, block_on)
            if settings.gate_enabled
            else []
        )
        bronze_path = bronze_dir / f"{csv_path.stem}.parquet"
        result = ingest_file(
            csv_path,
            bronze_path,
            date_columns_by_file.get(filename, set()),
            numeric_columns_by_file.get(filename, set()),
            chunk_size=settings.chunk_size,
            blocked_reasons=blocked_reasons,
        )
        results.append(result)
        counts[result["status"]] = counts.get(result["status"], 0) + 1
        logger.info("Bronze ingest %s -> %s", filename, result["status"])

    return {
        "bronze_version": BRONZE_VERSION,
        "ingested_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_directory": _relative_to_root(csv_dir),
        "bronze_directory": _relative_to_root(bronze_dir),
        "gate": {
            "enabled": settings.gate_enabled,
            "block_on_statuses": sorted(block_on),
        },
        "summary": {
            "total_files": len(files),
            "ingested": counts.get("ingested", 0),
            "blocked": counts.get("blocked", 0),
            "skipped": counts.get("skipped", 0),
        },
        "files": results,
    }


def write_bronze_manifest_json(manifest: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, default=str)
        fh.write("\n")


def run_bronze_ingestion(
    csv_dir: Path,
    bronze_dir: Path,
    reports_dir: Path,
    settings: BronzeSettings | None = None,
) -> dict:
    """Refresh the Phase 2D validation reports, then ingest every gated-clear CSV.

    Reuses (does not reimplement) the relationship and data quality engines
    from Phase 2D: their reports are rebuilt and rewritten to ``reports_dir``
    so the gate always reflects the latest data/raw contents, and the same
    reports are used to decide what to promote into Bronze.
    """
    settings = settings or load_bronze_settings()
    dq_settings = load_data_quality_settings()

    relationship_summary = build_relationship_summary(csv_dir, chunk_size=settings.chunk_size)
    write_relationship_summary_json(relationship_summary, reports_dir / RELATIONSHIP_SUMMARY_FILENAME)

    data_quality_report = build_data_quality_report(csv_dir, settings=dq_settings)
    write_data_quality_report_json(data_quality_report, reports_dir / DATA_QUALITY_REPORT_FILENAME)
    write_data_quality_summary_csv(data_quality_report, reports_dir / DATA_QUALITY_SUMMARY_FILENAME)

    failed_samples = build_failed_record_samples(data_quality_report, relationship_summary)
    write_failed_record_samples_json(failed_samples, reports_dir / FAILED_RECORD_SAMPLES_FILENAME)

    manifest = build_bronze_manifest(
        csv_dir,
        bronze_dir,
        settings=settings,
        data_quality_settings=dq_settings,
        relationship_summary=relationship_summary,
        data_quality_report=data_quality_report,
    )
    write_bronze_manifest_json(manifest, bronze_dir / BRONZE_MANIFEST_FILENAME)
    return manifest
