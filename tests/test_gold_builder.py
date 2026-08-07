"""Tests for careflow.gold.gold_builder and careflow.gold.schema.

All tests use temporary Parquet fixtures shaped like Silver output. None
of them depend on the real Bronze/Silver dataset.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from careflow.gold import gold_builder as gb
from careflow.gold import schema as gs


# -- fixtures -----------------------------------------------------------------


def write_parquet(path: Path, df: pd.DataFrame) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, engine="pyarrow", index=False)
    return path


def make_silver_manifest(datasets: list[str], checksum: str = "checksum") -> dict:
    return {
        "datasets": [
            {"dataset": d, "status": "processed", "source_checksum": f"{checksum}-{d}"} for d in datasets
        ]
    }


def make_patients_df(ids=("p1", "p2", "p3"), genders=("m", "F", "M")) -> pd.DataFrame:
    n = len(ids)
    return pd.DataFrame({
        "patient_id": list(ids),
        "birthdate": pd.to_datetime(["1990-01-01"] * n, utc=True),
        "deathdate": pd.to_datetime([None] * n, utc=True),
        "ssn": [f"999-00-{i:04d}" for i in range(n)],
        "drivers": [None] * n,
        "passport": [None] * n,
        "marital": ["M"] * n,
        "race": ["white"] * n,
        "ethnicity": ["nonhispanic"] * n,
        "gender": list(genders)[:n],
        "city": ["Boston"] * n,
        "state": ["MA"] * n,
        "county": ["Suffolk"] * n,
        "fips": ["25025"] * n,
        "zip": ["02138"] * n,
        "lat": [42.1] * n,
        "lon": [-71.1] * n,
        "healthcare_expenses": [1000.0] * n,
        "healthcare_coverage": [500.0] * n,
        "income": [50000] * n,
        "is_deceased": [False] * n,
        "age_at_reference_date": [35] * n,
        "age_group": ["35-49"] * n,
        "source_file": ["patients.csv"] * n,
        "source_checksum": ["chk"] * n,
        "transformation_timestamp_utc": ["2026-01-01T00:00:00Z"] * n,
    })


def make_providers_df(ids=("pr1", "pr2")) -> pd.DataFrame:
    n = len(ids)
    return pd.DataFrame({
        "id": list(ids), "organization_id": ["o1"] * n, "name": [f"Dr {i}" for i in ids],
        "gender": ["F"] * n, "speciality": ["GENERAL PRACTICE"] * n, "city": ["Boston"] * n,
        "state": ["MA"] * n, "zip": ["02138"] * n,
    })


def make_organizations_df(ids=("o1",)) -> pd.DataFrame:
    n = len(ids)
    return pd.DataFrame({
        "id": list(ids), "name": ["General Hospital"] * n, "city": ["Boston"] * n, "state": ["MA"] * n,
        "zip": ["02138"] * n, "revenue": ["1000000.0"] * n, "utilization": ["500"] * n,
    })


def make_payers_df(ids=("pay1",)) -> pd.DataFrame:
    n = len(ids)
    return pd.DataFrame({
        "id": list(ids), "name": ["Blue Cross"] * n, "ownership": ["private"] * n,
        "state_headquartered": ["MA"] * n, "amount_covered": ["10000.0"] * n,
        "amount_uncovered": ["2000.0"] * n, "revenue": ["500000.0"] * n,
        "unique_customers": ["100"] * n, "member_months": ["1200"] * n,
    })


def make_encounters_df(
    ids=("e1", "e2", "e3"), patient_ids=("p1", "p1", "p2"),
    classes=("inpatient", "emergency", "ambulatory"),
    starts=("2020-01-01T08:00:00Z", "2020-01-20T08:00:00Z", "2020-02-01T08:00:00Z"),
    stops=("2020-01-05T08:00:00Z", "2020-01-20T12:00:00Z", "2020-02-01T09:00:00Z"),
    total_costs=(1000.0, 200.0, 100.0), payer_coverages=(800.0, 150.0, 50.0),
    provider_ids=("pr1", "pr1", "pr2"), organization_ids=("o1", "o1", "o1"), payer_ids=("pay1", "pay1", "pay1"),
) -> pd.DataFrame:
    n = len(ids)
    return pd.DataFrame({
        "encounter_id": list(ids),
        "start": pd.to_datetime(list(starts), utc=True),
        "stop": pd.to_datetime(list(stops), utc=True),
        "patient_id": list(patient_ids)[:n],
        "organization_id": list(organization_ids)[:n],
        "provider_id": list(provider_ids)[:n],
        "payer_id": list(payer_ids)[:n],
        "encounter_class": list(classes)[:n],
        "base_encounter_cost": [50.0] * n,
        "total_claim_cost": list(total_costs)[:n],
        "payer_coverage": list(payer_coverages)[:n],
        "encounter_duration_minutes": [60.0] * n,
        "is_inpatient": [c == "inpatient" for c in list(classes)[:n]],
        "is_emergency": [c == "emergency" for c in list(classes)[:n]],
        "reasoncode": [None] * n,
        "reasondescription": [None] * n,
    })


def make_conditions_df() -> pd.DataFrame:
    return pd.DataFrame({
        "patient_id": ["p1", "p2"], "encounter_id": ["e1", "e3"], "code": ["11111", "22222"],
        "description": ["Cond A", "Cond B"],
        "start": pd.to_datetime(["2020-01-01", "2020-02-01"], utc=True),
        "stop": pd.to_datetime(["2020-06-01", None], utc=True),
        "is_active": [False, True], "condition_duration_days": [152, None],
    })


def make_procedures_df() -> pd.DataFrame:
    return pd.DataFrame({
        "patient_id": ["p1", "p1"], "encounter_id": ["e1", "e2"], "code": ["p-1", "p-2"],
        "description": ["Proc A", "Proc B"],
        "start": pd.to_datetime(["2020-01-02", "2020-01-20"], utc=True),
        "stop": pd.to_datetime(["2020-01-02", "2020-01-20"], utc=True),
        "base_cost": [100.0, 50.0], "reasoncode": [None, None], "reasondescription": [None, None],
        "procedure_duration_minutes": [30.0, 15.0],
    })


def make_medications_df() -> pd.DataFrame:
    return pd.DataFrame({
        "patient_id": ["p1", "p2"], "encounter_id": ["e1", "e3"], "payer_id": ["pay1", "pay1"],
        "code": ["m-1", "m-2"], "description": ["Med A", "Med B"],
        "start": pd.to_datetime(["2020-01-01", "2020-02-01"], utc=True),
        "stop": pd.to_datetime(["2020-01-10", None], utc=True),
        "base_cost": [10.0, 20.0], "payer_coverage": [5.0, 10.0], "dispenses": [1, 2],
        "total_cost": [10.0, 40.0], "medication_duration_days": [9, None], "is_active": [False, True],
        "reasoncode": [None, None], "reasondescription": [None, None],
    })


def make_observations_df() -> pd.DataFrame:
    return pd.DataFrame({
        "patient_id": ["p1", "p1"], "encounter_id": ["e1", "e1"], "observation_date": pd.to_datetime(["2020-01-01", "2020-01-02"], utc=True),
        "category": ["vital-signs"] * 2, "code": ["8302-2", "8302-2"], "description": ["Height", "Height"],
        "value": ["170.5", "171.0"], "units": ["cm", "cm"], "type": ["numeric", "numeric"],
        "numeric_value": [170.5, 171.0],
    })


def make_claims_df() -> pd.DataFrame:
    return pd.DataFrame({
        "id": ["c1", "c2"], "patient_id": ["p1", "p2"], "providerid": ["pr1", "unknown-provider"],
        "primarypatientinsuranceid": ["pay1", None], "encounter_id": ["e1", "e3"],
        "servicedate": pd.to_datetime(["2020-01-01", "2020-02-01"], utc=True),
        "status1": ["CLOSED", "BILLED"], "outstanding1": ["0.0", "50.0"],
        "healthcareclaimtypeid1": ["professional", "institutional"],
    })


def make_immunizations_df() -> pd.DataFrame:
    return pd.DataFrame({
        "patient_id": ["p1"], "encounter_id": ["e1"],
        "immunization_date": pd.to_datetime(["2020-01-01"], utc=True),
        "code": ["imm-1"], "description": ["Flu shot"], "base_cost": [25.0],
    })


def make_imaging_studies_df() -> pd.DataFrame:
    # Same study id, two series -- the known non-unique-Id scenario.
    return pd.DataFrame({
        "id": ["study-1", "study-1"], "series_uid": ["series-1", "series-2"],
        "instance_uid": ["inst-1", "inst-2"], "date": pd.to_datetime(["2020-01-01", "2020-01-01"], utc=True),
        "patient_id": ["p1", "p1"], "encounter_id": ["e1", "e1"],
        "bodysite_code": ["c1", "c1"], "modality_code": ["CT", "CT"],
        "sop_code": ["s1", "s2"], "procedure_code": ["pc1", "pc1"],
    })


def write_full_silver(silver_dir: Path) -> None:
    write_parquet(silver_dir / "patients.parquet", make_patients_df())
    write_parquet(silver_dir / "providers.parquet", make_providers_df())
    write_parquet(silver_dir / "organizations.parquet", make_organizations_df())
    write_parquet(silver_dir / "payers.parquet", make_payers_df())
    write_parquet(silver_dir / "encounters.parquet", make_encounters_df())
    write_parquet(silver_dir / "conditions.parquet", make_conditions_df())
    write_parquet(silver_dir / "procedures.parquet", make_procedures_df())
    write_parquet(silver_dir / "medications.parquet", make_medications_df())
    write_parquet(silver_dir / "observations.parquet", make_observations_df())
    write_parquet(silver_dir / "claims.parquet", make_claims_df())
    write_parquet(silver_dir / "immunizations.parquet", make_immunizations_df())
    write_parquet(silver_dir / "imaging_studies.parquet", make_imaging_studies_df())


ALL_SILVER_DATASETS = [
    "patients", "providers", "organizations", "payers", "encounters", "conditions",
    "procedures", "medications", "observations", "claims", "claims_transactions", "immunizations",
]


def build_all_dims(silver_dir: Path) -> dict[str, pd.DataFrame]:
    built: dict[str, pd.DataFrame] = {}
    built["dim_patient"], _ = gb.build_dim_patient(silver_dir)
    built["dim_provider"], _ = gb.build_dim_provider(silver_dir)
    built["dim_organization"], _ = gb.build_dim_organization(silver_dir)
    built["dim_payer"], _ = gb.build_dim_payer(silver_dir)
    built["dim_condition"], _ = gb.build_dim_condition(silver_dir)
    built["dim_procedure"], _ = gb.build_dim_procedure(silver_dir)
    built["dim_medication"], _ = gb.build_dim_medication(silver_dir)
    return built


# -- surrogate keys ----------------------------------------------------------


def test_surrogate_keys_are_deterministic():
    k1 = gs.generate_surrogate_key("patient", "abc-123")
    k2 = gs.generate_surrogate_key("patient", "abc-123")
    assert k1 == k2
    assert isinstance(k1, int)


def test_surrogate_keys_are_namespace_scoped():
    assert gs.generate_surrogate_key("patient", "x") != gs.generate_surrogate_key("provider", "x")


def test_surrogate_keys_are_not_row_numbers():
    # Reordering the input rows must not change the resulting key for a
    # given natural key value.
    s1 = gs.surrogate_key_series(pd.Series(["a", "b", "c"]), "x")
    s2 = gs.surrogate_key_series(pd.Series(["c", "b", "a"]), "x")
    assert s1.iloc[0] == s2.iloc[2]
    assert s1.iloc[2] == s2.iloc[0]


# -- dimension dedup / composition --------------------------------------------


def test_dim_patient_deduplication(tmp_path):
    df = pd.concat([make_patients_df(ids=("p1",)), make_patients_df(ids=("p1",))], ignore_index=True)
    write_parquet(tmp_path / "patients.parquet", df)
    dim, source_rows = gb.build_dim_patient(tmp_path)
    assert source_rows == 2
    assert len(dim) == 1


def test_dim_provider_columns(tmp_path):
    write_parquet(tmp_path / "providers.parquet", make_providers_df())
    dim, _ = gb.build_dim_provider(tmp_path)
    assert set(gs.TABLE_SCHEMAS["dim_provider"]) <= set(dim.columns)
    assert dim["provider_key"].notna().all()


def test_dim_organization_numeric_typing(tmp_path):
    write_parquet(tmp_path / "organizations.parquet", make_organizations_df())
    dim, _ = gb.build_dim_organization(tmp_path)
    assert pd.api.types.is_numeric_dtype(dim["revenue"])
    assert pd.api.types.is_numeric_dtype(dim["utilization"])


def test_dim_date_completeness(tmp_path):
    write_full_silver(tmp_path)
    dim, _ = gb.build_dim_date(tmp_path)
    assert len(dim) > 0
    full_dates = pd.to_datetime(dim["full_date"])
    expected_days = (full_dates.max() - full_dates.min()).days + 1
    assert len(dim) == expected_days
    assert dim["date_key"].is_unique
    assert set(gs.TABLE_SCHEMAS["dim_date"]) == set(dim.columns)


def test_dim_condition_dedup_on_code(tmp_path):
    df = pd.concat([make_conditions_df(), make_conditions_df().iloc[[0]]], ignore_index=True)
    write_parquet(tmp_path / "conditions.parquet", df)
    dim, source_rows = gb.build_dim_condition(tmp_path)
    assert source_rows == 3
    assert dim["code"].is_unique


# -- fact foreign-key mapping / unknown references -----------------------------


def test_fact_encounter_foreign_keys_resolve(tmp_path):
    write_full_silver(tmp_path)
    built = build_all_dims(tmp_path)
    fact, source_rows = gb.build_fact_encounter(tmp_path, built)
    assert source_rows == 3
    assert not fact["patient_key_is_missing"].any()
    assert not fact["provider_key_is_missing"].any()
    assert not fact["organization_key_is_missing"].any()
    assert not fact["payer_key_is_missing"].any()


def test_fact_encounter_unknown_patient_is_flagged_not_dropped(tmp_path):
    df = make_encounters_df(patient_ids=("p1", "ghost-patient", "p2"))
    write_parquet(tmp_path / "encounters.parquet", df)
    write_parquet(tmp_path / "patients.parquet", make_patients_df())
    built = {"dim_patient": gb.build_dim_patient(tmp_path)[0]}
    write_parquet(tmp_path / "providers.parquet", make_providers_df())
    write_parquet(tmp_path / "organizations.parquet", make_organizations_df())
    write_parquet(tmp_path / "payers.parquet", make_payers_df())
    built["dim_provider"] = gb.build_dim_provider(tmp_path)[0]
    built["dim_organization"] = gb.build_dim_organization(tmp_path)[0]
    built["dim_payer"] = gb.build_dim_payer(tmp_path)[0]

    fact, _ = gb.build_fact_encounter(tmp_path, built)

    assert len(fact) == 3  # not dropped
    ghost_row = fact[fact["encounter_id"] == "e2"].iloc[0]
    assert ghost_row["patient_key_is_missing"] == True  # noqa: E712
    assert ghost_row["patient_key"] == gs.UNKNOWN_SURROGATE_KEY


# -- patient responsibility ----------------------------------------------------


def test_patient_responsibility_calculation(tmp_path):
    write_full_silver(tmp_path)
    built = build_all_dims(tmp_path)
    fact, _ = gb.build_fact_encounter(tmp_path, built)
    row = fact[fact["encounter_id"] == "e1"].iloc[0]
    assert row["patient_responsibility"] == pytest.approx(1000.0 - 800.0)


def test_negative_patient_responsibility_is_flagged(tmp_path):
    write_full_silver(tmp_path)
    df = make_encounters_df(total_costs=(100.0, 200.0, 100.0), payer_coverages=(500.0, 150.0, 50.0))
    write_parquet(tmp_path / "encounters.parquet", df)
    built = build_all_dims(tmp_path)
    fact, _ = gb.build_fact_encounter(tmp_path, built)
    e1 = fact[fact["encounter_id"] == "e1"].iloc[0]
    assert e1["patient_responsibility"] < 0
    assert e1["patient_responsibility_is_negative"] == True  # noqa: E712


# -- composite imaging study key -----------------------------------------------


def test_imaging_study_composite_key_disambiguates_same_study_id(tmp_path):
    df = make_imaging_studies_df()
    assert df["id"].nunique() == 1  # same study id
    assert df["id"].duplicated().any()

    write_full_silver(tmp_path)
    write_parquet(tmp_path / "imaging_studies.parquet", df)
    built = build_all_dims(tmp_path)
    fact, source_rows = gb.build_fact_imaging_study(tmp_path, built)

    assert source_rows == 2
    assert len(fact) == 2  # both rows kept -- not collapsed by the non-unique study id
    assert fact["imaging_study_key"].is_unique


def test_imaging_study_composite_key_function_matches_series_and_instance():
    df = make_imaging_studies_df()
    keys = gb.imaging_study_composite_key(df)
    assert keys.iloc[0] != keys.iloc[1]
    assert "series-1" in keys.iloc[0]
    assert "series-2" in keys.iloc[1]


# -- marts ----------------------------------------------------------------------


def _build_all_facts(silver_dir: Path, built: dict[str, pd.DataFrame]) -> None:
    built["fact_encounter"], _ = gb.build_fact_encounter(silver_dir, built)
    built["fact_condition"], _ = gb.build_fact_condition(silver_dir, built)
    built["fact_procedure"], _ = gb.build_fact_procedure(silver_dir, built)
    built["fact_medication"], _ = gb.build_fact_medication(silver_dir, built)
    built["fact_observation"], _ = gb.build_fact_observation(silver_dir, built)
    built["fact_immunization"], _ = gb.build_fact_immunization(silver_dir, built)


def test_mart_patient_360_aggregation(tmp_path):
    write_full_silver(tmp_path)
    built = build_all_dims(tmp_path)
    _build_all_facts(tmp_path, built)

    mart, _ = gb.build_mart_patient_360(built)
    p1 = mart[mart["patient_id"] == "p1"].iloc[0]
    assert p1["total_encounters"] == 2  # e1, e2
    assert p1["inpatient_encounters"] == 1
    assert p1["total_conditions"] == 1
    assert p1["total_procedures"] == 2
    p3 = mart[mart["patient_id"] == "p3"].iloc[0]
    assert p3["total_encounters"] == 0


def test_mart_readmission_within_windows(tmp_path):
    # Index inpatient encounter discharges, then a subsequent emergency
    # encounter starts 5 days later -> within 7/14/30 day windows.
    encounters = make_encounters_df(
        ids=("e1", "e2"), patient_ids=("p1", "p1"), classes=("inpatient", "emergency"),
        starts=("2020-01-01T08:00:00Z", "2020-01-10T08:00:00Z"),
        stops=("2020-01-05T08:00:00Z", "2020-01-10T12:00:00Z"),
    )
    write_full_silver(tmp_path)
    write_parquet(tmp_path / "encounters.parquet", encounters)
    built = build_all_dims(tmp_path)
    built["fact_encounter"], _ = gb.build_fact_encounter(tmp_path, built)

    settings = gb.GoldSettings(readmission_qualifying_classes=("inpatient", "emergency"), readmission_windows=(7, 14, 30))
    mart, _ = gb.build_mart_readmission(built, settings)

    row = mart[mart["index_encounter_key"] == gs.generate_surrogate_key("encounter", "e1")].iloc[0]
    assert row["days_to_readmission"] == pytest.approx(5.0)
    assert row["readmitted_within_7_days"] == True  # noqa: E712
    assert row["readmitted_within_14_days"] == True  # noqa: E712
    assert row["readmitted_within_30_days"] == True  # noqa: E712


def test_mart_readmission_outside_30_days(tmp_path):
    encounters = make_encounters_df(
        ids=("e1", "e2"), patient_ids=("p1", "p1"), classes=("inpatient", "inpatient"),
        starts=("2020-01-01T08:00:00Z", "2020-03-01T08:00:00Z"),
        stops=("2020-01-05T08:00:00Z", "2020-03-01T12:00:00Z"),
    )
    write_full_silver(tmp_path)
    write_parquet(tmp_path / "encounters.parquet", encounters)
    built = build_all_dims(tmp_path)
    built["fact_encounter"], _ = gb.build_fact_encounter(tmp_path, built)

    settings = gb.GoldSettings(readmission_qualifying_classes=("inpatient", "emergency"), readmission_windows=(7, 14, 30))
    mart, _ = gb.build_mart_readmission(built, settings)
    row = mart[mart["index_encounter_key"] == gs.generate_surrogate_key("encounter", "e1")].iloc[0]
    assert row["readmitted_within_30_days"] == False  # noqa: E712


def test_mart_readmission_no_self_reference(tmp_path):
    write_full_silver(tmp_path)
    built = build_all_dims(tmp_path)
    built["fact_encounter"], _ = gb.build_fact_encounter(tmp_path, built)
    settings = gb.GoldSettings(readmission_qualifying_classes=("inpatient", "emergency"), readmission_windows=(7, 14, 30))
    mart, _ = gb.build_mart_readmission(built, settings)
    assert not (mart["index_encounter_key"] == mart["next_encounter_key"]).any()


def test_mart_readmission_multiple_encounters_ordered_correctly(tmp_path):
    # Three qualifying encounters for the same patient, out of chronological
    # input order -- each index's "next" must be the chronologically next one.
    encounters = make_encounters_df(
        ids=("e3", "e1", "e2"), patient_ids=("p1", "p1", "p1"), classes=("inpatient", "inpatient", "inpatient"),
        starts=("2020-03-01T08:00:00Z", "2020-01-01T08:00:00Z", "2020-01-10T08:00:00Z"),
        stops=("2020-03-01T12:00:00Z", "2020-01-02T08:00:00Z", "2020-01-11T08:00:00Z"),
    )
    write_full_silver(tmp_path)
    write_parquet(tmp_path / "encounters.parquet", encounters)
    built = build_all_dims(tmp_path)
    built["fact_encounter"], _ = gb.build_fact_encounter(tmp_path, built)

    settings = gb.GoldSettings(readmission_qualifying_classes=("inpatient",), readmission_windows=(7, 14, 30))
    mart, _ = gb.build_mart_readmission(built, settings)
    mart = mart.set_index("index_encounter_key")

    e1_key = gs.generate_surrogate_key("encounter", "e1")
    e2_key = gs.generate_surrogate_key("encounter", "e2")
    assert mart.loc[e1_key, "next_encounter_key"] == e2_key  # e1 -> e2, not e3
    assert pd.isna(mart.loc[e2_key, "next_encounter_key"]) or mart.loc[e2_key, "next_encounter_key"] != e1_key


def test_mart_hospital_operations_aggregation(tmp_path):
    write_full_silver(tmp_path)
    built = build_all_dims(tmp_path)
    built["fact_encounter"], _ = gb.build_fact_encounter(tmp_path, built)
    mart, _ = gb.build_mart_hospital_operations(built)
    assert mart["encounter_count"].sum() == 3
    assert set(gs.TABLE_SCHEMAS["mart_hospital_operations"]) == set(mart.columns)


def test_mart_provider_utilization_aggregation(tmp_path):
    write_full_silver(tmp_path)
    built = build_all_dims(tmp_path)
    built["fact_encounter"], _ = gb.build_fact_encounter(tmp_path, built)
    built["fact_procedure"], _ = gb.build_fact_procedure(tmp_path, built)
    mart, _ = gb.build_mart_provider_utilization(built)
    pr1_key = gs.generate_surrogate_key("provider", "pr1")
    pr1_rows = mart[mart["provider_key"] == pr1_key]
    assert pr1_rows["encounter_count"].sum() == 2
    assert pr1_rows["total_procedures"].sum() == 2


def test_monthly_kpi_calculations(tmp_path):
    write_full_silver(tmp_path)
    built = build_all_dims(tmp_path)
    _build_all_facts(tmp_path, built)
    settings = gb.GoldSettings(readmission_qualifying_classes=("inpatient", "emergency"), readmission_windows=(7, 14, 30))
    built["mart_readmission"], _ = gb.build_mart_readmission(built, settings)

    mart, _ = gb.build_mart_monthly_kpis(built)
    jan = mart[mart["year_month"] == "2020-01"].iloc[0]
    assert jan["total_encounters"] == 2
    assert jan["inpatient_encounters"] == 1


# -- quality: schema drift / manifest / write scope ---------------------------


def test_quality_report_generation(tmp_path):
    silver_dir = tmp_path / "silver"
    gold_dir = tmp_path / "gold"
    write_full_silver(silver_dir)
    silver_manifest = make_silver_manifest(ALL_SILVER_DATASETS)

    manifest = gb.run_gold_build(silver_dir, gold_dir, silver_manifest=silver_manifest)
    report = gb.build_gold_quality_report(gold_dir, manifest)

    assert report["summary"]["total_checks"] > 0
    check_ids = {c["check_id"] for c in report["checks"]}
    assert "surrogate_key_unique:dim_patient" in check_ids
    assert "fact_key_not_null:fact_encounter" in check_ids


def test_quality_report_and_summary_files(tmp_path):
    silver_dir = tmp_path / "silver"
    gold_dir = tmp_path / "gold"
    reports_dir = tmp_path / "reports"
    write_full_silver(silver_dir)
    silver_manifest = make_silver_manifest(ALL_SILVER_DATASETS)
    (silver_dir / "silver_manifest.json").write_text(json.dumps(silver_manifest))

    manifest, quality_report, kpi_summary = gb.run_gold_pipeline(silver_dir, gold_dir, reports_dir)

    assert (gold_dir / gb.GOLD_MANIFEST_FILENAME).is_file()
    assert (reports_dir / gb.GOLD_QUALITY_REPORT_FILENAME).is_file()
    assert (reports_dir / gb.GOLD_QUALITY_SUMMARY_FILENAME).is_file()
    assert (reports_dir / gb.GOLD_KPI_SUMMARY_FILENAME).is_file()
    with (reports_dir / gb.GOLD_QUALITY_REPORT_FILENAME).open() as fh:
        assert json.load(fh) == quality_report


# -- incremental / force / --table / --mart ------------------------------------


def test_incremental_skip_when_checksum_unchanged(tmp_path):
    silver_dir = tmp_path / "silver"
    gold_dir = tmp_path / "gold"
    write_full_silver(silver_dir)
    silver_manifest = make_silver_manifest(ALL_SILVER_DATASETS)

    first = gb.run_gold_build(silver_dir, gold_dir, tables=["dim_patient"], silver_manifest=silver_manifest)
    assert first["tables"][0]["status"] == "processed"

    second = gb.run_gold_build(
        silver_dir, gold_dir, tables=["dim_patient"], silver_manifest=silver_manifest, previous_manifest=first,
    )
    assert second["tables"][0]["status"] == "skipped"


def test_force_rebuild_bypasses_skip(tmp_path):
    silver_dir = tmp_path / "silver"
    gold_dir = tmp_path / "gold"
    write_full_silver(silver_dir)
    silver_manifest = make_silver_manifest(ALL_SILVER_DATASETS)

    first = gb.run_gold_build(silver_dir, gold_dir, tables=["dim_patient"], silver_manifest=silver_manifest)
    second = gb.run_gold_build(
        silver_dir, gold_dir, tables=["dim_patient"], force=True,
        silver_manifest=silver_manifest, previous_manifest=first,
    )
    assert second["tables"][0]["status"] == "processed"


def test_dimension_change_triggers_dependent_fact_rebuild(tmp_path):
    silver_dir = tmp_path / "silver"
    gold_dir = tmp_path / "gold"
    write_full_silver(silver_dir)
    manifest_v1 = make_silver_manifest(ALL_SILVER_DATASETS, checksum="v1")

    first = gb.run_gold_build(silver_dir, gold_dir, silver_manifest=manifest_v1)
    assert next(e for e in first["tables"] if e["table"] == "fact_encounter")["status"] == "processed"

    manifest_v2 = make_silver_manifest(ALL_SILVER_DATASETS, checksum="v1")
    # Only patients' checksum changes.
    for entry in manifest_v2["datasets"]:
        if entry["dataset"] == "patients":
            entry["source_checksum"] = "v2-patients"

    second = gb.run_gold_build(silver_dir, gold_dir, silver_manifest=manifest_v2, previous_manifest=first)
    statuses = {e["table"]: e["status"] for e in second["tables"]}
    assert statuses["dim_patient"] == "processed"
    assert statuses["fact_encounter"] == "processed"  # depends transitively on dim_patient
    assert statuses["dim_organization"] == "skipped"  # unaffected


def test_single_table_build(tmp_path):
    silver_dir = tmp_path / "silver"
    gold_dir = tmp_path / "gold"
    write_full_silver(silver_dir)
    silver_manifest = make_silver_manifest(ALL_SILVER_DATASETS)

    manifest = gb.run_gold_build(silver_dir, gold_dir, tables=["dim_patient"], silver_manifest=silver_manifest)
    assert [e["table"] for e in manifest["tables"]] == ["dim_patient"]
    assert (gold_dir / "dim_patient.parquet").is_file()
    assert not (gold_dir / "fact_encounter.parquet").exists()


def test_single_mart_build_pulls_in_dependencies(tmp_path):
    silver_dir = tmp_path / "silver"
    gold_dir = tmp_path / "gold"
    write_full_silver(silver_dir)
    silver_manifest = make_silver_manifest(ALL_SILVER_DATASETS)

    manifest = gb.run_gold_build(silver_dir, gold_dir, marts=["mart_readmission"], silver_manifest=silver_manifest)
    tables_built = {e["table"] for e in manifest["tables"]}
    assert "mart_readmission" in tables_built
    assert "fact_encounter" in tables_built  # dependency pulled in automatically
    assert "dim_patient" in tables_built
    assert "mart_financial_performance" not in tables_built  # unrelated mart untouched


# -- write scope / upstream immutability ---------------------------------------


def test_writes_only_to_gold_and_reports(tmp_path):
    silver_dir = tmp_path / "silver"
    gold_dir = tmp_path / "gold"
    reports_dir = tmp_path / "reports"
    write_full_silver(silver_dir)
    silver_manifest = make_silver_manifest(ALL_SILVER_DATASETS)
    (silver_dir / "silver_manifest.json").write_text(json.dumps(silver_manifest))

    before_silver_files = set(silver_dir.iterdir())

    gb.run_gold_pipeline(silver_dir, gold_dir, reports_dir, tables=["dim_patient"])

    assert set(silver_dir.iterdir()) == before_silver_files
    assert (gold_dir / "dim_patient.parquet").exists()
    assert (gold_dir / gb.GOLD_MANIFEST_FILENAME).exists()


def test_silver_files_not_modified(tmp_path):
    silver_dir = tmp_path / "silver"
    gold_dir = tmp_path / "gold"
    write_full_silver(silver_dir)
    silver_manifest = make_silver_manifest(ALL_SILVER_DATASETS)
    (silver_dir / "silver_manifest.json").write_text(json.dumps(silver_manifest))

    patients_path = silver_dir / "patients.parquet"
    before_bytes = patients_path.read_bytes()

    gb.run_gold_pipeline(silver_dir, gold_dir, tmp_path / "reports", tables=["dim_patient"])

    assert patients_path.read_bytes() == before_bytes
