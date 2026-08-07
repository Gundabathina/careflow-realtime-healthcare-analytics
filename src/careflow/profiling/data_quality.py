"""Structural and analytical data quality rules for CareFlow Analytics.

Implements configuration-driven checks (completeness, uniqueness, temporal
ordering, numeric plausibility, domain values, identifier format) over the
Synthea CSV files. These are structural checks, not clinical validation,
and are designed to be reusable for future Bronze-layer validation.
"""

from __future__ import annotations

import csv as csv_module
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from careflow.config import Config, load_config
from careflow.logging_config import get_logger
from careflow.profiling.file_profiler import DEFAULT_CHUNK_SIZE, _relative_to_root
from careflow.profiling.relationship_profiler import RelationshipConfig, load_relationship_configs

logger = get_logger(__name__)

DATA_QUALITY_VERSION = "1.0.0"
DATA_QUALITY_REPORT_FILENAME = "data_quality_report.json"
DATA_QUALITY_SUMMARY_FILENAME = "data_quality_summary.csv"
FAILED_RECORD_SAMPLES_FILENAME = "failed_record_samples.json"

DEFAULT_MAX_SAMPLES = 10
DEFAULT_WARNING_PCT = 1.0
DEFAULT_FAIL_PCT = 5.0
DEFAULT_MAX_PATIENT_AGE_YEARS = 115

# Zero-tolerance thresholds for structural/identity rules: any failure at
# all is at least a warning, and more than 1% is a fail.
ZERO_TOLERANCE_WARNING_PCT = 0.0
ZERO_TOLERANCE_FAIL_PCT = 1.0

STATUS_VALUES = ("pass", "warning", "fail", "skipped")

DEFAULT_ALLOWED_ENCOUNTER_CLASSES = (
    "ambulatory", "wellness", "outpatient", "emergency", "urgentcare",
    "inpatient", "home", "hospice", "virtual", "snf",
)

DEFAULT_CRITICAL_COLUMNS: tuple[tuple[str, str], ...] = (
    ("patients.csv", "BIRTHDATE"),
    ("encounters.csv", "PATIENT"),
    ("encounters.csv", "START"),
    ("encounters.csv", "ENCOUNTERCLASS"),
    ("organizations.csv", "Id"),
    ("providers.csv", "Id"),
    ("payers.csv", "Id"),
)

DEFAULT_COST_FIELDS: tuple[tuple[str, str], ...] = (
    ("encounters.csv", "BASE_ENCOUNTER_COST"),
    ("encounters.csv", "TOTAL_CLAIM_COST"),
    ("encounters.csv", "PAYER_COVERAGE"),
    ("procedures.csv", "BASE_COST"),
    ("medications.csv", "BASE_COST"),
    ("medications.csv", "TOTALCOST"),
    ("medications.csv", "PAYER_COVERAGE"),
    ("immunizations.csv", "BASE_COST"),
)

DEFAULT_UUID_COLUMNS: tuple[tuple[str, str], ...] = (
    ("patients.csv", "Id"),
    ("encounters.csv", "Id"),
    ("encounters.csv", "PATIENT"),
    ("encounters.csv", "ORGANIZATION"),
    ("encounters.csv", "PROVIDER"),
    ("encounters.csv", "PAYER"),
    ("organizations.csv", "Id"),
    ("providers.csv", "Id"),
    ("payers.csv", "Id"),
    ("conditions.csv", "PATIENT"),
    ("conditions.csv", "ENCOUNTER"),
    ("procedures.csv", "PATIENT"),
    ("procedures.csv", "ENCOUNTER"),
    ("medications.csv", "PATIENT"),
    ("medications.csv", "ENCOUNTER"),
    ("observations.csv", "PATIENT"),
    ("observations.csv", "ENCOUNTER"),
    ("careplans.csv", "Id"),
    ("careplans.csv", "PATIENT"),
    ("careplans.csv", "ENCOUNTER"),
    ("devices.csv", "PATIENT"),
    ("devices.csv", "ENCOUNTER"),
    ("immunizations.csv", "PATIENT"),
    ("immunizations.csv", "ENCOUNTER"),
    ("imaging_studies.csv", "Id"),
    ("imaging_studies.csv", "PATIENT"),
    ("imaging_studies.csv", "ENCOUNTER"),
    ("claims.csv", "Id"),
    ("claims.csv", "PATIENTID"),
    ("claims.csv", "APPOINTMENTID"),
)

DEFAULT_ZIP_COLUMNS: tuple[tuple[str, str], ...] = (
    ("patients.csv", "ZIP"),
    ("organizations.csv", "ZIP"),
    ("providers.csv", "ZIP"),
)

UUID_PATTERN = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
ZIP_TEXT_PATTERN = re.compile(r"^\d{3,10}$")

DATA_QUALITY_SUMMARY_FIELDNAMES = [
    "rule_id",
    "rule_name",
    "category",
    "source_file",
    "severity",
    "status",
    "records_evaluated",
    "records_failed",
    "failure_percentage",
    "warning_threshold_pct",
    "fail_threshold_pct",
    "skipped_reason",
]


@dataclass(frozen=True)
class DataQualitySettings:
    """Resolved, configuration-driven settings for the data quality engine."""

    chunk_size: int
    max_samples: int
    warning_threshold_pct: float
    fail_threshold_pct: float
    max_patient_age_years: float
    allowed_encounter_classes: tuple[str, ...]
    critical_columns: tuple[tuple[str, str], ...]
    cost_fields: tuple[tuple[str, str], ...]
    uuid_columns: tuple[tuple[str, str], ...]
    zip_columns: tuple[tuple[str, str], ...]


def load_data_quality_settings(config: Config | None = None) -> DataQualitySettings:
    """Load data quality settings from ``data_quality`` in config, with defaults."""
    cfg = config or load_config()

    def _pairs(key: str, default: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
        raw = cfg.get("data_quality", key, default=None)
        if not raw:
            return tuple(default)
        return tuple((item["file"], item["column"]) for item in raw)

    return DataQualitySettings(
        chunk_size=int(cfg.get("data_quality", "chunk_size", default=DEFAULT_CHUNK_SIZE)),
        max_samples=int(cfg.get("data_quality", "max_failure_samples", default=DEFAULT_MAX_SAMPLES)),
        warning_threshold_pct=float(
            cfg.get("data_quality", "thresholds", "warning_pct", default=DEFAULT_WARNING_PCT)
        ),
        fail_threshold_pct=float(
            cfg.get("data_quality", "thresholds", "fail_pct", default=DEFAULT_FAIL_PCT)
        ),
        max_patient_age_years=float(
            cfg.get("data_quality", "max_patient_age_years", default=DEFAULT_MAX_PATIENT_AGE_YEARS)
        ),
        allowed_encounter_classes=tuple(
            cfg.get("data_quality", "allowed_encounter_classes", default=list(DEFAULT_ALLOWED_ENCOUNTER_CLASSES))
        ),
        critical_columns=_pairs("critical_columns", DEFAULT_CRITICAL_COLUMNS),
        cost_fields=_pairs("cost_fields", DEFAULT_COST_FIELDS),
        uuid_columns=_pairs("uuid_columns", DEFAULT_UUID_COLUMNS),
        zip_columns=_pairs("zip_columns", DEFAULT_ZIP_COLUMNS),
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        return value.item()
    return value


def _parse_date_series(series: pd.Series) -> pd.Series:
    try:
        return pd.to_datetime(series, errors="coerce", format="mixed", utc=True)
    except (TypeError, ValueError):
        return pd.Series([pd.NaT] * len(series), index=series.index)


def _check_files_and_columns(csv_dir: Path, requirements: list[tuple[str, str | None]]) -> str | None:
    """Return a skip reason if any required (file, column) is missing, else None."""
    checked: dict[str, list[str]] = {}
    for filename, column in requirements:
        if filename not in checked:
            path = csv_dir / filename
            if not path.is_file():
                return f"Required file missing: {filename}"
            try:
                header = pd.read_csv(path, nrows=0)
            except pd.errors.EmptyDataError:
                return f"Required file is empty: {filename}"
            except (pd.errors.ParserError, UnicodeDecodeError, OSError) as exc:
                return f"Could not read {filename}: {exc}"
            checked[filename] = list(header.columns)
        if column is not None and column not in checked[filename]:
            return f"Required column '{column}' missing in {filename}"
    return None


def _build_result(
    rule_id: str,
    rule_name: str,
    category: str,
    business_reason: str,
    source_file: str,
    columns_used: list[str],
    records_evaluated: int | None,
    records_failed: int | None,
    severity: str,
    sample_failures: list[dict],
    warning_threshold_pct: float,
    fail_threshold_pct: float,
    max_samples: int = DEFAULT_MAX_SAMPLES,
    skipped_reason: str | None = None,
) -> dict:
    threshold = {"warning_pct": warning_threshold_pct, "fail_pct": fail_threshold_pct}

    if skipped_reason is not None:
        return {
            "rule_id": rule_id,
            "rule_name": rule_name,
            "category": category,
            "business_reason": business_reason,
            "source_file": source_file,
            "columns_used": columns_used,
            "records_evaluated": None,
            "records_failed": None,
            "failure_percentage": None,
            "severity": severity,
            "status": "skipped",
            "threshold": threshold,
            "sample_failures": [],
            "skipped_reason": skipped_reason,
        }

    failure_pct = (records_failed / records_evaluated * 100) if records_evaluated else 0.0
    if failure_pct > fail_threshold_pct:
        status = "fail"
    elif failure_pct > warning_threshold_pct:
        status = "warning"
    else:
        status = "pass"

    return {
        "rule_id": rule_id,
        "rule_name": rule_name,
        "category": category,
        "business_reason": business_reason,
        "source_file": source_file,
        "columns_used": columns_used,
        "records_evaluated": records_evaluated,
        "records_failed": records_failed,
        "failure_percentage": round(failure_pct, 4),
        "severity": severity,
        "status": status,
        "threshold": threshold,
        "sample_failures": [{k: _json_safe(v) for k, v in s.items()} for s in sample_failures[:max_samples]],
        "skipped_reason": None,
    }


# ---------------------------------------------------------------------------
# Individual rule evaluators
# ---------------------------------------------------------------------------


def _check_not_null(
    csv_dir, file, column, rule_id, rule_name, category, business_reason, severity,
    warning_pct, fail_pct, max_samples, chunk_size, identifier_columns: tuple[str, ...] = (),
) -> dict:
    skip = _check_files_and_columns(csv_dir, [(file, column)])
    if skip:
        return _build_result(rule_id, rule_name, category, business_reason, file, [column],
                              None, None, severity, [], warning_pct, fail_pct, max_samples, skip)

    path = csv_dir / file
    usecols = list(dict.fromkeys([column, *identifier_columns]))
    total = 0
    failed = 0
    samples: list[dict] = []
    try:
        for chunk in pd.read_csv(path, usecols=usecols, chunksize=chunk_size, dtype=str):
            total += len(chunk)
            mask = chunk[column].isna()
            failed += int(mask.sum())
            if mask.any() and len(samples) < max_samples:
                bad_rows = chunk[mask.values]
                for _, row in bad_rows.head(max_samples - len(samples)).iterrows():
                    sample = {"column": column, "value": None}
                    for id_col in identifier_columns:
                        sample[id_col.lower()] = row.get(id_col)
                    samples.append(sample)
    except (pd.errors.ParserError, UnicodeDecodeError, OSError) as exc:
        return _build_result(rule_id, rule_name, category, business_reason, file, [column],
                              None, None, severity, [], warning_pct, fail_pct, max_samples,
                              f"Could not read {file}: {exc}")

    return _build_result(rule_id, rule_name, category, business_reason, file, [column],
                          total, failed, severity, samples, warning_pct, fail_pct, max_samples)


def _check_unique(
    csv_dir, file, column, rule_id, rule_name, category, business_reason, severity,
    warning_pct, fail_pct, max_samples, chunk_size,
) -> dict:
    skip = _check_files_and_columns(csv_dir, [(file, column)])
    if skip:
        return _build_result(rule_id, rule_name, category, business_reason, file, [column],
                              None, None, severity, [], warning_pct, fail_pct, max_samples, skip)

    path = csv_dir / file
    counts: dict[str, int] = {}
    total = 0
    try:
        for chunk in pd.read_csv(path, usecols=[column], chunksize=chunk_size, dtype=str):
            total += len(chunk)
            for value in chunk[column].dropna():
                counts[value] = counts.get(value, 0) + 1
    except (pd.errors.ParserError, UnicodeDecodeError, OSError) as exc:
        return _build_result(rule_id, rule_name, category, business_reason, file, [column],
                              None, None, severity, [], warning_pct, fail_pct, max_samples,
                              f"Could not read {file}: {exc}")

    duplicates = {v: c for v, c in counts.items() if c > 1}
    failed = sum(c - 1 for c in duplicates.values())
    samples = [{"value": v, "occurrences": c} for v, c in list(duplicates.items())[:max_samples]]

    return _build_result(rule_id, rule_name, category, business_reason, file, [column],
                          total, failed, severity, samples, warning_pct, fail_pct, max_samples)


def _check_date_parses(
    csv_dir, file, column, rule_id, rule_name, business_reason, severity,
    warning_pct, fail_pct, max_samples, chunk_size, required: bool,
    identifier_columns: tuple[str, ...] = (),
) -> dict:
    category = "format"
    skip = _check_files_and_columns(csv_dir, [(file, column)])
    if skip:
        return _build_result(rule_id, rule_name, category, business_reason, file, [column],
                              None, None, severity, [], warning_pct, fail_pct, max_samples, skip)

    path = csv_dir / file
    usecols = list(dict.fromkeys([column, *identifier_columns]))
    total = 0
    failed = 0
    samples: list[dict] = []
    try:
        for chunk in pd.read_csv(path, usecols=usecols, chunksize=chunk_size, dtype=str):
            series = chunk[column]
            parsed = _parse_date_series(series)
            if required:
                bad_mask = parsed.isna()
                total += len(chunk)
            else:
                non_null_mask = series.notna()
                bad_mask = non_null_mask & parsed.isna()
                total += int(non_null_mask.sum())
            failed += int(bad_mask.sum())
            if bad_mask.any() and len(samples) < max_samples:
                bad_rows = chunk[bad_mask.values]
                for _, row in bad_rows.head(max_samples - len(samples)).iterrows():
                    sample = {"column": column, "value": row[column]}
                    for id_col in identifier_columns:
                        sample[id_col.lower()] = row.get(id_col)
                    samples.append(sample)
    except (pd.errors.ParserError, UnicodeDecodeError, OSError) as exc:
        return _build_result(rule_id, rule_name, category, business_reason, file, [column],
                              None, None, severity, [], warning_pct, fail_pct, max_samples,
                              f"Could not read {file}: {exc}")

    return _build_result(rule_id, rule_name, category, business_reason, file, [column],
                          total, failed, severity, samples, warning_pct, fail_pct, max_samples)


def _check_date_order_same_file(
    csv_dir, file, start_column, stop_column, rule_id, rule_name, business_reason, severity,
    warning_pct, fail_pct, max_samples, chunk_size, identifier_columns: tuple[str, ...] = (),
) -> dict:
    category = "temporal"
    skip = _check_files_and_columns(csv_dir, [(file, start_column), (file, stop_column)])
    if skip:
        return _build_result(rule_id, rule_name, category, business_reason, file,
                              [start_column, stop_column], None, None, severity, [],
                              warning_pct, fail_pct, max_samples, skip)

    path = csv_dir / file
    usecols = list(dict.fromkeys([start_column, stop_column, *identifier_columns]))
    total = 0
    failed = 0
    samples: list[dict] = []
    try:
        for chunk in pd.read_csv(path, usecols=usecols, chunksize=chunk_size, dtype=str):
            stop_present = chunk[stop_column].notna()
            if not stop_present.any():
                continue
            relevant = chunk[stop_present]
            start_parsed = _parse_date_series(relevant[start_column])
            stop_parsed = _parse_date_series(relevant[stop_column])
            comparable = start_parsed.notna() & stop_parsed.notna()
            total += int(comparable.sum())
            bad_mask = comparable & (stop_parsed < start_parsed)
            failed += int(bad_mask.sum())
            if bad_mask.any() and len(samples) < max_samples:
                bad_rows = relevant[bad_mask.values]
                for _, row in bad_rows.head(max_samples - len(samples)).iterrows():
                    sample = {start_column: row[start_column], stop_column: row[stop_column]}
                    for id_col in identifier_columns:
                        sample[id_col.lower()] = row.get(id_col)
                    samples.append(sample)
    except (pd.errors.ParserError, UnicodeDecodeError, OSError) as exc:
        return _build_result(rule_id, rule_name, category, business_reason, file,
                              [start_column, stop_column], None, None, severity, [],
                              warning_pct, fail_pct, max_samples, f"Could not read {file}: {exc}")

    return _build_result(rule_id, rule_name, category, business_reason, file,
                          [start_column, stop_column], total, failed, severity, samples,
                          warning_pct, fail_pct, max_samples)


def _check_birthdate_before_encounter(
    csv_dir, patients_file, patient_id_column, birthdate_column,
    encounters_file, encounter_id_column, encounter_patient_column, encounter_start_column,
    rule_id, rule_name, business_reason, severity, warning_pct, fail_pct, max_samples, chunk_size,
) -> dict:
    category = "temporal"
    columns_used = [birthdate_column, encounter_start_column]
    skip = _check_files_and_columns(csv_dir, [
        (patients_file, patient_id_column),
        (patients_file, birthdate_column),
        (encounters_file, encounter_id_column),
        (encounters_file, encounter_patient_column),
        (encounters_file, encounter_start_column),
    ])
    if skip:
        return _build_result(rule_id, rule_name, category, business_reason, encounters_file,
                              columns_used, None, None, severity, [], warning_pct, fail_pct,
                              max_samples, skip)

    patients_path = csv_dir / patients_file
    encounters_path = csv_dir / encounters_file
    birthdates: dict[str, pd.Timestamp] = {}
    try:
        for chunk in pd.read_csv(patients_path, usecols=[patient_id_column, birthdate_column],
                                  chunksize=chunk_size, dtype=str):
            parsed = _parse_date_series(chunk[birthdate_column])
            for pid, bd in zip(chunk[patient_id_column], parsed):
                if pd.notna(bd):
                    birthdates[pid] = bd
    except (pd.errors.ParserError, UnicodeDecodeError, OSError) as exc:
        return _build_result(rule_id, rule_name, category, business_reason, encounters_file,
                              columns_used, None, None, severity, [], warning_pct, fail_pct,
                              max_samples, f"Could not read {patients_file}: {exc}")

    total = 0
    failed = 0
    samples: list[dict] = []
    try:
        for chunk in pd.read_csv(encounters_path,
                                  usecols=[encounter_id_column, encounter_patient_column, encounter_start_column],
                                  chunksize=chunk_size, dtype=str):
            start_parsed = _parse_date_series(chunk[encounter_start_column])
            mapped_birthdate = chunk[encounter_patient_column].map(birthdates)
            comparable = mapped_birthdate.notna() & start_parsed.notna()
            total += int(comparable.sum())
            bad_mask = comparable & (start_parsed < mapped_birthdate)
            failed += int(bad_mask.sum())
            if bad_mask.any() and len(samples) < max_samples:
                bad_view = chunk[bad_mask.values].copy()
                bad_view["_start_parsed"] = start_parsed[bad_mask.values].values
                bad_view["_birthdate"] = mapped_birthdate[bad_mask.values].values
                for _, row in bad_view.head(max_samples - len(samples)).iterrows():
                    samples.append({
                        "encounter_id": row[encounter_id_column],
                        "patient_id": row[encounter_patient_column],
                        "encounter_start": str(row["_start_parsed"]),
                        "birthdate": str(row["_birthdate"]),
                    })
    except (pd.errors.ParserError, UnicodeDecodeError, OSError) as exc:
        return _build_result(rule_id, rule_name, category, business_reason, encounters_file,
                              columns_used, None, None, severity, [], warning_pct, fail_pct,
                              max_samples, f"Could not read {encounters_file}: {exc}")

    return _build_result(rule_id, rule_name, category, business_reason,
                          f"{patients_file},{encounters_file}", columns_used, total, failed,
                          severity, samples, warning_pct, fail_pct, max_samples)


def _check_numeric_non_negative(
    csv_dir, file, column, rule_id, category, business_reason, severity,
    warning_pct, fail_pct, max_samples, chunk_size, identifier_columns: tuple[str, ...] = (),
) -> dict:
    rule_name = f"{column} in {file} is not negative"
    skip = _check_files_and_columns(csv_dir, [(file, column)])
    if skip:
        return _build_result(rule_id, rule_name, category, business_reason, file, [column],
                              None, None, severity, [], warning_pct, fail_pct, max_samples, skip)

    path = csv_dir / file
    usecols = list(dict.fromkeys([column, *identifier_columns]))
    total = 0
    failed = 0
    samples: list[dict] = []
    try:
        for chunk in pd.read_csv(path, usecols=usecols, chunksize=chunk_size):
            numeric = pd.to_numeric(chunk[column], errors="coerce")
            relevant = numeric.notna()
            total += int(relevant.sum())
            bad_mask = relevant & (numeric < 0)
            failed += int(bad_mask.sum())
            if bad_mask.any() and len(samples) < max_samples:
                bad_rows = chunk[bad_mask.values]
                for _, row in bad_rows.head(max_samples - len(samples)).iterrows():
                    sample = {"column": column, "value": row[column]}
                    for id_col in identifier_columns:
                        sample[id_col.lower()] = row.get(id_col)
                    samples.append(sample)
    except (pd.errors.ParserError, UnicodeDecodeError, OSError) as exc:
        return _build_result(rule_id, rule_name, category, business_reason, file, [column],
                              None, None, severity, [], warning_pct, fail_pct, max_samples,
                              f"Could not read {file}: {exc}")

    return _build_result(rule_id, rule_name, category, business_reason, file, [column],
                          total, failed, severity, samples, warning_pct, fail_pct, max_samples)


def _check_coverage_not_exceeds_total(
    csv_dir, file, coverage_column, total_column, rule_id, rule_name, business_reason, severity,
    warning_pct, fail_pct, max_samples, chunk_size, identifier_columns: tuple[str, ...] = (),
) -> dict:
    category = "numeric"
    columns_used = [coverage_column, total_column]
    skip = _check_files_and_columns(csv_dir, [(file, coverage_column), (file, total_column)])
    if skip:
        return _build_result(rule_id, rule_name, category, business_reason, file, columns_used,
                              None, None, severity, [], warning_pct, fail_pct, max_samples, skip)

    path = csv_dir / file
    usecols = list(dict.fromkeys([coverage_column, total_column, *identifier_columns]))
    total_rows = 0
    failed = 0
    samples: list[dict] = []
    try:
        for chunk in pd.read_csv(path, usecols=usecols, chunksize=chunk_size):
            coverage = pd.to_numeric(chunk[coverage_column], errors="coerce")
            total_cost = pd.to_numeric(chunk[total_column], errors="coerce")
            comparable = coverage.notna() & total_cost.notna()
            total_rows += int(comparable.sum())
            bad_mask = comparable & (coverage > total_cost)
            failed += int(bad_mask.sum())
            if bad_mask.any() and len(samples) < max_samples:
                bad_rows = chunk[bad_mask.values]
                for _, row in bad_rows.head(max_samples - len(samples)).iterrows():
                    sample = {coverage_column: row[coverage_column], total_column: row[total_column]}
                    for id_col in identifier_columns:
                        sample[id_col.lower()] = row.get(id_col)
                    samples.append(sample)
    except (pd.errors.ParserError, UnicodeDecodeError, OSError) as exc:
        return _build_result(rule_id, rule_name, category, business_reason, file, columns_used,
                              None, None, severity, [], warning_pct, fail_pct, max_samples,
                              f"Could not read {file}: {exc}")

    return _build_result(rule_id, rule_name, category, business_reason, file, columns_used,
                          total_rows, failed, severity, samples, warning_pct, fail_pct, max_samples)


def _check_required_foreign_keys_exist(
    csv_dir, relationships: list[RelationshipConfig], rule_id, rule_name, business_reason,
    severity, warning_pct, fail_pct, max_samples,
) -> dict:
    category = "referential"
    total = 0
    failed = 0
    samples: list[dict] = []
    for rel in relationships:
        path = csv_dir / rel.child_file
        if not path.is_file():
            continue
        total += 1
        try:
            header = pd.read_csv(path, nrows=0)
        except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError, OSError):
            failed += 1
            if len(samples) < max_samples:
                samples.append({"file": rel.child_file, "expected_column": rel.child_key, "issue": "file unreadable"})
            continue
        if rel.child_key not in header.columns:
            failed += 1
            if len(samples) < max_samples:
                samples.append({"file": rel.child_file, "expected_column": rel.child_key, "issue": "column missing"})

    if total == 0:
        return _build_result(rule_id, rule_name, category, business_reason, "multiple", ["*"],
                              None, None, severity, [], warning_pct, fail_pct, max_samples,
                              "No child files present to check for required foreign key columns.")

    return _build_result(rule_id, rule_name, category, business_reason, "multiple", ["*"],
                          total, failed, severity, samples, warning_pct, fail_pct, max_samples)


def _check_allowed_values(
    csv_dir, file, column, allowed_values, rule_id, rule_name, business_reason, severity,
    warning_pct, fail_pct, max_samples, chunk_size, identifier_columns: tuple[str, ...] = (),
) -> dict:
    category = "domain"
    skip = _check_files_and_columns(csv_dir, [(file, column)])
    if skip:
        return _build_result(rule_id, rule_name, category, business_reason, file, [column],
                              None, None, severity, [], warning_pct, fail_pct, max_samples, skip)

    allowed_lower = {str(v).strip().lower() for v in allowed_values}
    path = csv_dir / file
    usecols = list(dict.fromkeys([column, *identifier_columns]))
    total = 0
    failed = 0
    samples: list[dict] = []
    try:
        for chunk in pd.read_csv(path, usecols=usecols, chunksize=chunk_size, dtype=str):
            total += len(chunk)
            normalized = chunk[column].astype(str).str.strip().str.lower()
            bad_mask = chunk[column].isna() | ~normalized.isin(allowed_lower)
            failed += int(bad_mask.sum())
            if bad_mask.any() and len(samples) < max_samples:
                bad_rows = chunk[bad_mask.values]
                for _, row in bad_rows.head(max_samples - len(samples)).iterrows():
                    sample = {"column": column, "value": row[column]}
                    for id_col in identifier_columns:
                        sample[id_col.lower()] = row.get(id_col)
                    samples.append(sample)
    except (pd.errors.ParserError, UnicodeDecodeError, OSError) as exc:
        return _build_result(rule_id, rule_name, category, business_reason, file, [column],
                              None, None, severity, [], warning_pct, fail_pct, max_samples,
                              f"Could not read {file}: {exc}")

    return _build_result(rule_id, rule_name, category, business_reason, file, [column],
                          total, failed, severity, samples, warning_pct, fail_pct, max_samples)


def _check_uuid_format(
    csv_dir, file, column, rule_id, category, business_reason, severity,
    warning_pct, fail_pct, max_samples, chunk_size,
) -> dict:
    rule_name = f"{column} in {file} is a valid UUID"
    skip = _check_files_and_columns(csv_dir, [(file, column)])
    if skip:
        return _build_result(rule_id, rule_name, category, business_reason, file, [column],
                              None, None, severity, [], warning_pct, fail_pct, max_samples, skip)

    path = csv_dir / file
    total = 0
    failed = 0
    samples: list[dict] = []
    try:
        for chunk in pd.read_csv(path, usecols=[column], chunksize=chunk_size, dtype=str):
            non_null = chunk[column].dropna()
            total += len(non_null)
            if non_null.empty:
                continue
            bad_mask = ~non_null.str.match(UUID_PATTERN)
            failed += int(bad_mask.sum())
            if bad_mask.any() and len(samples) < max_samples:
                for v in non_null[bad_mask.values].head(max_samples - len(samples)):
                    samples.append({"column": column, "value": v})
    except (pd.errors.ParserError, UnicodeDecodeError, OSError) as exc:
        return _build_result(rule_id, rule_name, category, business_reason, file, [column],
                              None, None, severity, [], warning_pct, fail_pct, max_samples,
                              f"Could not read {file}: {exc}")

    return _build_result(rule_id, rule_name, category, business_reason, file, [column],
                          total, failed, severity, samples, warning_pct, fail_pct, max_samples)


def _check_zip_text_format(
    csv_dir, file, column, rule_id, category, business_reason, severity,
    warning_pct, fail_pct, max_samples, chunk_size,
) -> dict:
    rule_name = f"{column} in {file} is treated as identifier text"
    skip = _check_files_and_columns(csv_dir, [(file, column)])
    if skip:
        return _build_result(rule_id, rule_name, category, business_reason, file, [column],
                              None, None, severity, [], warning_pct, fail_pct, max_samples, skip)

    path = csv_dir / file
    total = 0
    failed = 0
    samples: list[dict] = []
    try:
        for chunk in pd.read_csv(path, usecols=[column], chunksize=chunk_size, dtype=str):
            non_null = chunk[column].dropna()
            total += len(non_null)
            if non_null.empty:
                continue
            bad_mask = ~non_null.str.match(ZIP_TEXT_PATTERN)
            failed += int(bad_mask.sum())
            if bad_mask.any() and len(samples) < max_samples:
                for v in non_null[bad_mask.values].head(max_samples - len(samples)):
                    samples.append({"column": column, "value": v})
    except (pd.errors.ParserError, UnicodeDecodeError, OSError) as exc:
        return _build_result(rule_id, rule_name, category, business_reason, file, [column],
                              None, None, severity, [], warning_pct, fail_pct, max_samples,
                              f"Could not read {file}: {exc}")

    return _build_result(rule_id, rule_name, category, business_reason, file, [column],
                          total, failed, severity, samples, warning_pct, fail_pct, max_samples)


def _check_impossible_age(
    csv_dir, file, birthdate_column, deathdate_column, max_age_years, rule_id, rule_name,
    business_reason, severity, warning_pct, fail_pct, max_samples, chunk_size,
    identifier_columns: tuple[str, ...] = (), reference_date: pd.Timestamp | None = None,
) -> dict:
    category = "plausibility"
    skip = _check_files_and_columns(csv_dir, [(file, birthdate_column)])
    if skip:
        return _build_result(rule_id, rule_name, category, business_reason, file, [birthdate_column],
                              None, None, severity, [], warning_pct, fail_pct, max_samples, skip)

    path = csv_dir / file
    has_deathdate = (
        deathdate_column is not None
        and _check_files_and_columns(csv_dir, [(file, deathdate_column)]) is None
    )
    usecols = list(dict.fromkeys(
        [birthdate_column, *identifier_columns] + ([deathdate_column] if has_deathdate else [])
    ))
    reference = reference_date if reference_date is not None else pd.Timestamp.now(tz="UTC")

    total = 0
    failed = 0
    samples: list[dict] = []
    try:
        for chunk in pd.read_csv(path, usecols=usecols, chunksize=chunk_size, dtype=str):
            birth = _parse_date_series(chunk[birthdate_column])
            if has_deathdate:
                death = _parse_date_series(chunk[deathdate_column])
                end_date = death.where(death.notna(), other=reference)
            else:
                end_date = pd.Series(reference, index=chunk.index)
            valid = birth.notna()
            total += int(valid.sum())
            age_years = (end_date - birth).dt.days / 365.25
            bad_mask = valid & ((age_years > max_age_years) | (age_years < 0))
            failed += int(bad_mask.sum())
            if bad_mask.any() and len(samples) < max_samples:
                bad_view = chunk[bad_mask.values].copy()
                bad_view["_computed_age_years"] = age_years[bad_mask.values].values
                for _, row in bad_view.head(max_samples - len(samples)).iterrows():
                    sample = {
                        "column": birthdate_column,
                        "birthdate": row[birthdate_column],
                        "age_years": round(float(row["_computed_age_years"]), 1),
                    }
                    for id_col in identifier_columns:
                        sample[id_col.lower()] = row.get(id_col)
                    samples.append(sample)
    except (pd.errors.ParserError, UnicodeDecodeError, OSError) as exc:
        return _build_result(rule_id, rule_name, category, business_reason, file, [birthdate_column],
                              None, None, severity, [], warning_pct, fail_pct, max_samples,
                              f"Could not read {file}: {exc}")

    return _build_result(rule_id, rule_name, category, business_reason, file, [birthdate_column],
                          total, failed, severity, samples, warning_pct, fail_pct, max_samples)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def build_data_quality_report(
    csv_dir: Path,
    settings: DataQualitySettings | None = None,
    relationships: list[RelationshipConfig] | None = None,
) -> dict:
    """Run every configured data quality rule and return the full report."""
    settings = settings or load_data_quality_settings()
    rels = relationships if relationships is not None else load_relationship_configs()

    cs = settings.chunk_size
    ms = settings.max_samples
    w, f = settings.warning_threshold_pct, settings.fail_threshold_pct
    zw, zf = ZERO_TOLERANCE_WARNING_PCT, ZERO_TOLERANCE_FAIL_PCT

    rules: list[dict] = []

    rules.append(_check_not_null(
        csv_dir, "patients.csv", "Id", "not_null:patients.csv:Id", "Patient identifier is not null",
        "completeness", "Every patient record must have a primary identifier to support downstream joins and analytics.",
        "critical", zw, zf, ms, cs))
    rules.append(_check_unique(
        csv_dir, "patients.csv", "Id", "unique:patients.csv:Id", "Patient identifier is unique",
        "uniqueness", "Duplicate patient identifiers would corrupt patient-level aggregation and readmission analysis.",
        "critical", zw, zf, ms, cs))
    rules.append(_check_not_null(
        csv_dir, "encounters.csv", "Id", "not_null:encounters.csv:Id", "Encounter identifier is not null",
        "completeness", "Every encounter record must have a primary identifier.",
        "critical", zw, zf, ms, cs, identifier_columns=("PATIENT",)))
    rules.append(_check_unique(
        csv_dir, "encounters.csv", "Id", "unique:encounters.csv:Id", "Encounter identifier is unique",
        "uniqueness", "Duplicate encounter identifiers would corrupt encounter-level analytics.",
        "critical", zw, zf, ms, cs))
    rules.append(_check_date_parses(
        csv_dir, "encounters.csv", "START", "date_parses:encounters.csv:START",
        "Encounter START parses as a date",
        "Encounter start time drives scheduling, duration, and time-series analytics.",
        "high", zw, zf, ms, cs, required=True, identifier_columns=("Id",)))
    rules.append(_check_date_parses(
        csv_dir, "encounters.csv", "STOP", "date_parses:encounters.csv:STOP",
        "Encounter STOP parses as a date when present",
        "Encounter stop time, when recorded, must be usable for duration analytics.",
        "high", zw, zf, ms, cs, required=False, identifier_columns=("Id",)))
    rules.append(_check_date_order_same_file(
        csv_dir, "encounters.csv", "START", "STOP", "date_order:encounters.csv:START:STOP",
        "Encounter STOP is not before START",
        "A stop time earlier than the start time indicates a corrupted or reversed timestamp.",
        "high", w, f, ms, cs, identifier_columns=("Id",)))
    rules.append(_check_date_parses(
        csv_dir, "patients.csv", "BIRTHDATE", "date_parses:patients.csv:BIRTHDATE",
        "Patient BIRTHDATE parses as a date",
        "Birthdate underpins age, cohort, and readmission-risk calculations.",
        "high", zw, zf, ms, cs, required=True, identifier_columns=("Id",)))
    rules.append(_check_date_order_same_file(
        csv_dir, "patients.csv", "BIRTHDATE", "DEATHDATE", "date_order:patients.csv:BIRTHDATE:DEATHDATE",
        "Patient DEATHDATE is not before BIRTHDATE",
        "A death date earlier than the birth date is biologically impossible.",
        "critical", zw, zf, ms, cs, identifier_columns=("Id",)))
    rules.append(_check_birthdate_before_encounter(
        csv_dir, "patients.csv", "Id", "BIRTHDATE", "encounters.csv", "Id", "PATIENT", "START",
        "temporal:birthdate_before_encounter_start", "Patient birth date is not after encounter start",
        "A patient cannot be encountered before they are born; this flags broken patient-encounter linkage or bad dates.",
        "high", w, f, ms, cs))
    rules.append(_check_date_order_same_file(
        csv_dir, "procedures.csv", "START", "STOP", "date_order:procedures.csv:START:STOP",
        "Procedure STOP is not before START", "A procedure cannot end before it starts.",
        "high", w, f, ms, cs, identifier_columns=("PATIENT", "ENCOUNTER")))
    rules.append(_check_date_order_same_file(
        csv_dir, "medications.csv", "START", "STOP", "date_order:medications.csv:START:STOP",
        "Medication STOP is not before START", "A medication course cannot end before it starts.",
        "high", w, f, ms, cs, identifier_columns=("PATIENT", "ENCOUNTER")))
    rules.append(_check_date_order_same_file(
        csv_dir, "careplans.csv", "START", "STOP", "date_order:careplans.csv:START:STOP",
        "Careplan STOP is not before START", "A care plan cannot end before it starts.",
        "high", w, f, ms, cs, identifier_columns=("Id", "PATIENT")))
    rules.append(_check_date_order_same_file(
        csv_dir, "devices.csv", "START", "STOP", "date_order:devices.csv:START:STOP",
        "Device STOP is not before START", "A device usage period cannot end before it starts.",
        "high", w, f, ms, cs, identifier_columns=("PATIENT", "ENCOUNTER")))

    for file, column in settings.cost_fields:
        rules.append(_check_numeric_non_negative(
            csv_dir, file, column, f"cost_non_negative:{file}:{column}", "numeric",
            "Negative costs are not economically meaningful and indicate a data or export error.",
            "high", zw, zf, ms, cs, identifier_columns=("Id",) if file == "encounters.csv" else ()))

    rules.append(_check_coverage_not_exceeds_total(
        csv_dir, "encounters.csv", "PAYER_COVERAGE", "TOTAL_CLAIM_COST",
        "numeric:encounters.csv:PAYER_COVERAGE_le_TOTAL_CLAIM_COST",
        "Encounter payer coverage does not exceed total claim cost",
        "A payer cannot cover more than the total cost of the encounter.",
        "high", w, f, ms, cs, identifier_columns=("Id",)))

    rules.append(_check_required_foreign_keys_exist(
        csv_dir, rels, "structural:required_foreign_keys_exist", "Required foreign key columns exist",
        "Downstream joins depend on expected foreign key columns being present in each file.",
        "critical", zw, zf, ms))

    for file, column in settings.critical_columns:
        rules.append(_check_not_null(
            csv_dir, file, column, f"critical_not_null:{file}:{column}",
            f"{column} in {file} does not contain unexpected nulls", "completeness",
            "This column is configured as critical for downstream analytics and must be populated.",
            "high", zw, zf, ms, cs))

    rules.append(_check_allowed_values(
        csv_dir, "encounters.csv", "ENCOUNTERCLASS", settings.allowed_encounter_classes,
        "domain:encounters.csv:ENCOUNTERCLASS", "Encounter class is within the allowed set",
        "Unrecognized encounter classes indicate schema drift or upstream data issues.",
        "medium", w, f, ms, cs, identifier_columns=("Id",)))

    for file, column in settings.uuid_columns:
        rules.append(_check_uuid_format(
            csv_dir, file, column, f"uuid_format:{file}:{column}", "format",
            "Identifier columns are expected to be UUID-formatted for reliable joins; deviations may indicate a non-standard export.",
            "low", w, f, ms, cs))

    for file, column in settings.zip_columns:
        rules.append(_check_zip_text_format(
            csv_dir, file, column, f"zip_text_format:{file}:{column}", "format",
            "ZIP codes must be treated as identifier text so leading zeros are preserved, not stripped as numbers.",
            "low", w, f, ms, cs))

    rules.append(_check_impossible_age(
        csv_dir, "patients.csv", "BIRTHDATE", "DEATHDATE", settings.max_patient_age_years,
        "plausibility:patients.csv:age", "Patient age is plausible",
        "Ages beyond a configurable maximum (or negative ages) indicate corrupted birth/death dates.",
        "medium", w, f, ms, cs, identifier_columns=("Id",)))

    return {
        "data_quality_version": DATA_QUALITY_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_directory": _relative_to_root(csv_dir),
        "summary": _summarize_rules(rules),
        "rules": rules,
    }


def _summarize_rules(rules: list[dict]) -> dict:
    counts = {"pass": 0, "warning": 0, "fail": 0, "skipped": 0}
    total_failed = 0
    for rule in rules:
        counts[rule["status"]] += 1
        if rule["records_failed"]:
            total_failed += rule["records_failed"]
    return {
        "total_rules": len(rules),
        "passed": counts["pass"],
        "warnings": counts["warning"],
        "failed": counts["fail"],
        "skipped": counts["skipped"],
        "total_records_failed": total_failed,
    }


def write_data_quality_report_json(report: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
        fh.write("\n")


def write_data_quality_summary_csv(report: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv_module.DictWriter(fh, fieldnames=DATA_QUALITY_SUMMARY_FIELDNAMES)
        writer.writeheader()
        for rule in report["rules"]:
            threshold = rule["threshold"]
            writer.writerow({
                "rule_id": rule["rule_id"],
                "rule_name": rule["rule_name"],
                "category": rule["category"],
                "source_file": rule["source_file"],
                "severity": rule["severity"],
                "status": rule["status"],
                "records_evaluated": rule["records_evaluated"],
                "records_failed": rule["records_failed"],
                "failure_percentage": rule["failure_percentage"],
                "warning_threshold_pct": threshold["warning_pct"],
                "fail_threshold_pct": threshold["fail_pct"],
                "skipped_reason": rule["skipped_reason"] or "",
            })


def build_failed_record_samples(data_quality_report: dict, relationship_summary: dict | None = None) -> dict:
    """Consolidate warning/fail sample failures from rules and relationships."""
    rule_samples = [
        {
            "rule_id": rule["rule_id"],
            "rule_name": rule["rule_name"],
            "source_file": rule["source_file"],
            "status": rule["status"],
            "sample_failures": rule["sample_failures"],
        }
        for rule in data_quality_report["rules"]
        if rule["status"] in ("warning", "fail") and rule["sample_failures"]
    ]

    relationship_samples = []
    if relationship_summary:
        relationship_samples = [
            {
                "relationship": rel["relationship"],
                "child_file": rel["child_file"],
                "child_key": rel["child_key"],
                "status": rel["status"],
                "sample_unmatched_values": rel["sample_unmatched_values"],
            }
            for rel in relationship_summary.get("relationships", [])
            if rel["status"] in ("warning", "fail") and rel["sample_unmatched_values"]
        ]

    return {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rule_failures": rule_samples,
        "relationship_failures": relationship_samples,
    }


def write_failed_record_samples_json(samples: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(samples, fh, indent=2, default=str)
        fh.write("\n")


def run_data_quality_validation(
    csv_dir: Path,
    output_dir: Path,
    relationship_summary: dict | None = None,
    settings: DataQualitySettings | None = None,
    relationships: list[RelationshipConfig] | None = None,
) -> dict:
    """Run the full data quality rule set and write all three output files."""
    report = build_data_quality_report(csv_dir, settings=settings, relationships=relationships)
    write_data_quality_report_json(report, output_dir / DATA_QUALITY_REPORT_FILENAME)
    write_data_quality_summary_csv(report, output_dir / DATA_QUALITY_SUMMARY_FILENAME)
    samples = build_failed_record_samples(report, relationship_summary)
    write_failed_record_samples_json(samples, output_dir / FAILED_RECORD_SAMPLES_FILENAME)
    return report
