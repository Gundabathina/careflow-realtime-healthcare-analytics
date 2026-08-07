"""Bronze-to-Silver transformation engine for CareFlow Analytics.

Reads Bronze Parquet datasets, applies the declarative rules in
``schema_registry.py`` (column renaming, safe date/numeric typing,
deduplication, lineage metadata) plus a small set of dataset-specific
derived columns for patients/encounters/conditions/procedures/
medications/observations, and writes clean, analytics-ready Silver
Parquet files. Incremental: a dataset is skipped when its Bronze source
checksum has not changed since the last successful Silver run.

Reads only from data/bronze/. Writes only to data/silver/ and
reports/profiling/. Never modifies data/raw or data/bronze.
"""

from __future__ import annotations

import csv as csv_module
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from careflow.bronze.ingest import BRONZE_MANIFEST_FILENAME
from careflow.config import Config, load_config
from careflow.logging_config import get_logger
from careflow.profiling.file_profiler import _relative_to_root
from careflow.transformation.schema_registry import (
    SCHEMA_VERSION,
    DatasetSchema,
    get_schema,
    list_datasets,
)

logger = get_logger(__name__)

TRANSFORMATION_VERSION = "1.0.0"
DEFAULT_REFERENCE_DATE = "2026-01-01T00:00:00Z"

SILVER_MANIFEST_FILENAME = "silver_manifest.json"
SILVER_QUALITY_REPORT_FILENAME = "silver_quality_report.json"
SILVER_QUALITY_SUMMARY_FILENAME = "silver_quality_summary.csv"

SILVER_QUALITY_SUMMARY_FIELDNAMES = [
    "check_id", "dataset", "category", "status", "records_evaluated", "records_failed", "details",
]

# Cost-like numeric columns checked for negativity. Deliberately excludes
# lat/lon (which can be legitimately negative) and count-like fields.
COST_LIKE_COLUMNS: dict[str, tuple[str, ...]] = {
    "patients": ("healthcare_expenses", "healthcare_coverage", "income"),
    "encounters": ("base_encounter_cost", "total_claim_cost", "payer_coverage"),
    "procedures": ("base_cost",),
    "medications": ("base_cost", "payer_coverage", "total_cost"),
    "immunizations": ("base_cost",),
}

_AGE_GROUP_BINS = [-1, 17, 34, 49, 64, 79, 200]
_AGE_GROUP_LABELS = ["0-17", "18-34", "35-49", "50-64", "65-79", "80+"]


@dataclass(frozen=True)
class SilverSettings:
    """Configuration-driven settings for the Silver transformation run."""

    reference_date: pd.Timestamp


def load_silver_settings(config: Config | None = None) -> SilverSettings:
    """Load Silver settings, requiring an explicit (never "today") reference date."""
    cfg = config or load_config()
    raw = cfg.get("silver", "reference_date", default=DEFAULT_REFERENCE_DATE)
    reference_date = pd.Timestamp(raw)
    reference_date = (
        reference_date.tz_localize("UTC") if reference_date.tzinfo is None else reference_date.tz_convert("UTC")
    )
    return SilverSettings(reference_date=reference_date)


# ---------------------------------------------------------------------------
# Generic per-dataset pipeline steps
# ---------------------------------------------------------------------------


def _convert_empty_strings_to_null(df: pd.DataFrame) -> None:
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].replace("", None)


def _parse_date_columns(df: pd.DataFrame, schema: DatasetSchema) -> dict[str, int]:
    failures: dict[str, int] = {}
    for col in schema.date_columns:
        if col not in df.columns:
            continue
        original_null = df[col].isna()
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = (
                df[col].dt.tz_localize("UTC") if df[col].dt.tz is None else df[col].dt.tz_convert("UTC")
            )
            continue
        parsed = pd.to_datetime(df[col], errors="coerce", format="mixed", utc=True)
        newly_failed = int((parsed.isna() & ~original_null).sum())
        if newly_failed:
            failures[col] = newly_failed
        df[col] = parsed
    return failures


def _coerce_numeric_columns(df: pd.DataFrame, schema: DatasetSchema) -> dict[str, int]:
    failures: dict[str, int] = {}
    for col in schema.numeric_columns:
        if col not in df.columns:
            continue
        if pd.api.types.is_numeric_dtype(df[col]):
            continue
        original_null = df[col].isna()
        parsed = pd.to_numeric(df[col], errors="coerce")
        newly_failed = int((parsed.isna() & ~original_null).sum())
        if newly_failed:
            failures[col] = newly_failed
        df[col] = parsed
    return failures


# ---------------------------------------------------------------------------
# Dataset-specific derived-column transforms
# ---------------------------------------------------------------------------


def _bucket_age_group(age_years: pd.Series) -> pd.Series:
    numeric = age_years.astype("float64")
    groups = pd.cut(numeric, bins=_AGE_GROUP_BINS, labels=_AGE_GROUP_LABELS)
    return groups.astype(object).where(age_years.notna(), None)


def _transform_patients(df: pd.DataFrame, settings: SilverSettings) -> tuple[pd.DataFrame, dict]:
    df["gender"] = df["gender"].where(df["gender"].isna(), df["gender"].str.strip().str.upper())
    df["race"] = df["race"].where(df["race"].isna(), df["race"].str.strip().str.lower())
    df["ethnicity"] = df["ethnicity"].where(df["ethnicity"].isna(), df["ethnicity"].str.strip().str.lower())

    df["is_deceased"] = df["deathdate"].notna()

    reference_date = settings.reference_date
    valid_birth = df["birthdate"].notna() & (df["birthdate"] <= reference_date)
    age_days = (reference_date - df["birthdate"]).dt.days
    age_years = np.floor(age_days / 365.25)
    df["age_at_reference_date"] = age_years.where(valid_birth).astype("Int64")
    df["age_group"] = _bucket_age_group(df["age_at_reference_date"])

    df["is_duplicate_patient_id"] = df["patient_id"].duplicated(keep=False)

    before = len(df)
    df = df.drop_duplicates(subset=["patient_id"], keep="first").reset_index(drop=True)
    extra_removed = before - len(df)

    return df, {"extra_duplicate_rows_removed": extra_removed, "parse_failures": {}}


def _transform_encounters(df: pd.DataFrame, settings: SilverSettings) -> tuple[pd.DataFrame, dict]:
    df["encounter_class"] = df["encounter_class"].where(
        df["encounter_class"].isna(), df["encounter_class"].str.strip().str.lower()
    )

    valid_pair = df["start"].notna() & df["stop"].notna()
    duration_minutes = (df["stop"] - df["start"]).dt.total_seconds() / 60.0

    df["stop_before_start"] = valid_pair & (df["stop"] < df["start"])
    df["encounter_duration_minutes"] = duration_minutes.where(valid_pair & ~df["stop_before_start"])

    df["encounter_date"] = df["start"].dt.strftime("%Y-%m-%d")
    df["encounter_year"] = df["start"].dt.year.astype("Int64")
    df["encounter_month"] = df["start"].dt.month.astype("Int64")

    df["is_inpatient"] = df["encounter_class"] == "inpatient"
    df["is_emergency"] = df["encounter_class"] == "emergency"

    return df, {"extra_duplicate_rows_removed": 0, "parse_failures": {}}


def _transform_conditions(df: pd.DataFrame, settings: SilverSettings) -> tuple[pd.DataFrame, dict]:
    df["is_active"] = df["stop"].isna()
    valid_pair = df["start"].notna() & df["stop"].notna()
    duration_days = (df["stop"] - df["start"]).dt.days
    df["condition_duration_days"] = duration_days.where(valid_pair).astype("Int64")
    return df, {"extra_duplicate_rows_removed": 0, "parse_failures": {}}


def _transform_procedures(df: pd.DataFrame, settings: SilverSettings) -> tuple[pd.DataFrame, dict]:
    valid_pair = df["start"].notna() & df["stop"].notna()
    duration_minutes = (df["stop"] - df["start"]).dt.total_seconds() / 60.0
    df["procedure_duration_minutes"] = duration_minutes.where(valid_pair)
    return df, {"extra_duplicate_rows_removed": 0, "parse_failures": {}}


def _transform_medications(df: pd.DataFrame, settings: SilverSettings) -> tuple[pd.DataFrame, dict]:
    df["is_active"] = df["stop"].isna()
    valid_pair = df["start"].notna() & df["stop"].notna()
    duration_days = (df["stop"] - df["start"]).dt.days
    df["medication_duration_days"] = duration_days.where(valid_pair).astype("Int64")
    return df, {"extra_duplicate_rows_removed": 0, "parse_failures": {}}


def _transform_observations(df: pd.DataFrame, settings: SilverSettings) -> tuple[pd.DataFrame, dict]:
    # VALUE is legitimately mixed numeric/qualitative; only populate
    # numeric_value where parsing succeeds, never force it.
    df["numeric_value"] = pd.to_numeric(df["value"], errors="coerce")
    return df, {"extra_duplicate_rows_removed": 0, "parse_failures": {}}


CUSTOM_TRANSFORMS: dict[str, Callable[[pd.DataFrame, SilverSettings], tuple[pd.DataFrame, dict]]] = {
    "patients": _transform_patients,
    "encounters": _transform_encounters,
    "conditions": _transform_conditions,
    "procedures": _transform_procedures,
    "medications": _transform_medications,
    "observations": _transform_observations,
}


# ---------------------------------------------------------------------------
# Per-dataset orchestration
# ---------------------------------------------------------------------------


def _manifest_entry(
    dataset: str,
    status: str,
    source_bronze_file: str | None = None,
    source_checksum: str | None = None,
    target_file: str | None = None,
    source_row_count: int | None = None,
    target_row_count: int | None = None,
    duplicate_rows_removed: int | None = None,
    parse_failures: dict | None = None,
    error: str | None = None,
) -> dict:
    return {
        "dataset": dataset,
        "status": status,
        "source_bronze_file": source_bronze_file,
        "source_checksum": source_checksum,
        "target_file": target_file,
        "source_row_count": source_row_count,
        "target_row_count": target_row_count,
        "duplicate_rows_removed": duplicate_rows_removed,
        "parse_failures": parse_failures or {},
        "error": error,
    }


def transform_dataset(
    bronze_dir: Path,
    schema: DatasetSchema,
    bronze_entry: dict,
    settings: SilverSettings,
    silver_dir: Path,
    ingestion_timestamp: str | None,
    run_started_at: datetime,
) -> dict:
    """Transform one Bronze Parquet file into a Silver Parquet file.

    Never raises on data problems: read/parse/write failures produce a
    "failed" manifest entry so the rest of the run can continue.
    """
    source_checksum = bronze_entry.get("source_checksum")
    bronze_path = bronze_dir / schema.bronze_file

    if not bronze_path.is_file():
        return _manifest_entry(
            schema.target_dataset, "failed", schema.bronze_file, source_checksum,
            error=f"Bronze file missing on disk: {bronze_path}",
        )

    try:
        df = pd.read_parquet(bronze_path)
    except Exception as exc:  # noqa: BLE001 - never crash the whole run on a bad Parquet file
        return _manifest_entry(
            schema.target_dataset, "failed", schema.bronze_file, source_checksum,
            error=f"Could not read Bronze file: {exc}",
        )

    source_row_count = len(df)

    missing_source_cols = set(schema.expected_source_columns) - set(df.columns)
    if missing_source_cols:
        return _manifest_entry(
            schema.target_dataset, "failed", schema.bronze_file, source_checksum,
            source_row_count=source_row_count,
            error=f"Missing expected source columns: {sorted(missing_source_cols)}",
        )

    df = df[list(schema.column_mapping.keys())].rename(columns=schema.column_mapping)

    _convert_empty_strings_to_null(df)
    parse_failures = _parse_date_columns(df, schema)
    for col, count in _coerce_numeric_columns(df, schema).items():
        parse_failures[col] = parse_failures.get(col, 0) + count

    before_dedup = len(df)
    df = df.drop_duplicates(keep="first").reset_index(drop=True)
    duplicate_rows_removed = before_dedup - len(df)

    df["source_file"] = schema.source_file
    df["ingestion_timestamp_utc"] = ingestion_timestamp
    df["transformation_timestamp_utc"] = run_started_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    df["source_checksum"] = source_checksum

    transform_fn = CUSTOM_TRANSFORMS.get(schema.transform_name)
    if transform_fn is not None:
        try:
            df, extra = transform_fn(df, settings)
        except Exception as exc:  # noqa: BLE001
            return _manifest_entry(
                schema.target_dataset, "failed", schema.bronze_file, source_checksum,
                source_row_count=source_row_count,
                error=f"Custom transform '{schema.transform_name}' failed: {exc}",
            )
        duplicate_rows_removed += extra.get("extra_duplicate_rows_removed", 0)
        for col, count in extra.get("parse_failures", {}).items():
            parse_failures[col] = parse_failures.get(col, 0) + count

    target_path = silver_dir / f"{schema.target_dataset}.parquet"
    silver_dir.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(target_path, engine="pyarrow", index=False)
    except Exception as exc:  # noqa: BLE001
        target_path.unlink(missing_ok=True)
        return _manifest_entry(
            schema.target_dataset, "failed", schema.bronze_file, source_checksum,
            source_row_count=source_row_count,
            error=f"Could not write Silver Parquet: {exc}",
        )

    return _manifest_entry(
        schema.target_dataset, "processed", schema.bronze_file, source_checksum,
        target_file=_relative_to_root(target_path),
        source_row_count=source_row_count,
        target_row_count=len(df),
        duplicate_rows_removed=duplicate_rows_removed,
        parse_failures=parse_failures,
    )


# ---------------------------------------------------------------------------
# Incremental build orchestration
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _parquet_row_count(path: Path) -> int:
    return pq.ParquetFile(str(path)).metadata.num_rows


def _summarize_entries(entries: list[dict]) -> dict:
    counts = {"processed": 0, "skipped": 0, "failed": 0}
    for entry in entries:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    return {
        "total_datasets": len(entries),
        "processed": counts.get("processed", 0),
        "skipped": counts.get("skipped", 0),
        "failed": counts.get("failed", 0),
    }


def run_silver_build(
    bronze_dir: Path,
    silver_dir: Path,
    datasets: list[str] | None = None,
    force: bool = False,
    settings: SilverSettings | None = None,
    bronze_manifest: dict | None = None,
    previous_manifest: dict | None = None,
) -> dict:
    """Build (or incrementally refresh) the Silver layer manifest.

    A dataset is skipped when its Bronze ``source_checksum`` matches the
    checksum recorded the last time it was successfully processed and its
    Silver file still exists; otherwise it is (re)processed. ``datasets``
    restricts which datasets are considered this run -- any dataset left
    out keeps its previous manifest entry unchanged.
    """
    settings = settings or load_silver_settings()
    run_started_at = datetime.now(timezone.utc)
    run_id = f"silver_{run_started_at.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"

    bronze_manifest_path = bronze_dir / BRONZE_MANIFEST_FILENAME
    if bronze_manifest is None:
        bronze_manifest = _load_json(bronze_manifest_path)
    if bronze_manifest is None:
        raise FileNotFoundError(f"Bronze manifest not found: {bronze_manifest_path}")

    bronze_entries = {f["filename"]: f for f in bronze_manifest.get("files", [])}
    ingestion_timestamp = bronze_manifest.get("ingested_at_utc")

    silver_manifest_path = silver_dir / SILVER_MANIFEST_FILENAME
    if previous_manifest is None:
        previous_manifest = _load_json(silver_manifest_path)
    previous_entries = {e["dataset"]: e for e in (previous_manifest or {}).get("datasets", [])}

    all_datasets = list_datasets()
    in_scope = set(datasets) if datasets else set(all_datasets)
    unknown = in_scope - set(all_datasets)
    if unknown:
        raise ValueError(f"Unknown dataset(s) requested: {sorted(unknown)}")

    result_entries: list[dict] = []
    for dataset in all_datasets:
        if dataset not in in_scope:
            if dataset in previous_entries:
                result_entries.append(previous_entries[dataset])
            continue

        schema = get_schema(dataset)
        bronze_entry = bronze_entries.get(schema.source_file)

        if bronze_entry is None or bronze_entry.get("status") != "ingested":
            status = bronze_entry.get("status") if bronze_entry else "missing"
            result_entries.append(_manifest_entry(
                dataset, "failed", schema.bronze_file,
                error=f"Bronze file not available for '{dataset}' (status={status})",
            ))
            continue

        current_checksum = bronze_entry.get("source_checksum")
        target_path = silver_dir / f"{dataset}.parquet"
        previous_entry = previous_entries.get(dataset)

        if (
            not force
            and previous_entry is not None
            and previous_entry.get("status") in ("processed", "skipped")
            and previous_entry.get("source_checksum") == current_checksum
            and target_path.is_file()
        ):
            entry = dict(previous_entry)
            entry["status"] = "skipped"
            entry["target_row_count"] = _parquet_row_count(target_path)
            result_entries.append(entry)
            logger.info("Silver skip %s (Bronze checksum unchanged)", dataset)
            continue

        entry = transform_dataset(
            bronze_dir, schema, bronze_entry, settings, silver_dir, ingestion_timestamp, run_started_at
        )
        result_entries.append(entry)
        logger.info("Silver %s -> %s", dataset, entry["status"])

    completed_at = datetime.now(timezone.utc)
    return {
        "run_id": run_id,
        "started_at_utc": run_started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "completed_at_utc": completed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "transformation_version": TRANSFORMATION_VERSION,
        "schema_version": SCHEMA_VERSION,
        "bronze_manifest_path": _relative_to_root(bronze_manifest_path),
        "reference_date": settings.reference_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "datasets": result_entries,
        "summary": _summarize_entries(result_entries),
    }


def write_silver_manifest_json(manifest: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, default=str)
        fh.write("\n")


# ---------------------------------------------------------------------------
# Quality checks
# ---------------------------------------------------------------------------


def _quality_check(
    check_id: str, dataset: str, category: str, status: str, details: str,
    records_evaluated: int | None = None, records_failed: int | None = None,
) -> dict:
    return {
        "check_id": check_id,
        "dataset": dataset,
        "category": category,
        "status": status,
        "details": details,
        "records_evaluated": records_evaluated,
        "records_failed": records_failed,
    }


def _dataset_quality_checks(dataset: str, schema: DatasetSchema, df: pd.DataFrame, entry: dict) -> list[dict]:
    checks: list[dict] = []
    n = len(df)

    missing = [c for c in schema.target_columns if c not in df.columns]
    checks.append(_quality_check(
        f"target_columns_exist:{dataset}", dataset, "structural",
        "pass" if not missing else "fail",
        "All expected target columns present" if not missing else f"Missing target columns: {missing}",
        len(schema.target_columns), len(missing),
    ))

    if schema.primary_key and schema.primary_key in df.columns:
        null_count = int(df[schema.primary_key].isna().sum())
        checks.append(_quality_check(
            f"primary_key_not_null:{dataset}", dataset, "completeness",
            "pass" if null_count == 0 else "fail",
            f"{null_count} null primary key value(s)", n, null_count,
        ))
        non_null = df[schema.primary_key].dropna()
        dup_count = int(non_null.duplicated(keep=False).sum())
        checks.append(_quality_check(
            f"primary_key_unique:{dataset}", dataset, "uniqueness",
            "pass" if dup_count == 0 else "fail",
            f"{dup_count} row(s) share a duplicate primary key", len(non_null), dup_count,
        ))

    for col in schema.required_columns:
        if col not in df.columns:
            continue
        null_count = int(df[col].isna().sum())
        checks.append(_quality_check(
            f"required_not_null:{dataset}:{col}", dataset, "completeness",
            "pass" if null_count == 0 else "warning",
            f"{null_count} null value(s) in required column '{col}'", n, null_count,
        ))

    for col in schema.foreign_keys:
        if col not in df.columns:
            continue
        null_count = int(df[col].isna().sum())
        checks.append(_quality_check(
            f"foreign_key_not_null:{dataset}:{col}", dataset, "referential",
            "pass" if null_count == 0 else "warning",
            f"{null_count} null value(s) in foreign key '{col}'", n, null_count,
        ))

    for col in schema.date_columns:
        if col not in df.columns:
            continue
        is_dt = pd.api.types.is_datetime64_any_dtype(df[col])
        is_utc = is_dt and df[col].dt.tz is not None and str(df[col].dt.tz) == "UTC"
        checks.append(_quality_check(
            f"date_type_utc:{dataset}:{col}", dataset, "type",
            "pass" if is_utc else "fail",
            "UTC-aware timestamp" if is_utc else f"Column '{col}' is not a UTC-aware timestamp",
            n, 0 if is_utc else n,
        ))

    for col in schema.numeric_columns:
        if col not in df.columns:
            continue
        is_num = pd.api.types.is_numeric_dtype(df[col])
        checks.append(_quality_check(
            f"numeric_type:{dataset}:{col}", dataset, "type",
            "pass" if is_num else "fail",
            "Numeric dtype" if is_num else f"Column '{col}' is not numeric", n, 0 if is_num else n,
        ))

    for col in schema.identifier_columns:
        if col not in df.columns:
            continue
        is_text = not pd.api.types.is_numeric_dtype(df[col])
        checks.append(_quality_check(
            f"identifier_type:{dataset}:{col}", dataset, "type",
            "pass" if is_text else "fail",
            "Text dtype preserved" if is_text else f"Identifier column '{col}' was coerced to numeric",
            n, 0 if is_text else n,
        ))

    if "zip" in df.columns:
        is_text = not pd.api.types.is_numeric_dtype(df["zip"])
        checks.append(_quality_check(
            f"zip_is_string:{dataset}", dataset, "type",
            "pass" if is_text else "fail",
            "ZIP preserved as text" if is_text else "ZIP was coerced to numeric", n, 0 if is_text else n,
        ))

    for col in COST_LIKE_COLUMNS.get(dataset, ()):
        if col not in df.columns:
            continue
        negative_count = int((df[col] < 0).sum())
        checks.append(_quality_check(
            f"no_negative_cost:{dataset}:{col}", dataset, "numeric",
            "pass" if negative_count == 0 else "fail",
            f"{negative_count} negative value(s) in '{col}'", int(df[col].notna().sum()), negative_count,
        ))

    if dataset == "encounters" and "stop_before_start" in df.columns:
        flagged = int(df["stop_before_start"].sum())
        checks.append(_quality_check(
            "encounter_stop_not_before_start", dataset, "temporal",
            "pass" if flagged == 0 else "warning",
            f"{flagged} encounter(s) flagged with STOP before START", n, flagged,
        ))

    source_rc = entry.get("source_row_count")
    dup_removed = entry.get("duplicate_rows_removed") or 0
    target_rc = entry.get("target_row_count")
    if source_rc is not None and target_rc is not None:
        reconciled = (source_rc - dup_removed) == target_rc
        checks.append(_quality_check(
            f"row_count_reconciliation:{dataset}", dataset, "completeness",
            "pass" if reconciled else "fail",
            f"source={source_rc} duplicates_removed={dup_removed} target={target_rc}",
            source_rc, 0 if reconciled else abs((source_rc - dup_removed) - target_rc),
        ))

    return checks


def _cross_dataset_quality_checks(silver_dir: Path) -> list[dict]:
    checks: list[dict] = []
    patients_path = silver_dir / "patients.parquet"
    encounters_path = silver_dir / "encounters.parquet"
    if not (patients_path.is_file() and encounters_path.is_file()):
        return checks
    try:
        patients = pd.read_parquet(patients_path, columns=["patient_id", "birthdate"])
        encounters = pd.read_parquet(encounters_path, columns=["patient_id", "start"])
        merged = encounters.merge(patients, on="patient_id", how="inner")
        comparable = merged["birthdate"].notna() & merged["start"].notna()
        violations = int((comparable & (merged["start"] < merged["birthdate"])).sum())
        checks.append(_quality_check(
            "birthdate_before_encounter_start", "cross_dataset", "temporal",
            "pass" if violations == 0 else "warning",
            f"{violations} encounter(s) start before the patient's birthdate",
            int(comparable.sum()), violations,
        ))
    except Exception as exc:  # noqa: BLE001
        checks.append(_quality_check(
            "birthdate_before_encounter_start", "cross_dataset", "temporal", "skipped", str(exc)
        ))
    return checks


def build_silver_quality_report(silver_dir: Path, manifest: dict) -> dict:
    """Run structural, type, and reconciliation quality checks over Silver output."""
    checks: list[dict] = []
    entries_by_dataset = {e["dataset"]: e for e in manifest["datasets"]}

    for dataset in list_datasets():
        entry = entries_by_dataset.get(dataset)
        if entry is None or entry["status"] not in ("processed", "skipped"):
            continue
        schema = get_schema(dataset)
        target_path = silver_dir / f"{dataset}.parquet"
        if not target_path.is_file():
            checks.append(_quality_check(
                f"file_exists:{dataset}", dataset, "structural", "fail", "Silver file missing", 0, 0
            ))
            continue
        try:
            df = pd.read_parquet(target_path)
        except Exception as exc:  # noqa: BLE001
            checks.append(_quality_check(
                f"file_readable:{dataset}", dataset, "structural", "fail", str(exc), 0, 0
            ))
            continue
        checks.extend(_dataset_quality_checks(dataset, schema, df, entry))

    checks.extend(_cross_dataset_quality_checks(silver_dir))

    status_counts = {"pass": 0, "warning": 0, "fail": 0, "skipped": 0}
    for check in checks:
        status_counts[check["status"]] += 1

    return {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "transformation_version": TRANSFORMATION_VERSION,
        "run_id": manifest.get("run_id"),
        "summary": {
            "total_checks": len(checks),
            "passed": status_counts["pass"],
            "warnings": status_counts["warning"],
            "failed": status_counts["fail"],
            "skipped": status_counts["skipped"],
        },
        "checks": checks,
    }


def write_silver_quality_report_json(report: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
        fh.write("\n")


def write_silver_quality_summary_csv(report: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv_module.DictWriter(fh, fieldnames=SILVER_QUALITY_SUMMARY_FIELDNAMES)
        writer.writeheader()
        for check in report["checks"]:
            writer.writerow({key: check.get(key) for key in SILVER_QUALITY_SUMMARY_FIELDNAMES})


# ---------------------------------------------------------------------------
# Top-level pipeline
# ---------------------------------------------------------------------------


def run_silver_pipeline(
    bronze_dir: Path,
    silver_dir: Path,
    reports_dir: Path,
    datasets: list[str] | None = None,
    force: bool = False,
    settings: SilverSettings | None = None,
) -> tuple[dict, dict]:
    """Run the incremental Silver build and quality checks, writing all outputs."""
    manifest = run_silver_build(bronze_dir, silver_dir, datasets=datasets, force=force, settings=settings)
    write_silver_manifest_json(manifest, silver_dir / SILVER_MANIFEST_FILENAME)

    quality_report = build_silver_quality_report(silver_dir, manifest)
    write_silver_quality_report_json(quality_report, reports_dir / SILVER_QUALITY_REPORT_FILENAME)
    write_silver_quality_summary_csv(quality_report, reports_dir / SILVER_QUALITY_SUMMARY_FILENAME)

    return manifest, quality_report
