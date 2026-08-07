"""Gold-layer star schema and analytics mart builder for CareFlow Analytics.

Reads only from data/silver/. Writes only to data/gold/ and
reports/profiling/. Never modifies data/raw, data/bronze, or data/silver.

Incremental: each table's Bronze->Silver->Gold dependency chain is
reduced to a signature over the underlying Silver dataset checksums; a
table is rebuilt only when that signature changes (or --force), and
rebuilding a dimension automatically triggers rebuilding every fact/mart
that (transitively) depends on it.
"""

from __future__ import annotations

import csv as csv_module
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from careflow.config import Config, load_config
from careflow.gold.kpi_calculator import build_kpi_summary
from careflow.gold.schema import (
    BUILD_ORDER,
    GOLD_SCHEMA_VERSION,
    GOLD_TRANSFORMATION_VERSION,
    KEY_STRATEGY,
    TABLE_DEPENDENCIES,
    TABLE_SCHEMAS,
    UNKNOWN_SURROGATE_KEY,
    date_key_series,
    map_foreign_key,
    surrogate_key_series,
)
from careflow.logging_config import get_logger
from careflow.profiling.file_profiler import _relative_to_root
from careflow.transformation.silver_transformer import SILVER_MANIFEST_FILENAME, _load_json

logger = get_logger(__name__)

GOLD_MANIFEST_FILENAME = "gold_manifest.json"
GOLD_QUALITY_REPORT_FILENAME = "gold_quality_report.json"
GOLD_QUALITY_SUMMARY_FILENAME = "gold_quality_summary.csv"
GOLD_KPI_SUMMARY_FILENAME = "gold_kpi_summary.json"

GOLD_QUALITY_SUMMARY_FIELDNAMES = [
    "check_id", "table", "category", "status", "records_evaluated", "records_failed", "details",
]

DEFAULT_QUALIFYING_ENCOUNTER_CLASSES: tuple[str, ...] = ("inpatient", "emergency")
DEFAULT_READMISSION_WINDOWS: tuple[int, ...] = (7, 14, 30)

DATE_DIMENSION_SOURCES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("encounters", ("start", "stop")),
    ("conditions", ("start", "stop")),
    ("procedures", ("start", "stop")),
    ("medications", ("start", "stop")),
    ("observations", ("observation_date",)),
    ("claims", ("servicedate", "currentillnessdate")),
    ("immunizations", ("immunization_date",)),
)


@dataclass(frozen=True)
class GoldSettings:
    """Configuration-driven settings for Gold mart construction."""

    readmission_qualifying_classes: tuple[str, ...]
    readmission_windows: tuple[int, ...]


def load_gold_settings(config: Config | None = None) -> GoldSettings:
    cfg = config or load_config()
    classes = cfg.get(
        "gold", "readmission", "qualifying_encounter_classes",
        default=list(DEFAULT_QUALIFYING_ENCOUNTER_CLASSES),
    )
    windows = cfg.get("gold", "readmission", "windows_days", default=list(DEFAULT_READMISSION_WINDOWS))
    return GoldSettings(
        readmission_qualifying_classes=tuple(classes),
        readmission_windows=tuple(int(w) for w in windows),
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _valid_keys(dim: pd.DataFrame, key_col: str) -> set[int]:
    return {int(k) for k in dim[key_col].dropna().tolist()}


def _composite_natural_key(df: pd.DataFrame, columns: list[str]) -> pd.Series:
    combined = df[columns[0]].astype(str)
    for col in columns[1:]:
        combined = combined + "||" + df[col].astype(str)
    return combined


def _empty_like(table: str) -> pd.DataFrame:
    return pd.DataFrame(columns=list(TABLE_SCHEMAS[table]))


# ---------------------------------------------------------------------------
# Dimension builders
# ---------------------------------------------------------------------------


def build_dim_patient(silver_dir: Path) -> tuple[pd.DataFrame, int]:
    df = pd.read_parquet(silver_dir / "patients.parquet")
    source_rows = len(df)
    out = pd.DataFrame({
        "patient_key": surrogate_key_series(df["patient_id"], "patient"),
        "patient_id": df["patient_id"],
        "birth_date": df["birthdate"],
        "death_date": df["deathdate"],
        "gender": df["gender"],
        "race": df["race"],
        "ethnicity": df["ethnicity"],
        "marital_status": df["marital"],
        "city": df["city"],
        "state": df["state"],
        "county": df["county"],
        "zip": df["zip"],
        "latitude": df["lat"],
        "longitude": df["lon"],
        "income": df["income"],
        "healthcare_expenses": df["healthcare_expenses"],
        "healthcare_coverage": df["healthcare_coverage"],
        "is_deceased": df["is_deceased"],
        "age_at_reference_date": df["age_at_reference_date"],
        "age_group": df["age_group"],
        "source_file": df["source_file"],
        "source_checksum": df["source_checksum"],
        "transformation_timestamp_utc": df["transformation_timestamp_utc"],
    })
    out = out.dropna(subset=["patient_key"]).drop_duplicates(subset=["patient_key"], keep="first").reset_index(drop=True)
    return out, source_rows


def build_dim_provider(silver_dir: Path) -> tuple[pd.DataFrame, int]:
    df = pd.read_parquet(silver_dir / "providers.parquet")
    source_rows = len(df)
    out = pd.DataFrame({
        "provider_key": surrogate_key_series(df["id"], "provider"),
        "provider_id": df["id"],
        "organization_id": df["organization_id"],
        "provider_name": df["name"],
        "gender": df["gender"],
        "speciality": df["speciality"],
        "city": df["city"],
        "state": df["state"],
        "zip": df["zip"],
    })
    out = out.dropna(subset=["provider_key"]).drop_duplicates(subset=["provider_key"], keep="first").reset_index(drop=True)
    return out, source_rows


def build_dim_organization(silver_dir: Path) -> tuple[pd.DataFrame, int]:
    df = pd.read_parquet(silver_dir / "organizations.parquet")
    source_rows = len(df)
    out = pd.DataFrame({
        "organization_key": surrogate_key_series(df["id"], "organization"),
        "organization_id": df["id"],
        "organization_name": df["name"],
        "city": df["city"],
        "state": df["state"],
        "zip": df["zip"],
        "revenue": pd.to_numeric(df["revenue"], errors="coerce"),
        "utilization": pd.to_numeric(df["utilization"], errors="coerce"),
    })
    out = out.dropna(subset=["organization_key"]).drop_duplicates(subset=["organization_key"], keep="first").reset_index(drop=True)
    return out, source_rows


def build_dim_payer(silver_dir: Path) -> tuple[pd.DataFrame, int]:
    df = pd.read_parquet(silver_dir / "payers.parquet")
    source_rows = len(df)
    out = pd.DataFrame({
        "payer_key": surrogate_key_series(df["id"], "payer"),
        "payer_id": df["id"],
        "payer_name": df["name"],
        "ownership": df["ownership"],
        "state_headquartered": df["state_headquartered"],
        "amount_covered": pd.to_numeric(df["amount_covered"], errors="coerce"),
        "amount_uncovered": pd.to_numeric(df["amount_uncovered"], errors="coerce"),
        "revenue": pd.to_numeric(df["revenue"], errors="coerce"),
        "unique_customers": pd.to_numeric(df["unique_customers"], errors="coerce"),
        "member_months": pd.to_numeric(df["member_months"], errors="coerce"),
    })
    out = out.dropna(subset=["payer_key"]).drop_duplicates(subset=["payer_key"], keep="first").reset_index(drop=True)
    return out, source_rows


def build_dim_date(silver_dir: Path) -> tuple[pd.DataFrame, int]:
    all_dates: list[pd.Series] = []
    source_rows = 0
    for dataset, columns in DATE_DIMENSION_SOURCES:
        path = silver_dir / f"{dataset}.parquet"
        if not path.is_file():
            continue
        present = [c for c in columns if True]
        try:
            df = pd.read_parquet(path)
        except Exception:  # noqa: BLE001
            continue
        source_rows += len(df)
        for col in present:
            if col not in df.columns:
                continue
            parsed = pd.to_datetime(df[col], errors="coerce", utc=True)
            non_null = parsed.dropna()
            if len(non_null):
                all_dates.append(non_null)

    if not all_dates:
        return _empty_like("dim_date"), source_rows

    combined = pd.concat(all_dates, ignore_index=True)
    min_date = combined.min().normalize()
    max_date = combined.max().normalize()

    calendar = pd.date_range(min_date, max_date, freq="D", tz="UTC")
    out = pd.DataFrame({"full_date": calendar})
    out["date_key"] = (out["full_date"].dt.year * 10000 + out["full_date"].dt.month * 100 + out["full_date"].dt.day).astype("int64")
    out["day"] = out["full_date"].dt.day.astype("int64")
    out["day_name"] = out["full_date"].dt.day_name()
    out["week_of_year"] = out["full_date"].dt.isocalendar().week.astype("int64")
    out["month"] = out["full_date"].dt.month.astype("int64")
    out["month_name"] = out["full_date"].dt.month_name()
    out["quarter"] = out["full_date"].dt.quarter.astype("int64")
    out["year"] = out["full_date"].dt.year.astype("int64")
    out["year_month"] = out["full_date"].dt.strftime("%Y-%m")
    out["is_weekend"] = out["full_date"].dt.dayofweek >= 5

    out = out[list(TABLE_SCHEMAS["dim_date"])].reset_index(drop=True)
    return out, source_rows


def _code_dimension(silver_dir: Path, source_file: str, key_col: str, namespace: str) -> tuple[pd.DataFrame, int]:
    df = pd.read_parquet(silver_dir / source_file, columns=["code", "description"])
    source_rows = len(df)
    out = df.dropna(subset=["code"]).drop_duplicates(subset=["code"], keep="first").reset_index(drop=True)
    out[key_col] = surrogate_key_series(out["code"], namespace)
    out = out[[key_col, "code", "description"]]
    return out, source_rows


def build_dim_condition(silver_dir: Path) -> tuple[pd.DataFrame, int]:
    return _code_dimension(silver_dir, "conditions.parquet", "condition_key", "condition")


def build_dim_procedure(silver_dir: Path) -> tuple[pd.DataFrame, int]:
    return _code_dimension(silver_dir, "procedures.parquet", "procedure_key", "procedure")


def build_dim_medication(silver_dir: Path) -> tuple[pd.DataFrame, int]:
    return _code_dimension(silver_dir, "medications.parquet", "medication_key", "medication")


# ---------------------------------------------------------------------------
# Fact builders
# ---------------------------------------------------------------------------


def build_fact_encounter(silver_dir: Path, built: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, int]:
    df = pd.read_parquet(silver_dir / "encounters.parquet")
    source_rows = len(df)

    patient_key, patient_missing = map_foreign_key(df["patient_id"], "patient", _valid_keys(built["dim_patient"], "patient_key"))
    provider_key, provider_missing = map_foreign_key(df["provider_id"], "provider", _valid_keys(built["dim_provider"], "provider_key"))
    organization_key, organization_missing = map_foreign_key(df["organization_id"], "organization", _valid_keys(built["dim_organization"], "organization_key"))
    payer_key, payer_missing = map_foreign_key(df["payer_id"], "payer", _valid_keys(built["dim_payer"], "payer_key"))

    patient_responsibility = df["total_claim_cost"] - df["payer_coverage"]
    negative_flag = patient_responsibility < 0

    out = pd.DataFrame({
        "encounter_key": surrogate_key_series(df["encounter_id"], "encounter"),
        "encounter_id": df["encounter_id"],
        "patient_key": patient_key,
        "patient_key_is_missing": patient_missing,
        "provider_key": provider_key,
        "provider_key_is_missing": provider_missing,
        "organization_key": organization_key,
        "organization_key_is_missing": organization_missing,
        "payer_key": payer_key,
        "payer_key_is_missing": payer_missing,
        "encounter_date_key": date_key_series(df["start"]),
        "start_timestamp": df["start"],
        "stop_timestamp": df["stop"],
        "encounter_class": df["encounter_class"],
        "encounter_duration_minutes": df["encounter_duration_minutes"],
        "base_encounter_cost": df["base_encounter_cost"],
        "total_claim_cost": df["total_claim_cost"],
        "payer_coverage": df["payer_coverage"],
        "patient_responsibility": patient_responsibility,
        "patient_responsibility_is_negative": negative_flag,
        "is_inpatient": df["is_inpatient"],
        "is_emergency": df["is_emergency"],
        "reason_code": df["reasoncode"],
        "reason_description": df["reasondescription"],
    })
    out = out.dropna(subset=["encounter_key"]).drop_duplicates(subset=["encounter_key"], keep="first").reset_index(drop=True)
    return out, source_rows


def build_fact_condition(silver_dir: Path, built: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, int]:
    df = pd.read_parquet(silver_dir / "conditions.parquet")
    source_rows = len(df)

    patient_key, patient_missing = map_foreign_key(df["patient_id"], "patient", _valid_keys(built["dim_patient"], "patient_key"))
    condition_key, condition_missing = map_foreign_key(df["code"], "condition", _valid_keys(built["dim_condition"], "condition_key"))
    encounter_key = surrogate_key_series(df["encounter_id"], "encounter")
    natural = _composite_natural_key(df, ["patient_id", "encounter_id", "code", "start"])

    out = pd.DataFrame({
        "condition_event_key": surrogate_key_series(natural, "condition_event"),
        "patient_key": patient_key,
        "patient_key_is_missing": patient_missing,
        "encounter_key": encounter_key,
        "condition_key": condition_key,
        "condition_key_is_missing": condition_missing,
        "start_date_key": date_key_series(df["start"]),
        "stop_date_key": date_key_series(df["stop"]),
        "is_active": df["is_active"],
        "condition_duration_days": df["condition_duration_days"],
    })
    out = out.dropna(subset=["condition_event_key"]).drop_duplicates(subset=["condition_event_key"], keep="first").reset_index(drop=True)
    return out, source_rows


def build_fact_procedure(silver_dir: Path, built: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, int]:
    df = pd.read_parquet(silver_dir / "procedures.parquet")
    source_rows = len(df)

    patient_key, patient_missing = map_foreign_key(df["patient_id"], "patient", _valid_keys(built["dim_patient"], "patient_key"))
    procedure_key, procedure_missing = map_foreign_key(df["code"], "procedure", _valid_keys(built["dim_procedure"], "procedure_key"))
    encounter_key = surrogate_key_series(df["encounter_id"], "encounter")
    natural = _composite_natural_key(df, ["patient_id", "encounter_id", "code", "start"])

    out = pd.DataFrame({
        "procedure_event_key": surrogate_key_series(natural, "procedure_event"),
        "patient_key": patient_key,
        "patient_key_is_missing": patient_missing,
        "encounter_key": encounter_key,
        "procedure_key": procedure_key,
        "procedure_key_is_missing": procedure_missing,
        "start_date_key": date_key_series(df["start"]),
        "stop_date_key": date_key_series(df["stop"]),
        "procedure_duration_minutes": df["procedure_duration_minutes"],
        "base_cost": df["base_cost"],
        "reason_code": df["reasoncode"],
        "reason_description": df["reasondescription"],
    })
    out = out.dropna(subset=["procedure_event_key"]).drop_duplicates(subset=["procedure_event_key"], keep="first").reset_index(drop=True)
    return out, source_rows


def build_fact_medication(silver_dir: Path, built: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, int]:
    df = pd.read_parquet(silver_dir / "medications.parquet")
    source_rows = len(df)

    patient_key, patient_missing = map_foreign_key(df["patient_id"], "patient", _valid_keys(built["dim_patient"], "patient_key"))
    payer_key, payer_missing = map_foreign_key(df["payer_id"], "payer", _valid_keys(built["dim_payer"], "payer_key"))
    medication_key, medication_missing = map_foreign_key(df["code"], "medication", _valid_keys(built["dim_medication"], "medication_key"))
    encounter_key = surrogate_key_series(df["encounter_id"], "encounter")
    natural = _composite_natural_key(df, ["patient_id", "encounter_id", "code", "start"])

    out = pd.DataFrame({
        "medication_event_key": surrogate_key_series(natural, "medication_event"),
        "patient_key": patient_key,
        "patient_key_is_missing": patient_missing,
        "encounter_key": encounter_key,
        "payer_key": payer_key,
        "payer_key_is_missing": payer_missing,
        "medication_key": medication_key,
        "medication_key_is_missing": medication_missing,
        "start_date_key": date_key_series(df["start"]),
        "stop_date_key": date_key_series(df["stop"]),
        "base_cost": df["base_cost"],
        "payer_coverage": df["payer_coverage"],
        "total_cost": df["total_cost"],
        "dispenses": df["dispenses"],
        "medication_duration_days": df["medication_duration_days"],
        "is_active": df["is_active"],
    })
    out = out.dropna(subset=["medication_event_key"]).drop_duplicates(subset=["medication_event_key"], keep="first").reset_index(drop=True)
    return out, source_rows


def build_fact_observation(silver_dir: Path, built: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, int]:
    df = pd.read_parquet(silver_dir / "observations.parquet")
    source_rows = len(df)

    patient_key, patient_missing = map_foreign_key(df["patient_id"], "patient", _valid_keys(built["dim_patient"], "patient_key"))
    encounter_key = surrogate_key_series(df["encounter_id"], "encounter")
    natural = _composite_natural_key(df, ["patient_id", "encounter_id", "code", "observation_date", "value"])

    out = pd.DataFrame({
        "observation_key": surrogate_key_series(natural, "observation"),
        "patient_key": patient_key,
        "patient_key_is_missing": patient_missing,
        "encounter_key": encounter_key,
        "observation_date_key": date_key_series(df["observation_date"]),
        "category": df["category"],
        "observation_code": df["code"],
        "description": df["description"],
        "raw_value": df["value"],
        "numeric_value": df["numeric_value"],
        "units": df["units"],
        "observation_type": df["type"],
    })
    out = out.dropna(subset=["observation_key"]).drop_duplicates(subset=["observation_key"], keep="first").reset_index(drop=True)
    return out, source_rows


def build_fact_claim(silver_dir: Path, built: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, int]:
    """Build fact_claim from claims.csv only.

    ``providerid`` and ``primarypatientinsuranceid`` are used for
    provider/payer resolution because they reliably match dim_provider /
    dim_payer natural keys; no relationship is inferred for columns that
    don't have a verified match.
    """
    df = pd.read_parquet(silver_dir / "claims.parquet")
    source_rows = len(df)

    patient_key, patient_missing = map_foreign_key(df["patient_id"], "patient", _valid_keys(built["dim_patient"], "patient_key"))
    provider_key, provider_missing = map_foreign_key(df["providerid"], "provider", _valid_keys(built["dim_provider"], "provider_key"))
    payer_key, payer_missing = map_foreign_key(df["primarypatientinsuranceid"], "payer", _valid_keys(built["dim_payer"], "payer_key"))
    encounter_key = surrogate_key_series(df["encounter_id"], "encounter")

    out = pd.DataFrame({
        "claim_key": surrogate_key_series(df["id"], "claim"),
        "claim_id": df["id"],
        "patient_key": patient_key,
        "patient_key_is_missing": patient_missing,
        "encounter_key": encounter_key,
        "provider_key": provider_key,
        "provider_key_is_missing": provider_missing,
        "payer_key": payer_key,
        "payer_key_is_missing": payer_missing,
        "service_date_key": date_key_series(df["servicedate"]),
        "claim_status": df["status1"],
        "outstanding_amount": pd.to_numeric(df["outstanding1"], errors="coerce"),
        "claim_type": df["healthcareclaimtypeid1"],
    })
    out = out.dropna(subset=["claim_key"]).drop_duplicates(subset=["claim_key"], keep="first").reset_index(drop=True)
    return out, source_rows


def build_fact_immunization(silver_dir: Path, built: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, int]:
    df = pd.read_parquet(silver_dir / "immunizations.parquet")
    source_rows = len(df)

    patient_key, patient_missing = map_foreign_key(df["patient_id"], "patient", _valid_keys(built["dim_patient"], "patient_key"))
    encounter_key = surrogate_key_series(df["encounter_id"], "encounter")
    natural = _composite_natural_key(df, ["patient_id", "encounter_id", "code", "immunization_date"])

    out = pd.DataFrame({
        "immunization_key": surrogate_key_series(natural, "immunization"),
        "patient_key": patient_key,
        "patient_key_is_missing": patient_missing,
        "encounter_key": encounter_key,
        "immunization_date_key": date_key_series(df["immunization_date"]),
        "code": df["code"],
        "description": df["description"],
        "base_cost": df["base_cost"],
    })
    out = out.dropna(subset=["immunization_key"]).drop_duplicates(subset=["immunization_key"], keep="first").reset_index(drop=True)
    return out, source_rows


def imaging_study_composite_key(df: pd.DataFrame) -> pd.Series:
    """The imaging_studies grain, made explicit.

    Phase 2F's Silver quality checks found that ``imaging_studies.Id``
    (the DICOM *study* id) is not row-level unique: one study can have
    multiple series/instance rows. The true row grain requires
    study id + series_uid + instance_uid together.
    """
    return _composite_natural_key(df, ["id", "series_uid", "instance_uid"])


def build_fact_imaging_study(silver_dir: Path, built: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, int]:
    df = pd.read_parquet(silver_dir / "imaging_studies.parquet")
    source_rows = len(df)

    patient_key, patient_missing = map_foreign_key(df["patient_id"], "patient", _valid_keys(built["dim_patient"], "patient_key"))
    encounter_key = surrogate_key_series(df["encounter_id"], "encounter")
    natural = imaging_study_composite_key(df)

    out = pd.DataFrame({
        "imaging_study_key": surrogate_key_series(natural, "imaging_study"),
        "study_id": df["id"],
        "series_uid": df["series_uid"],
        "instance_uid": df["instance_uid"],
        "patient_key": patient_key,
        "patient_key_is_missing": patient_missing,
        "encounter_key": encounter_key,
        "study_date_key": date_key_series(df["date"]),
        "bodysite_code": df["bodysite_code"],
        "modality_code": df["modality_code"],
        "sop_code": df["sop_code"],
        "procedure_code": df["procedure_code"],
    })
    out = out.dropna(subset=["imaging_study_key"]).drop_duplicates(subset=["imaging_study_key"], keep="first").reset_index(drop=True)
    return out, source_rows


# ---------------------------------------------------------------------------
# Analytics marts
# ---------------------------------------------------------------------------


def build_mart_patient_360(built: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, int]:
    dim_patient = built["dim_patient"]
    fact_encounter = built["fact_encounter"]
    fact_condition = built["fact_condition"]
    fact_procedure = built["fact_procedure"]
    fact_medication = built["fact_medication"]
    fact_observation = built["fact_observation"]
    fact_immunization = built["fact_immunization"]

    out = dim_patient[["patient_key", "patient_id", "gender", "race", "ethnicity", "age_group", "is_deceased"]].copy()

    if len(fact_encounter):
        enc_agg = fact_encounter.groupby("patient_key").agg(
            total_encounters=("encounter_key", "count"),
            inpatient_encounters=("is_inpatient", "sum"),
            emergency_encounters=("is_emergency", "sum"),
            first_encounter_date=("start_timestamp", "min"),
            last_encounter_date=("start_timestamp", "max"),
            total_claim_cost=("total_claim_cost", "sum"),
            total_payer_coverage=("payer_coverage", "sum"),
            total_patient_responsibility=("patient_responsibility", "sum"),
            average_encounter_duration_minutes=("encounter_duration_minutes", "mean"),
        ).reset_index()
        most_recent = (
            fact_encounter.dropna(subset=["start_timestamp"])
            .sort_values("start_timestamp")
            .groupby("patient_key")
            .tail(1)[["patient_key", "encounter_class"]]
            .rename(columns={"encounter_class": "most_recent_encounter_class"})
        )
        out = out.merge(enc_agg, on="patient_key", how="left").merge(most_recent, on="patient_key", how="left")

    for fact_df, key_col, count_name in (
        (fact_condition, "condition_event_key", "total_conditions"),
        (fact_procedure, "procedure_event_key", "total_procedures"),
        (fact_medication, "medication_event_key", "total_medications"),
        (fact_observation, "observation_key", "total_observations"),
        (fact_immunization, "immunization_key", "total_immunizations"),
    ):
        if len(fact_df):
            agg = fact_df.groupby("patient_key").agg(**{count_name: (key_col, "count")}).reset_index()
            out = out.merge(agg, on="patient_key", how="left")

    if len(fact_condition):
        active = fact_condition.groupby("patient_key").agg(active_conditions=("is_active", "sum")).reset_index()
        out = out.merge(active, on="patient_key", how="left")

    count_cols = [
        "total_encounters", "inpatient_encounters", "emergency_encounters", "total_conditions",
        "active_conditions", "total_procedures", "total_medications", "total_observations",
        "total_immunizations",
    ]
    for col in count_cols:
        if col not in out.columns:
            out[col] = 0
        out[col] = out[col].fillna(0).astype("int64")

    money_cols = ["total_claim_cost", "total_payer_coverage", "total_patient_responsibility"]
    for col in money_cols:
        if col not in out.columns:
            out[col] = 0.0
        out[col] = out[col].fillna(0.0)

    for col in ("first_encounter_date", "last_encounter_date", "average_encounter_duration_minutes", "most_recent_encounter_class"):
        if col not in out.columns:
            out[col] = pd.NA

    out = out[list(TABLE_SCHEMAS["mart_patient_360"])].reset_index(drop=True)
    return out, len(dim_patient)


def build_mart_readmission(built: dict[str, pd.DataFrame], settings: GoldSettings) -> tuple[pd.DataFrame, int]:
    fact_encounter = built["fact_encounter"]
    source_rows = len(fact_encounter)

    qualifying = fact_encounter[
        fact_encounter["encounter_class"].isin(settings.readmission_qualifying_classes)
    ].dropna(subset=["start_timestamp"]).copy()

    if qualifying.empty:
        return _empty_like("mart_readmission"), source_rows

    qualifying = qualifying.sort_values(["patient_key", "start_timestamp"]).reset_index(drop=True)

    grouped = qualifying.groupby("patient_key")
    qualifying["next_encounter_key"] = grouped["encounter_key"].shift(-1)
    qualifying["next_encounter_timestamp"] = grouped["start_timestamp"].shift(-1)
    qualifying["next_encounter_class"] = grouped["encounter_class"].shift(-1)

    qualifying["index_discharge_timestamp"] = qualifying["stop_timestamp"].where(
        qualifying["stop_timestamp"].notna(), qualifying["start_timestamp"]
    )

    has_next = qualifying["next_encounter_timestamp"].notna()
    days = (qualifying["next_encounter_timestamp"] - qualifying["index_discharge_timestamp"]).dt.total_seconds() / 86400.0
    # A negative value means the next qualifying encounter started before
    # the index encounter's recorded discharge (overlapping/back-to-back
    # encounters in the source data) -- "days to readmission" is not a
    # meaningful concept there, so it is left null rather than negative.
    qualifying["days_to_readmission"] = days.where(has_next & (days >= 0))

    for window in settings.readmission_windows:
        qualifying[f"readmitted_within_{window}_days"] = (
            has_next & (qualifying["days_to_readmission"] >= 0) & (qualifying["days_to_readmission"] <= window)
        )

    out = qualifying.rename(columns={"encounter_key": "index_encounter_key", "encounter_class": "index_encounter_class"})
    out["next_encounter_key"] = out["next_encounter_key"].astype("Int64")

    for window in DEFAULT_READMISSION_WINDOWS:
        col = f"readmitted_within_{window}_days"
        if col not in out.columns:
            out[col] = False

    out = out[list(TABLE_SCHEMAS["mart_readmission"])].reset_index(drop=True)
    return out, source_rows


def build_mart_hospital_operations(built: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, int]:
    fact_encounter = built["fact_encounter"]
    source_rows = len(fact_encounter)
    if fact_encounter.empty:
        return _empty_like("mart_hospital_operations"), source_rows

    grouped = fact_encounter.groupby(["organization_key", "encounter_date_key", "encounter_class"], dropna=False)
    out = grouped.agg(
        encounter_count=("encounter_key", "count"),
        unique_patients=("patient_key", "nunique"),
        average_duration_minutes=("encounter_duration_minutes", "mean"),
        median_duration_minutes=("encounter_duration_minutes", "median"),
        inpatient_count=("is_inpatient", "sum"),
        emergency_count=("is_emergency", "sum"),
        total_claim_cost=("total_claim_cost", "sum"),
        payer_coverage=("payer_coverage", "sum"),
        patient_responsibility=("patient_responsibility", "sum"),
        provider_count=("provider_key", "nunique"),
    ).reset_index()

    out = out[list(TABLE_SCHEMAS["mart_hospital_operations"])].reset_index(drop=True)
    return out, source_rows


def build_mart_financial_performance(built: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, int]:
    fact_encounter = built["fact_encounter"].copy()
    source_rows = len(fact_encounter)
    if fact_encounter.empty:
        return _empty_like("mart_financial_performance"), source_rows

    fact_encounter["year_month"] = fact_encounter["start_timestamp"].dt.strftime("%Y-%m")
    grouped = fact_encounter.groupby(["payer_key", "organization_key", "year_month"], dropna=False)
    out = grouped.agg(
        encounter_count=("encounter_key", "count"),
        total_claim_cost=("total_claim_cost", "sum"),
        total_payer_coverage=("payer_coverage", "sum"),
        total_patient_responsibility=("patient_responsibility", "sum"),
    ).reset_index()

    out["average_claim_cost"] = out["total_claim_cost"] / out["encounter_count"].replace(0, pd.NA)
    out["coverage_ratio"] = out["total_payer_coverage"] / out["total_claim_cost"].replace(0, pd.NA)

    out = out[list(TABLE_SCHEMAS["mart_financial_performance"])].reset_index(drop=True)
    return out, source_rows


def build_mart_provider_utilization(built: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, int]:
    fact_encounter = built["fact_encounter"].copy()
    source_rows = len(fact_encounter)
    if fact_encounter.empty:
        return _empty_like("mart_provider_utilization"), source_rows

    fact_encounter["year_month"] = fact_encounter["start_timestamp"].dt.strftime("%Y-%m")
    enc_grouped = fact_encounter.groupby(["provider_key", "year_month"], dropna=False).agg(
        encounter_count=("encounter_key", "count"),
        unique_patients=("patient_key", "nunique"),
        average_encounter_duration_minutes=("encounter_duration_minutes", "mean"),
        total_claim_cost=("total_claim_cost", "sum"),
    ).reset_index()

    fact_procedure = built["fact_procedure"]
    if len(fact_procedure):
        enc_lookup = fact_encounter[["encounter_key", "provider_key", "year_month"]]
        proc_with_provider = fact_procedure.merge(enc_lookup, on="encounter_key", how="inner")
        proc_grouped = proc_with_provider.groupby(["provider_key", "year_month"], dropna=False).agg(
            total_procedures=("procedure_event_key", "count"),
        ).reset_index()
        out = enc_grouped.merge(proc_grouped, on=["provider_key", "year_month"], how="left")
    else:
        out = enc_grouped.copy()
        out["total_procedures"] = 0

    out["total_procedures"] = out["total_procedures"].fillna(0).astype("int64")
    out = out[list(TABLE_SCHEMAS["mart_provider_utilization"])].reset_index(drop=True)
    return out, source_rows


def build_mart_monthly_kpis(built: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, int]:
    fact_encounter = built["fact_encounter"].copy()
    source_rows = len(fact_encounter)
    if fact_encounter.empty:
        return _empty_like("mart_monthly_kpis"), source_rows

    fact_encounter["year_month"] = fact_encounter["start_timestamp"].dt.strftime("%Y-%m")
    out = fact_encounter.groupby("year_month").agg(
        total_patients_served=("patient_key", "nunique"),
        total_encounters=("encounter_key", "count"),
        inpatient_encounters=("is_inpatient", "sum"),
        emergency_encounters=("is_emergency", "sum"),
        average_length_of_stay_minutes=("encounter_duration_minutes", "mean"),
        total_claim_cost=("total_claim_cost", "sum"),
        total_payer_coverage=("payer_coverage", "sum"),
        total_patient_responsibility=("patient_responsibility", "sum"),
    ).reset_index()

    month_lookup = fact_encounter[["encounter_key", "year_month"]]

    fact_procedure = built["fact_procedure"]
    if len(fact_procedure):
        proc_month = fact_procedure.merge(month_lookup, on="encounter_key", how="inner")
        proc_counts = proc_month.groupby("year_month").size().rename("procedure_count")
        out = out.merge(proc_counts, on="year_month", how="left")
    else:
        out["procedure_count"] = 0
    out["procedure_count"] = out["procedure_count"].fillna(0)
    out["average_procedures_per_encounter"] = out["procedure_count"] / out["total_encounters"].replace(0, pd.NA)

    fact_medication = built["fact_medication"]
    if len(fact_medication):
        med_month = fact_medication.merge(month_lookup, on="encounter_key", how="inner")
        med_stats = med_month.groupby("year_month").agg(
            medication_count=("medication_event_key", "count"),
            medication_patient_count=("patient_key", "nunique"),
        )
        out = out.merge(med_stats, on="year_month", how="left")
    else:
        out["medication_count"] = 0
        out["medication_patient_count"] = 0
    out["medication_count"] = out["medication_count"].fillna(0)
    out["medication_patient_count"] = out["medication_patient_count"].fillna(0)
    out["average_medications_per_patient"] = out["medication_count"] / out["medication_patient_count"].replace(0, pd.NA)

    mart_readmission = built.get("mart_readmission", _empty_like("mart_readmission"))
    if len(mart_readmission):
        readmission_month = mart_readmission.merge(
            month_lookup.rename(columns={"encounter_key": "index_encounter_key"}), on="index_encounter_key", how="left",
        )
        readmission_stats = readmission_month.groupby("year_month").agg(
            readmission_count=("readmitted_within_30_days", "sum"),
            qualifying_count=("index_encounter_key", "count"),
        )
        out = out.merge(readmission_stats, on="year_month", how="left")
    else:
        out["readmission_count"] = 0
        out["qualifying_count"] = 0
    out["readmission_count"] = out["readmission_count"].fillna(0).astype("int64")
    out["readmission_rate_30_day"] = out["readmission_count"] / out["qualifying_count"].replace(0, pd.NA)

    out = out[list(TABLE_SCHEMAS["mart_monthly_kpis"])].reset_index(drop=True)
    return out, source_rows


# ---------------------------------------------------------------------------
# Build dispatch, dependency resolution, and incremental orchestration
# ---------------------------------------------------------------------------

BUILDERS: dict[str, Callable[[Path, dict[str, pd.DataFrame], GoldSettings], tuple[pd.DataFrame, int]]] = {
    "dim_patient": lambda silver_dir, built, settings: build_dim_patient(silver_dir),
    "dim_provider": lambda silver_dir, built, settings: build_dim_provider(silver_dir),
    "dim_organization": lambda silver_dir, built, settings: build_dim_organization(silver_dir),
    "dim_payer": lambda silver_dir, built, settings: build_dim_payer(silver_dir),
    "dim_date": lambda silver_dir, built, settings: build_dim_date(silver_dir),
    "dim_condition": lambda silver_dir, built, settings: build_dim_condition(silver_dir),
    "dim_procedure": lambda silver_dir, built, settings: build_dim_procedure(silver_dir),
    "dim_medication": lambda silver_dir, built, settings: build_dim_medication(silver_dir),
    "fact_encounter": lambda silver_dir, built, settings: build_fact_encounter(silver_dir, built),
    "fact_condition": lambda silver_dir, built, settings: build_fact_condition(silver_dir, built),
    "fact_procedure": lambda silver_dir, built, settings: build_fact_procedure(silver_dir, built),
    "fact_medication": lambda silver_dir, built, settings: build_fact_medication(silver_dir, built),
    "fact_observation": lambda silver_dir, built, settings: build_fact_observation(silver_dir, built),
    "fact_claim": lambda silver_dir, built, settings: build_fact_claim(silver_dir, built),
    "fact_immunization": lambda silver_dir, built, settings: build_fact_immunization(silver_dir, built),
    "fact_imaging_study": lambda silver_dir, built, settings: build_fact_imaging_study(silver_dir, built),
    "mart_patient_360": lambda silver_dir, built, settings: build_mart_patient_360(built),
    "mart_readmission": lambda silver_dir, built, settings: build_mart_readmission(built, settings),
    "mart_hospital_operations": lambda silver_dir, built, settings: build_mart_hospital_operations(built),
    "mart_financial_performance": lambda silver_dir, built, settings: build_mart_financial_performance(built),
    "mart_provider_utilization": lambda silver_dir, built, settings: build_mart_provider_utilization(built),
    "mart_monthly_kpis": lambda silver_dir, built, settings: build_mart_monthly_kpis(built),
}


def _resolve_silver_deps(table: str, seen: set[str] | None = None) -> set[str]:
    seen = seen if seen is not None else set()
    if table in seen:
        return set()
    seen.add(table)
    resolved: set[str] = set()
    for dep in TABLE_DEPENDENCIES.get(table, ()):
        if dep in TABLE_DEPENDENCIES:
            resolved |= _resolve_silver_deps(dep, seen)
        else:
            resolved.add(dep)
    return resolved


def _dependency_signature(table: str, silver_checksums: dict[str, str]) -> str:
    deps = sorted(_resolve_silver_deps(table))
    parts = [f"{d}:{silver_checksums.get(d, 'MISSING')}" for d in deps]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _expand_scope(requested: set[str]) -> set[str]:
    expanded = set(requested)
    frontier = list(requested)
    while frontier:
        current = frontier.pop()
        for dep in TABLE_DEPENDENCIES.get(current, ()):
            if dep in TABLE_DEPENDENCIES and dep not in expanded:
                expanded.add(dep)
                frontier.append(dep)
    return expanded


def _gold_entry(
    table: str, status: str, source_checksum: str | None = None,
    dependencies: list[str] | None = None, target_path: str | None = None,
    source_rows: int | None = None, target_rows: int | None = None, error: str | None = None,
) -> dict:
    return {
        "table": table,
        "status": status,
        "source_checksum": source_checksum,
        "dependencies": dependencies or [],
        "target_path": target_path,
        "source_rows": source_rows,
        "target_rows": target_rows,
        "schema_version": GOLD_SCHEMA_VERSION,
        "transformation_version": GOLD_TRANSFORMATION_VERSION,
        "key_strategy": KEY_STRATEGY,
        "error": error,
    }


def _summarize(entries: list[dict]) -> dict:
    counts = {"processed": 0, "skipped": 0, "failed": 0}
    for entry in entries:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    return {
        "total_tables": len(entries),
        "processed": counts.get("processed", 0),
        "skipped": counts.get("skipped", 0),
        "failed": counts.get("failed", 0),
    }


def run_gold_build(
    silver_dir: Path,
    gold_dir: Path,
    tables: list[str] | None = None,
    marts: list[str] | None = None,
    force: bool = False,
    settings: GoldSettings | None = None,
    silver_manifest: dict | None = None,
    previous_manifest: dict | None = None,
) -> dict:
    """Build (or incrementally refresh) the Gold layer manifest.

    A table's dependency signature combines the Bronze->Silver checksums
    of every Silver dataset it transitively depends on (resolving through
    any dimension/fact dependencies). Unchanged signature -> skipped;
    changed, or ``force``, -> rebuilt. Rebuilding a dimension is reflected
    in the signature of every fact/mart that depends on it, so they are
    rebuilt too.
    """
    settings = settings or load_gold_settings()
    run_started_at = datetime.now(timezone.utc)
    run_id = f"gold_{run_started_at.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"

    silver_manifest_path = silver_dir / SILVER_MANIFEST_FILENAME
    if silver_manifest is None:
        silver_manifest = _load_json(silver_manifest_path)
    if silver_manifest is None:
        raise FileNotFoundError(f"Silver manifest not found: {silver_manifest_path}")

    silver_checksums = {e["dataset"]: e.get("source_checksum") for e in silver_manifest.get("datasets", [])}

    gold_manifest_path = gold_dir / GOLD_MANIFEST_FILENAME
    if previous_manifest is None:
        previous_manifest = _load_json(gold_manifest_path)
    previous_entries = {e["table"]: e for e in (previous_manifest or {}).get("tables", [])}

    requested = set(tables or []) | set(marts or [])
    if requested:
        unknown = requested - set(BUILD_ORDER)
        if unknown:
            raise ValueError(f"Unknown table/mart(s) requested: {sorted(unknown)}")
        in_scope = _expand_scope(requested)
    else:
        in_scope = set(BUILD_ORDER)

    built: dict[str, pd.DataFrame] = {}
    result_entries: list[dict] = []

    for table in BUILD_ORDER:
        if table not in in_scope:
            if table in previous_entries:
                result_entries.append(previous_entries[table])
            continue

        signature = _dependency_signature(table, silver_checksums)
        target_path = gold_dir / f"{table}.parquet"
        previous_entry = previous_entries.get(table)
        dependencies = sorted(TABLE_DEPENDENCIES.get(table, ()))

        if (
            not force
            and previous_entry is not None
            and previous_entry.get("status") in ("processed", "skipped")
            and previous_entry.get("source_checksum") == signature
            and target_path.is_file()
        ):
            try:
                df = pd.read_parquet(target_path)
            except Exception:  # noqa: BLE001
                df = None
            if df is not None:
                built[table] = df
                entry = dict(previous_entry)
                entry["status"] = "skipped"
                entry["target_rows"] = len(df)
                result_entries.append(entry)
                logger.info("Gold skip %s (dependency signature unchanged)", table)
                continue

        try:
            builder = BUILDERS[table]
            df, source_rows = builder(silver_dir, built, settings)
        except Exception as exc:  # noqa: BLE001 - never crash the whole run on one table
            result_entries.append(_gold_entry(table, "failed", signature, dependencies, error=str(exc)))
            logger.warning("Gold %s -> failed: %s", table, exc)
            continue

        gold_dir.mkdir(parents=True, exist_ok=True)
        try:
            df.to_parquet(target_path, engine="pyarrow", index=False)
        except Exception as exc:  # noqa: BLE001
            target_path.unlink(missing_ok=True)
            result_entries.append(_gold_entry(table, "failed", signature, dependencies, source_rows=source_rows, error=f"Could not write: {exc}"))
            continue

        built[table] = df
        result_entries.append(_gold_entry(
            table, "processed", signature, dependencies,
            target_path=_relative_to_root(target_path), source_rows=source_rows, target_rows=len(df),
        ))
        logger.info("Gold %s -> processed (%d rows)", table, len(df))

    completed_at = datetime.now(timezone.utc)
    return {
        "run_id": run_id,
        "started_at_utc": run_started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "completed_at_utc": completed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "silver_manifest_path": _relative_to_root(silver_manifest_path),
        "schema_version": GOLD_SCHEMA_VERSION,
        "transformation_version": GOLD_TRANSFORMATION_VERSION,
        "key_strategy": KEY_STRATEGY,
        "tables": result_entries,
        "summary": _summarize(result_entries),
    }


def write_gold_manifest_json(manifest: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, default=str)
        fh.write("\n")


# ---------------------------------------------------------------------------
# Quality checks
# ---------------------------------------------------------------------------

DIM_KEY_COLUMNS: dict[str, str] = {
    "dim_patient": "patient_key",
    "dim_provider": "provider_key",
    "dim_organization": "organization_key",
    "dim_payer": "payer_key",
    "dim_date": "date_key",
    "dim_condition": "condition_key",
    "dim_procedure": "procedure_key",
    "dim_medication": "medication_key",
}

FACT_KEY_COLUMNS: dict[str, str] = {
    "fact_encounter": "encounter_key",
    "fact_condition": "condition_event_key",
    "fact_procedure": "procedure_event_key",
    "fact_medication": "medication_event_key",
    "fact_observation": "observation_key",
    "fact_claim": "claim_key",
    "fact_immunization": "immunization_key",
    "fact_imaging_study": "imaging_study_key",
}

FACT_DATE_KEY_COLUMNS: dict[str, tuple[str, ...]] = {
    "fact_encounter": ("encounter_date_key",),
    "fact_condition": ("start_date_key", "stop_date_key"),
    "fact_procedure": ("start_date_key", "stop_date_key"),
    "fact_medication": ("start_date_key", "stop_date_key"),
    "fact_observation": ("observation_date_key",),
    "fact_claim": ("service_date_key",),
    "fact_immunization": ("immunization_date_key",),
    "fact_imaging_study": ("study_date_key",),
}


def _quality_check(
    check_id: str, table: str, category: str, status: str, details: str,
    records_evaluated: int | None = None, records_failed: int | None = None,
) -> dict:
    return {
        "check_id": check_id,
        "table": table,
        "category": category,
        "status": status,
        "details": details,
        "records_evaluated": records_evaluated,
        "records_failed": records_failed,
    }


def build_gold_quality_report(gold_dir: Path, manifest: dict) -> dict:
    """Run structural, key, referential, and reconciliation checks over Gold output."""
    checks: list[dict] = []
    entries = {e["table"]: e for e in manifest["tables"]}
    frames: dict[str, pd.DataFrame] = {}

    for table in BUILD_ORDER:
        entry = entries.get(table)
        if entry is None or entry["status"] not in ("processed", "skipped"):
            continue
        path = gold_dir / f"{table}.parquet"
        if not path.is_file():
            checks.append(_quality_check(f"file_exists:{table}", table, "structural", "fail", "Gold file missing", 0, 0))
            continue
        try:
            df = pd.read_parquet(path)
        except Exception as exc:  # noqa: BLE001
            checks.append(_quality_check(f"file_readable:{table}", table, "structural", "fail", str(exc), 0, 0))
            continue
        frames[table] = df
        n = len(df)

        expected = TABLE_SCHEMAS.get(table, ())
        missing_cols = [c for c in expected if c not in df.columns]
        checks.append(_quality_check(
            f"schema_columns_exist:{table}", table, "structural",
            "pass" if not missing_cols else "fail",
            "All expected columns present" if not missing_cols else f"Missing columns: {missing_cols}",
            len(expected), len(missing_cols),
        ))

        if table in DIM_KEY_COLUMNS:
            key_col = DIM_KEY_COLUMNS[table]
            null_count = int(df[key_col].isna().sum())
            checks.append(_quality_check(
                f"surrogate_key_not_null:{table}", table, "completeness",
                "pass" if null_count == 0 else "fail", f"{null_count} null surrogate key(s)", n, null_count,
            ))
            dup_count = int(df[key_col].dropna().duplicated(keep=False).sum())
            checks.append(_quality_check(
                f"surrogate_key_unique:{table}", table, "uniqueness",
                "pass" if dup_count == 0 else "fail", f"{dup_count} duplicate surrogate key row(s)", n, dup_count,
            ))

        if table in FACT_KEY_COLUMNS:
            key_col = FACT_KEY_COLUMNS[table]
            null_count = int(df[key_col].isna().sum())
            checks.append(_quality_check(
                f"fact_key_not_null:{table}", table, "completeness",
                "pass" if null_count == 0 else "fail", f"{null_count} null fact key(s)", n, null_count,
            ))
            dup_count = int(df[key_col].dropna().duplicated(keep=False).sum())
            checks.append(_quality_check(
                f"fact_key_unique:{table}", table, "uniqueness",
                "pass" if dup_count == 0 else "warning", f"{dup_count} duplicate fact key row(s)", n, dup_count,
            ))

        for col in df.columns:
            if col.endswith("_is_missing"):
                missing_count = int(df[col].sum())
                checks.append(_quality_check(
                    f"foreign_key_flagged:{table}:{col}", table, "referential",
                    "pass" if missing_count == 0 else "warning",
                    f"{missing_count} row(s) flagged for unresolved foreign key ({col})", n, missing_count,
                ))

        if table in FACT_DATE_KEY_COLUMNS and "dim_date" in frames:
            valid_date_keys = set(frames["dim_date"]["date_key"].dropna().astype("int64").tolist())
            for col in FACT_DATE_KEY_COLUMNS[table]:
                if col not in df.columns:
                    continue
                present = df[col].dropna()
                if present.empty:
                    continue
                unresolved = int((~present.astype("int64").isin(valid_date_keys)).sum())
                checks.append(_quality_check(
                    f"date_key_resolves:{table}:{col}", table, "referential",
                    "pass" if unresolved == 0 else "warning",
                    f"{unresolved} date key value(s) in '{col}' not found in dim_date", len(present), unresolved,
                ))

        source_rows = entry.get("source_rows")
        target_rows = entry.get("target_rows")
        if source_rows is not None and target_rows is not None:
            reconciled = target_rows <= source_rows
            checks.append(_quality_check(
                f"row_count_reconciliation:{table}", table, "completeness",
                "pass" if reconciled else "fail",
                f"source={source_rows} target={target_rows}", source_rows,
                0 if reconciled else abs(target_rows - source_rows),
            ))

    if "fact_encounter" in frames and len(frames["fact_encounter"]):
        fe = frames["fact_encounter"]
        negative = fe["patient_responsibility"] < 0
        unflagged = int((negative & ~fe["patient_responsibility_is_negative"]).sum())
        flagged_count = int(fe["patient_responsibility_is_negative"].sum())
        checks.append(_quality_check(
            "negative_patient_responsibility_flagged", "fact_encounter", "numeric",
            "pass" if unflagged == 0 else "fail",
            (f"{flagged_count} negative patient_responsibility row(s), all flagged" if unflagged == 0
             else f"{unflagged} negative patient_responsibility row(s) NOT flagged"),
            len(fe), unflagged,
        ))

    if "mart_readmission" in frames and len(frames["mart_readmission"]):
        mr = frames["mart_readmission"]
        same_encounter = int((mr["index_encounter_key"] == mr["next_encounter_key"]).sum())
        checks.append(_quality_check(
            "readmission_no_self_reference", "mart_readmission", "referential",
            "pass" if same_encounter == 0 else "fail",
            f"{same_encounter} row(s) reference the same encounter as index and next", len(mr), same_encounter,
        ))
        negative_days = int((mr["days_to_readmission"].dropna() < 0).sum())
        checks.append(_quality_check(
            "readmission_days_non_negative", "mart_readmission", "temporal",
            "pass" if negative_days == 0 else "fail",
            f"{negative_days} row(s) with negative days_to_readmission",
            int(mr["days_to_readmission"].notna().sum()), negative_days,
        ))

    return _finalize_quality_report(checks, manifest)


def _finalize_quality_report(checks: list[dict], manifest: dict) -> dict:
    status_counts = {"pass": 0, "warning": 0, "fail": 0, "skipped": 0}
    for check in checks:
        status_counts[check["status"]] += 1
    return {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "schema_version": GOLD_SCHEMA_VERSION,
        "transformation_version": GOLD_TRANSFORMATION_VERSION,
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


def _kpi_validity_checks(kpi_summary: dict) -> list[dict]:
    checks = []
    for kpi in kpi_summary.get("kpis", []):
        name = kpi["kpi_name"]
        numerator = kpi["numerator"]
        denominator = kpi["denominator"]
        valid = numerator is not None and denominator is not None and numerator >= 0 and denominator >= 0
        checks.append(_quality_check(
            f"kpi_valid:{name}", "kpi", "numeric",
            "pass" if valid else "fail",
            f"numerator={numerator} denominator={denominator}", 1, 0 if valid else 1,
        ))
    return checks


def _add_checks(quality_report: dict, new_checks: list[dict]) -> None:
    status_key = {"pass": "passed", "warning": "warnings", "fail": "failed", "skipped": "skipped"}
    for check in new_checks:
        quality_report["checks"].append(check)
        quality_report["summary"]["total_checks"] += 1
        quality_report["summary"][status_key[check["status"]]] += 1


def write_gold_quality_report_json(report: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
        fh.write("\n")


def write_gold_quality_summary_csv(report: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv_module.DictWriter(fh, fieldnames=GOLD_QUALITY_SUMMARY_FIELDNAMES)
        writer.writeheader()
        for check in report["checks"]:
            writer.writerow({key: check.get(key) for key in GOLD_QUALITY_SUMMARY_FIELDNAMES})


def write_gold_kpi_summary_json(kpi_summary: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(kpi_summary, fh, indent=2, default=str)
        fh.write("\n")


# ---------------------------------------------------------------------------
# Top-level pipeline
# ---------------------------------------------------------------------------

KPI_REQUIRED_TABLES: tuple[str, ...] = ("fact_encounter", "fact_procedure", "fact_medication", "dim_patient", "mart_readmission")


def run_gold_pipeline(
    silver_dir: Path,
    gold_dir: Path,
    reports_dir: Path,
    tables: list[str] | None = None,
    marts: list[str] | None = None,
    force: bool = False,
    settings: GoldSettings | None = None,
) -> tuple[dict, dict, dict]:
    """Run the incremental Gold build, quality checks, and KPI summary; write all outputs."""
    manifest = run_gold_build(silver_dir, gold_dir, tables=tables, marts=marts, force=force, settings=settings)
    write_gold_manifest_json(manifest, gold_dir / GOLD_MANIFEST_FILENAME)

    quality_report = build_gold_quality_report(gold_dir, manifest)

    if all((gold_dir / f"{t}.parquet").is_file() for t in KPI_REQUIRED_TABLES):
        frames = {t: pd.read_parquet(gold_dir / f"{t}.parquet") for t in KPI_REQUIRED_TABLES}
        kpi_summary = build_kpi_summary(
            fact_encounter=frames["fact_encounter"],
            fact_procedure=frames["fact_procedure"],
            fact_medication=frames["fact_medication"],
            dim_patient=frames["dim_patient"],
            mart_readmission=frames["mart_readmission"],
        )
        _add_checks(quality_report, _kpi_validity_checks(kpi_summary))
    else:
        missing = [t for t in KPI_REQUIRED_TABLES if not (gold_dir / f"{t}.parquet").is_file()]
        kpi_summary = {
            "kpi_calculator_version": None,
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "kpis": [],
            "skipped_reason": f"Required Gold table(s) not yet built: {missing}",
        }

    write_gold_quality_report_json(quality_report, reports_dir / GOLD_QUALITY_REPORT_FILENAME)
    write_gold_quality_summary_csv(quality_report, reports_dir / GOLD_QUALITY_SUMMARY_FILENAME)
    write_gold_kpi_summary_json(kpi_summary, reports_dir / GOLD_KPI_SUMMARY_FILENAME)

    return manifest, quality_report, kpi_summary
