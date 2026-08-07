"""Reusable healthcare KPI calculations for the Gold layer.

Every KPI returns a dict with ``kpi_name``, ``numerator``,
``denominator``, ``value``, ``unit``, ``definition``,
``calculated_at_utc``, and ``filters``. Zero denominators always
produce ``value: null`` rather than raising or dividing by zero.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

KPI_CALCULATOR_VERSION = "1.0.0"


def _kpi_result(
    kpi_name: str, numerator: float, denominator: float, unit: str, definition: str,
    filters: dict | None = None,
) -> dict:
    value = (numerator / denominator) if denominator else None
    return {
        "kpi_name": kpi_name,
        "numerator": numerator,
        "denominator": denominator,
        "value": value,
        "unit": unit,
        "definition": definition,
        "calculated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "filters": filters or {},
    }


def readmission_rate(readmission_df: pd.DataFrame, window_days: int) -> dict:
    col = {
        7: "readmitted_within_7_days", 14: "readmitted_within_14_days", 30: "readmitted_within_30_days",
    }[window_days]
    numerator = int(readmission_df[col].sum()) if not readmission_df.empty and col in readmission_df else 0
    denominator = len(readmission_df)
    return _kpi_result(
        f"readmission_rate_{window_days}_day", numerator, denominator, "ratio",
        f"Share of qualifying index encounters followed by another qualifying encounter within {window_days} days.",
        filters={"window_days": window_days},
    )


def average_encounter_duration(fact_encounter: pd.DataFrame) -> dict:
    valid = fact_encounter["encounter_duration_minutes"].dropna() if len(fact_encounter) else pd.Series([], dtype="float64")
    return _kpi_result(
        "average_encounter_duration", float(valid.sum()), len(valid), "minutes",
        "Mean encounter duration across encounters with both a start and stop timestamp.",
    )


def average_inpatient_length_of_stay(fact_encounter: pd.DataFrame) -> dict:
    inpatient = fact_encounter[fact_encounter.get("is_inpatient", pd.Series([], dtype=bool)) == True]  # noqa: E712
    valid = inpatient["encounter_duration_minutes"].dropna() if len(inpatient) else pd.Series([], dtype="float64")
    return _kpi_result(
        "average_inpatient_length_of_stay", float(valid.sum()), len(valid), "minutes",
        "Mean encounter duration for inpatient-class encounters.", filters={"is_inpatient": True},
    )


def emergency_encounter_percentage(fact_encounter: pd.DataFrame) -> dict:
    numerator = int(fact_encounter["is_emergency"].sum()) if len(fact_encounter) else 0
    denominator = len(fact_encounter)
    return _kpi_result(
        "emergency_encounter_percentage", numerator, denominator, "ratio",
        "Emergency-class encounters as a fraction of all encounters.",
    )


def payer_coverage_ratio(fact_encounter: pd.DataFrame) -> dict:
    numerator = float(fact_encounter["payer_coverage"].dropna().sum()) if len(fact_encounter) else 0.0
    denominator = float(fact_encounter["total_claim_cost"].dropna().sum()) if len(fact_encounter) else 0.0
    return _kpi_result(
        "payer_coverage_ratio", numerator, denominator, "ratio",
        "Total payer coverage divided by total claim cost across encounters.",
    )


def average_patient_responsibility(fact_encounter: pd.DataFrame) -> dict:
    valid = fact_encounter["patient_responsibility"].dropna() if len(fact_encounter) else pd.Series([], dtype="float64")
    return _kpi_result(
        "average_patient_responsibility", float(valid.sum()), len(valid), "currency",
        "Mean patient responsibility (total claim cost minus payer coverage) per encounter.",
    )


def cost_per_encounter(fact_encounter: pd.DataFrame) -> dict:
    valid = fact_encounter["total_claim_cost"].dropna() if len(fact_encounter) else pd.Series([], dtype="float64")
    return _kpi_result(
        "cost_per_encounter", float(valid.sum()), len(valid), "currency",
        "Mean total claim cost per encounter.",
    )


def encounters_per_patient(fact_encounter: pd.DataFrame) -> dict:
    numerator = len(fact_encounter)
    denominator = int(fact_encounter["patient_key"].nunique()) if len(fact_encounter) else 0
    return _kpi_result(
        "encounters_per_patient", numerator, denominator, "ratio",
        "Total encounters divided by the number of distinct patients with at least one encounter.",
    )


def procedures_per_encounter(fact_procedure: pd.DataFrame, fact_encounter: pd.DataFrame) -> dict:
    return _kpi_result(
        "procedures_per_encounter", len(fact_procedure), len(fact_encounter), "ratio",
        "Total procedure occurrences divided by total encounters.",
    )


def medications_per_patient(fact_medication: pd.DataFrame, dim_patient: pd.DataFrame) -> dict:
    return _kpi_result(
        "medications_per_patient", len(fact_medication), len(dim_patient), "ratio",
        "Total medication occurrences divided by total patients in dim_patient.",
    )


def build_kpi_summary(
    fact_encounter: pd.DataFrame,
    fact_procedure: pd.DataFrame,
    fact_medication: pd.DataFrame,
    dim_patient: pd.DataFrame,
    mart_readmission: pd.DataFrame,
) -> dict:
    """Compute every reusable KPI and return the full summary payload."""
    kpis = [
        readmission_rate(mart_readmission, 7),
        readmission_rate(mart_readmission, 14),
        readmission_rate(mart_readmission, 30),
        average_encounter_duration(fact_encounter),
        average_inpatient_length_of_stay(fact_encounter),
        emergency_encounter_percentage(fact_encounter),
        payer_coverage_ratio(fact_encounter),
        average_patient_responsibility(fact_encounter),
        cost_per_encounter(fact_encounter),
        encounters_per_patient(fact_encounter),
        procedures_per_encounter(fact_procedure, fact_encounter),
        medications_per_patient(fact_medication, dim_patient),
    ]
    return {
        "kpi_calculator_version": KPI_CALCULATOR_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kpis": kpis,
    }
