"""Tests for careflow.gold.kpi_calculator.

All tests use small in-memory DataFrames shaped like Gold fact/mart
tables. None of them depend on the real dataset.
"""

from __future__ import annotations

import pandas as pd
import pytest

from careflow.gold import kpi_calculator as kpi


def make_fact_encounter() -> pd.DataFrame:
    return pd.DataFrame({
        "encounter_key": [1, 2, 3, 4],
        "patient_key": [10, 10, 20, 30],
        "encounter_duration_minutes": [60.0, 120.0, None, 30.0],
        "is_inpatient": [True, False, False, False],
        "is_emergency": [False, True, False, False],
        "total_claim_cost": [1000.0, 200.0, 100.0, 50.0],
        "payer_coverage": [800.0, 150.0, 50.0, 25.0],
        "patient_responsibility": [200.0, 50.0, 50.0, 25.0],
    })


def make_fact_procedure() -> pd.DataFrame:
    return pd.DataFrame({"procedure_event_key": [1, 2, 3]})


def make_fact_medication() -> pd.DataFrame:
    return pd.DataFrame({"medication_event_key": [1, 2], "patient_key": [10, 20]})


def make_dim_patient() -> pd.DataFrame:
    return pd.DataFrame({"patient_key": [10, 20, 30]})


def make_mart_readmission(rows: int = 4, readmit_7=1, readmit_14=2, readmit_30=3) -> pd.DataFrame:
    return pd.DataFrame({
        "readmitted_within_7_days": [True] * readmit_7 + [False] * (rows - readmit_7),
        "readmitted_within_14_days": [True] * readmit_14 + [False] * (rows - readmit_14),
        "readmitted_within_30_days": [True] * readmit_30 + [False] * (rows - readmit_30),
    })


# -- individual KPI functions ---------------------------------------------------


def test_readmission_rate_7_day():
    result = kpi.readmission_rate(make_mart_readmission(), 7)
    assert result["kpi_name"] == "readmission_rate_7_day"
    assert result["numerator"] == 1
    assert result["denominator"] == 4
    assert result["value"] == pytest.approx(0.25)


def test_readmission_rate_30_day():
    result = kpi.readmission_rate(make_mart_readmission(), 30)
    assert result["numerator"] == 3
    assert result["value"] == pytest.approx(0.75)


def test_average_encounter_duration():
    result = kpi.average_encounter_duration(make_fact_encounter())
    # 3 non-null durations: 60, 120, 30 -> mean 70
    assert result["denominator"] == 3
    assert result["value"] == pytest.approx(70.0)


def test_average_inpatient_length_of_stay():
    result = kpi.average_inpatient_length_of_stay(make_fact_encounter())
    assert result["denominator"] == 1
    assert result["value"] == pytest.approx(60.0)


def test_emergency_encounter_percentage():
    result = kpi.emergency_encounter_percentage(make_fact_encounter())
    assert result["numerator"] == 1
    assert result["denominator"] == 4
    assert result["value"] == pytest.approx(0.25)


def test_payer_coverage_ratio():
    result = kpi.payer_coverage_ratio(make_fact_encounter())
    assert result["numerator"] == pytest.approx(1025.0)
    assert result["denominator"] == pytest.approx(1350.0)
    assert result["value"] == pytest.approx(1025.0 / 1350.0)


def test_average_patient_responsibility():
    result = kpi.average_patient_responsibility(make_fact_encounter())
    assert result["value"] == pytest.approx((200 + 50 + 50 + 25) / 4)


def test_cost_per_encounter():
    result = kpi.cost_per_encounter(make_fact_encounter())
    assert result["value"] == pytest.approx(1350.0 / 4)


def test_encounters_per_patient():
    result = kpi.encounters_per_patient(make_fact_encounter())
    assert result["numerator"] == 4
    assert result["denominator"] == 3  # patients 10, 20, 30
    assert result["value"] == pytest.approx(4 / 3)


def test_procedures_per_encounter():
    result = kpi.procedures_per_encounter(make_fact_procedure(), make_fact_encounter())
    assert result["numerator"] == 3
    assert result["denominator"] == 4


def test_medications_per_patient():
    result = kpi.medications_per_patient(make_fact_medication(), make_dim_patient())
    assert result["numerator"] == 2
    assert result["denominator"] == 3


# -- zero-denominator handling ---------------------------------------------------


def test_zero_denominator_returns_null_value_not_error():
    empty_encounters = make_fact_encounter().iloc[0:0]
    result = kpi.cost_per_encounter(empty_encounters)
    assert result["denominator"] == 0
    assert result["value"] is None


def test_zero_denominator_readmission_rate():
    empty_readmission = make_mart_readmission(rows=0, readmit_7=0, readmit_14=0, readmit_30=0)
    result = kpi.readmission_rate(empty_readmission, 30)
    assert result["denominator"] == 0
    assert result["value"] is None


def test_zero_denominator_encounters_per_patient():
    empty = make_fact_encounter().iloc[0:0]
    result = kpi.encounters_per_patient(empty)
    assert result["denominator"] == 0
    assert result["value"] is None


# -- every KPI carries the required metadata fields ------------------------------


def test_kpi_result_has_required_fields():
    result = kpi.cost_per_encounter(make_fact_encounter())
    for field in ("kpi_name", "numerator", "denominator", "value", "unit", "definition", "calculated_at_utc", "filters"):
        assert field in result


# -- full summary -----------------------------------------------------------------


def test_build_kpi_summary_includes_all_kpis():
    summary = kpi.build_kpi_summary(
        fact_encounter=make_fact_encounter(),
        fact_procedure=make_fact_procedure(),
        fact_medication=make_fact_medication(),
        dim_patient=make_dim_patient(),
        mart_readmission=make_mart_readmission(),
    )
    names = {k["kpi_name"] for k in summary["kpis"]}
    expected = {
        "readmission_rate_7_day", "readmission_rate_14_day", "readmission_rate_30_day",
        "average_encounter_duration", "average_inpatient_length_of_stay",
        "emergency_encounter_percentage", "payer_coverage_ratio", "average_patient_responsibility",
        "cost_per_encounter", "encounters_per_patient", "procedures_per_encounter",
        "medications_per_patient",
    }
    assert expected <= names
    assert "generated_at_utc" in summary
