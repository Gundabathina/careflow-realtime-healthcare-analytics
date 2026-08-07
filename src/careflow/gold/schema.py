"""Shared schema, key generation, and dependency definitions for the Gold layer.

Surrogate keys are deterministic: a stable SHA-256 hash of a namespace
(the dimension/table identity) plus the natural key value(s), never a
random value or a row number. The same natural key always produces the
same surrogate key across runs and across processes.
"""

from __future__ import annotations

import hashlib

import pandas as pd

GOLD_SCHEMA_VERSION = "1.0.0"
GOLD_TRANSFORMATION_VERSION = "1.0.0"
KEY_STRATEGY = "sha256(namespace|natural_key)[:15 hex digits] -> int; deterministic, no random values or row numbers"

UNKNOWN_SURROGATE_KEY = -1


def generate_surrogate_key(namespace: str, *parts: object) -> int:
    """Deterministically derive a surrogate key from a namespace and natural key part(s).

    The namespace (e.g. ``"patient"``, ``"provider"``) prevents different
    dimensions from colliding when they happen to share a natural key
    value. Stable across runs and processes -- never random, never a row
    number.
    """
    normalized_parts = []
    for part in (namespace, *parts):
        if part is None:
            normalized_parts.append("")
        else:
            try:
                if pd.isna(part):
                    normalized_parts.append("")
                    continue
            except (TypeError, ValueError):
                pass
            normalized_parts.append(str(part))
    normalized = "|".join(normalized_parts)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return int(digest[:15], 16)


def surrogate_key_series(natural_key: pd.Series, namespace: str) -> pd.Series:
    """Vectorized surrogate key generation for a natural-key column, null-safe."""
    return natural_key.apply(
        lambda v: generate_surrogate_key(namespace, v) if pd.notna(v) else pd.NA
    ).astype("Int64")


def map_foreign_key(natural_key: pd.Series, namespace: str, valid_keys: set[int]) -> tuple[pd.Series, pd.Series]:
    """Resolve a natural-key column to surrogate keys against a dimension's valid key set.

    Returns ``(resolved_key, is_missing)``. Rows with a null natural key,
    or a natural key that does not resolve to any row in the target
    dimension, get :data:`UNKNOWN_SURROGATE_KEY` and ``is_missing=True``
    -- the row is never dropped, only flagged.
    """
    computed = surrogate_key_series(natural_key, namespace)
    is_missing = computed.isna() | ~computed.isin(valid_keys)
    resolved = computed.where(~is_missing, UNKNOWN_SURROGATE_KEY).astype("int64")
    return resolved, is_missing.fillna(True)


def date_key(value: object) -> int | None:
    """Convert a date/timestamp value to a YYYYMMDD integer date key, or None."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return int(pd.Timestamp(value).strftime("%Y%m%d"))


def date_key_series(series: pd.Series) -> pd.Series:
    return series.apply(date_key).astype("Int64")


# ---------------------------------------------------------------------------
# Build order and dependency graph
# ---------------------------------------------------------------------------

DIMENSION_TABLES: tuple[str, ...] = (
    "dim_patient", "dim_provider", "dim_organization", "dim_payer", "dim_date",
    "dim_condition", "dim_procedure", "dim_medication",
)

FACT_TABLES: tuple[str, ...] = (
    "fact_encounter", "fact_condition", "fact_procedure", "fact_medication",
    "fact_observation", "fact_claim", "fact_immunization", "fact_imaging_study",
)

MART_TABLES: tuple[str, ...] = (
    "mart_patient_360", "mart_readmission", "mart_hospital_operations",
    "mart_financial_performance", "mart_provider_utilization", "mart_monthly_kpis",
)

BUILD_ORDER: tuple[str, ...] = DIMENSION_TABLES + FACT_TABLES + MART_TABLES

# Each table's dependencies: either a Silver dataset name (leaf) or
# another Gold table name (resolved transitively to Silver datasets by
# gold_builder._resolve_silver_dependencies).
TABLE_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "dim_patient": ("patients",),
    "dim_provider": ("providers",),
    "dim_organization": ("organizations",),
    "dim_payer": ("payers",),
    "dim_date": ("encounters", "conditions", "procedures", "medications", "observations", "claims", "immunizations"),
    "dim_condition": ("conditions",),
    "dim_procedure": ("procedures",),
    "dim_medication": ("medications",),
    "fact_encounter": ("encounters", "dim_patient", "dim_provider", "dim_organization", "dim_payer", "dim_date"),
    "fact_condition": ("conditions", "dim_patient", "dim_condition", "dim_date"),
    "fact_procedure": ("procedures", "dim_patient", "dim_procedure", "dim_date"),
    "fact_medication": ("medications", "dim_patient", "dim_payer", "dim_medication", "dim_date"),
    "fact_observation": ("observations", "dim_patient", "dim_date"),
    "fact_claim": ("claims", "claims_transactions", "dim_patient", "dim_date"),
    "fact_immunization": ("immunizations", "dim_patient", "dim_date"),
    # imaging_studies.Id is NOT row-level unique (Phase 2F finding): a study
    # can span multiple series/instance rows. Grain is defined explicitly
    # via a composite natural key (id, series_uid, instance_uid).
    "fact_imaging_study": ("imaging_studies", "dim_patient", "dim_date"),
    "mart_patient_360": (
        "dim_patient", "fact_encounter", "fact_condition", "fact_procedure",
        "fact_medication", "fact_observation", "fact_immunization",
    ),
    "mart_readmission": ("fact_encounter",),
    "mart_hospital_operations": ("fact_encounter",),
    "mart_financial_performance": ("fact_encounter",),
    "mart_provider_utilization": ("fact_encounter", "fact_procedure", "dim_provider"),
    "mart_monthly_kpis": ("fact_encounter", "fact_procedure", "fact_medication", "mart_readmission"),
}

# Expected output columns per table, used for the "no output schema drift"
# quality check. Facts/dims may carry additional flag columns (e.g.
# *_is_missing) beyond this required set.
TABLE_SCHEMAS: dict[str, tuple[str, ...]] = {
    "dim_patient": (
        "patient_key", "patient_id", "birth_date", "death_date", "gender", "race", "ethnicity",
        "marital_status", "city", "state", "county", "zip", "latitude", "longitude", "income",
        "healthcare_expenses", "healthcare_coverage", "is_deceased", "age_at_reference_date",
        "age_group", "source_file", "source_checksum", "transformation_timestamp_utc",
    ),
    "dim_provider": (
        "provider_key", "provider_id", "organization_id", "provider_name", "gender", "speciality",
        "city", "state", "zip",
    ),
    "dim_organization": (
        "organization_key", "organization_id", "organization_name", "city", "state", "zip",
        "revenue", "utilization",
    ),
    "dim_payer": (
        "payer_key", "payer_id", "payer_name", "ownership", "state_headquartered", "amount_covered",
        "amount_uncovered", "revenue", "unique_customers", "member_months",
    ),
    "dim_date": (
        "date_key", "full_date", "day", "day_name", "week_of_year", "month", "month_name",
        "quarter", "year", "year_month", "is_weekend",
    ),
    "dim_condition": ("condition_key", "code", "description"),
    "dim_procedure": ("procedure_key", "code", "description"),
    "dim_medication": ("medication_key", "code", "description"),
    "fact_encounter": (
        "encounter_key", "encounter_id", "patient_key", "provider_key", "organization_key",
        "payer_key", "encounter_date_key", "start_timestamp", "stop_timestamp", "encounter_class",
        "encounter_duration_minutes", "base_encounter_cost", "total_claim_cost", "payer_coverage",
        "patient_responsibility", "is_inpatient", "is_emergency", "reason_code", "reason_description",
    ),
    "fact_condition": (
        "condition_event_key", "patient_key", "encounter_key", "condition_key", "start_date_key",
        "stop_date_key", "is_active", "condition_duration_days",
    ),
    "fact_procedure": (
        "procedure_event_key", "patient_key", "encounter_key", "procedure_key", "start_date_key",
        "stop_date_key", "procedure_duration_minutes", "base_cost", "reason_code", "reason_description",
    ),
    "fact_medication": (
        "medication_event_key", "patient_key", "encounter_key", "payer_key", "medication_key",
        "start_date_key", "stop_date_key", "base_cost", "payer_coverage", "total_cost", "dispenses",
        "medication_duration_days", "is_active",
    ),
    "fact_observation": (
        "observation_key", "patient_key", "encounter_key", "observation_date_key", "category",
        "observation_code", "description", "raw_value", "numeric_value", "units", "observation_type",
    ),
    "fact_claim": (
        "claim_key", "claim_id", "patient_key", "encounter_key", "provider_key", "payer_key",
        "service_date_key", "claim_status", "outstanding_amount", "claim_type",
    ),
    "fact_immunization": (
        "immunization_key", "patient_key", "encounter_key", "immunization_date_key", "code",
        "description", "base_cost",
    ),
    "fact_imaging_study": (
        "imaging_study_key", "study_id", "series_uid", "instance_uid", "patient_key", "encounter_key",
        "study_date_key", "bodysite_code", "modality_code", "sop_code", "procedure_code",
    ),
    "mart_patient_360": (
        "patient_key", "patient_id", "gender", "race", "ethnicity", "age_group", "is_deceased",
        "total_encounters", "inpatient_encounters", "emergency_encounters", "total_conditions",
        "active_conditions", "total_procedures", "total_medications", "total_observations",
        "total_immunizations", "first_encounter_date", "last_encounter_date", "total_claim_cost",
        "total_payer_coverage", "total_patient_responsibility", "average_encounter_duration_minutes",
        "most_recent_encounter_class",
    ),
    "mart_readmission": (
        "patient_key", "index_encounter_key", "index_discharge_timestamp", "next_encounter_key",
        "next_encounter_timestamp", "days_to_readmission", "readmitted_within_30_days",
        "readmitted_within_7_days", "readmitted_within_14_days", "index_encounter_class",
        "next_encounter_class",
    ),
    "mart_hospital_operations": (
        "organization_key", "encounter_date_key", "encounter_class", "encounter_count",
        "unique_patients", "average_duration_minutes", "median_duration_minutes", "inpatient_count",
        "emergency_count", "total_claim_cost", "payer_coverage", "patient_responsibility",
        "provider_count",
    ),
    "mart_financial_performance": (
        "payer_key", "organization_key", "year_month", "encounter_count", "total_claim_cost",
        "total_payer_coverage", "total_patient_responsibility", "average_claim_cost", "coverage_ratio",
    ),
    "mart_provider_utilization": (
        "provider_key", "year_month", "encounter_count", "unique_patients", "total_procedures",
        "average_encounter_duration_minutes", "total_claim_cost",
    ),
    "mart_monthly_kpis": (
        "year_month", "total_patients_served", "total_encounters", "inpatient_encounters",
        "emergency_encounters", "average_length_of_stay_minutes", "total_claim_cost",
        "total_payer_coverage", "total_patient_responsibility", "readmission_count",
        "readmission_rate_30_day", "average_procedures_per_encounter", "average_medications_per_patient",
    ),
}
