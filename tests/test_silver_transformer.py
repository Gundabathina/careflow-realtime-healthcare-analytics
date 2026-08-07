"""Tests for careflow.transformation.silver_transformer.

All tests use temporary Parquet fixtures written to tmp_path. None of
them depend on the real Bronze/Synthea dataset.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from careflow.transformation import schema_registry as sr
from careflow.transformation import silver_transformer as st


# -- fixtures -----------------------------------------------------------------


def make_settings(reference_date: str = "2025-01-01T00:00:00Z") -> st.SilverSettings:
    return st.SilverSettings(reference_date=pd.Timestamp(reference_date, tz="UTC"))


def make_bronze_entry(filename: str, checksum: str = "checksum-1", status: str = "ingested") -> dict:
    return {
        "filename": filename,
        "status": status,
        "source_checksum": checksum,
        "bronze_checksum": "bronze-checksum-1",
        "bronze_size_bytes": 100,
        "schema": {},
        "cast_failures": {},
        "reason": None,
    }


def make_bronze_manifest(files: list[dict], ingested_at: str = "2026-01-01T00:00:00Z") -> dict:
    return {"bronze_version": "1.0.0", "ingested_at_utc": ingested_at, "files": files}


def write_bronze_parquet(bronze_dir: Path, filename: str, df: pd.DataFrame) -> Path:
    bronze_dir.mkdir(parents=True, exist_ok=True)
    path = bronze_dir / filename
    df.to_parquet(path, engine="pyarrow", index=False)
    return path


def make_patients_df(n_dupe_id: bool = False) -> pd.DataFrame:
    ids = ["p1", "p2", "p3"]
    if n_dupe_id:
        ids = ["p1", "p1", "p3"]
    return pd.DataFrame({
        "Id": ids,
        "BIRTHDATE": pd.to_datetime(["1990-01-01", "2000-06-15", "1950-03-20"], utc=True),
        "DEATHDATE": pd.to_datetime([None, None, "2020-01-01"], utc=True),
        "SSN": ["999-11-2222", "999-33-4444", "999-55-6666"],
        "DRIVERS": [None, None, None],
        "PASSPORT": [None, None, None],
        "PREFIX": [None, None, None],
        "FIRST": ["A", "B", "C"],
        "MIDDLE": [None, None, None],
        "LAST": ["X", "Y", "Z"],
        "SUFFIX": [None, None, None],
        "MAIDEN": [None, None, None],
        "MARITAL": [None, None, None],
        "RACE": ["White", "Black", "Asian"],
        "ETHNICITY": ["Nonhispanic", "Hispanic", "Nonhispanic"],
        "GENDER": ["m", "F", "m"],
        "BIRTHPLACE": [None, None, None],
        "ADDRESS": [None, None, None],
        "CITY": [None, None, None],
        "STATE": [None, None, None],
        "COUNTY": [None, None, None],
        "FIPS": ["25025", "25025", "25025"],
        "ZIP": ["02138", "00000", "01607"],
        "LAT": ["42.1", "42.2", "42.3"],
        "LON": ["-71.1", "-71.2", "-71.3"],
        "HEALTHCARE_EXPENSES": ["1000.0", "2000.0", "3000.0"],
        "HEALTHCARE_COVERAGE": ["500.0", "600.0", "700.0"],
        "INCOME": ["50000", "60000", "70000"],
    })


def make_encounters_df(stop_before_start: bool = False) -> pd.DataFrame:
    start = ["2020-01-01T10:00:00Z", "2020-02-01T08:00:00Z"]
    stop = ["2020-01-01T11:30:00Z", "2020-02-01T07:00:00Z" if stop_before_start else "2020-02-01T09:00:00Z"]
    return pd.DataFrame({
        "Id": ["e1", "e2"],
        "START": pd.to_datetime(start, utc=True),
        "STOP": pd.to_datetime(stop, utc=True),
        "PATIENT": ["p1", "p2"],
        "ORGANIZATION": ["o1", "o2"],
        "PROVIDER": ["pr1", "pr2"],
        "PAYER": ["pay1", "pay2"],
        "ENCOUNTERCLASS": ["ambulatory", "emergency"],
        "CODE": ["123", "456"],
        "DESCRIPTION": ["Visit A", "Visit B"],
        "BASE_ENCOUNTER_COST": [100.0, 200.0],
        "TOTAL_CLAIM_COST": [150.0, 250.0],
        "PAYER_COVERAGE": [50.0, 75.0],
        "REASONCODE": [None, None],
        "REASONDESCRIPTION": [None, None],
    })


def make_conditions_df() -> pd.DataFrame:
    return pd.DataFrame({
        "START": pd.to_datetime(["2020-01-01", "2020-02-01"], utc=True),
        "STOP": pd.to_datetime(["2020-06-01", None], utc=True),
        "PATIENT": ["p1", "p2"],
        "ENCOUNTER": ["e1", "e2"],
        "SYSTEM": ["SNOMED-CT", "SNOMED-CT"],
        "CODE": ["11111", "22222"],
        "DESCRIPTION": ["Cond A", "Cond B"],
    })


def make_medications_df() -> pd.DataFrame:
    return pd.DataFrame({
        "START": pd.to_datetime(["2020-01-01", "2020-02-01"], utc=True),
        "STOP": pd.to_datetime(["2020-01-10", None], utc=True),
        "PATIENT": ["p1", "p2"],
        "PAYER": ["pay1", "pay2"],
        "ENCOUNTER": ["e1", "e2"],
        "CODE": ["1", "2"],
        "DESCRIPTION": ["Med A", "Med B"],
        "BASE_COST": [10.0, 20.0],
        "PAYER_COVERAGE": [5.0, 10.0],
        "DISPENSES": [1, 2],
        "TOTALCOST": [10.0, 40.0],
        "REASONCODE": [None, None],
        "REASONDESCRIPTION": [None, None],
    })


def make_observations_df() -> pd.DataFrame:
    return pd.DataFrame({
        "DATE": pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-03"], utc=True),
        "PATIENT": ["p1", "p1", "p1"],
        "ENCOUNTER": ["e1", "e1", "e1"],
        "CATEGORY": ["vital-signs"] * 3,
        "CODE": ["8302-2", "8302-2", "72514-3"],
        "DESCRIPTION": ["Height", "Height", "Pain severity"],
        "VALUE": ["170.5", "not-numeric", "moderate"],
        "UNITS": ["cm", "cm", None],
        "TYPE": ["numeric", "numeric", "text"],
    })


# -- column renaming / typing / identifier preservation -----------------------


def test_column_renaming(tmp_path):
    bronze_dir = tmp_path / "bronze"
    write_bronze_parquet(bronze_dir, "patients.parquet", make_patients_df())
    entry = make_bronze_entry("patients.csv")

    result = st.transform_dataset(
        bronze_dir, sr.SCHEMA_REGISTRY["patients"], entry, make_settings(),
        tmp_path / "silver", "2026-01-01T00:00:00Z", datetime.now(timezone.utc),
    )

    assert result["status"] == "processed"
    df = pd.read_parquet(tmp_path / "silver" / "patients.parquet")
    assert "patient_id" in df.columns
    assert "Id" not in df.columns
    assert "birthdate" in df.columns


def test_patient_date_parsing(tmp_path):
    bronze_dir = tmp_path / "bronze"
    write_bronze_parquet(bronze_dir, "patients.parquet", make_patients_df())
    entry = make_bronze_entry("patients.csv")

    st.transform_dataset(
        bronze_dir, sr.SCHEMA_REGISTRY["patients"], entry, make_settings(),
        tmp_path / "silver", "2026-01-01T00:00:00Z", datetime.now(timezone.utc),
    )

    df = pd.read_parquet(tmp_path / "silver" / "patients.parquet")
    assert pd.api.types.is_datetime64_any_dtype(df["birthdate"])
    assert str(df["birthdate"].dt.tz) == "UTC"


def test_patient_age_derivation(tmp_path):
    bronze_dir = tmp_path / "bronze"
    write_bronze_parquet(bronze_dir, "patients.parquet", make_patients_df())
    entry = make_bronze_entry("patients.csv")
    settings = make_settings(reference_date="2025-01-01T00:00:00Z")

    st.transform_dataset(
        bronze_dir, sr.SCHEMA_REGISTRY["patients"], entry, settings,
        tmp_path / "silver", "2026-01-01T00:00:00Z", datetime.now(timezone.utc),
    )

    df = pd.read_parquet(tmp_path / "silver" / "patients.parquet").set_index("patient_id")
    assert df.loc["p1", "age_at_reference_date"] == 35  # born 1990-01-01, ref 2025-01-01
    assert df.loc["p1", "age_group"] == "35-49"
    assert df.loc["p1", "is_deceased"] is False or df.loc["p1", "is_deceased"] == False  # noqa: E712
    assert df.loc["p3", "is_deceased"] == True  # noqa: E712  (has DEATHDATE)


def test_zip_leading_zero_preservation(tmp_path):
    bronze_dir = tmp_path / "bronze"
    write_bronze_parquet(bronze_dir, "patients.parquet", make_patients_df())
    entry = make_bronze_entry("patients.csv")

    st.transform_dataset(
        bronze_dir, sr.SCHEMA_REGISTRY["patients"], entry, make_settings(),
        tmp_path / "silver", "2026-01-01T00:00:00Z", datetime.now(timezone.utc),
    )

    df = pd.read_parquet(tmp_path / "silver" / "patients.parquet")
    assert df.set_index("patient_id").loc["p2", "zip"] == "00000"
    assert not pd.api.types.is_numeric_dtype(df["zip"])


def test_gender_normalization(tmp_path):
    bronze_dir = tmp_path / "bronze"
    write_bronze_parquet(bronze_dir, "patients.parquet", make_patients_df())
    entry = make_bronze_entry("patients.csv")

    st.transform_dataset(
        bronze_dir, sr.SCHEMA_REGISTRY["patients"], entry, make_settings(),
        tmp_path / "silver", "2026-01-01T00:00:00Z", datetime.now(timezone.utc),
    )

    df = pd.read_parquet(tmp_path / "silver" / "patients.parquet").set_index("patient_id")
    assert df.loc["p1", "gender"] == "M"
    assert df.loc["p2", "gender"] == "F"


def test_schema_enforcement_types(tmp_path):
    bronze_dir = tmp_path / "bronze"
    write_bronze_parquet(bronze_dir, "encounters.parquet", make_encounters_df())
    entry = make_bronze_entry("encounters.csv")

    st.transform_dataset(
        bronze_dir, sr.SCHEMA_REGISTRY["encounters"], entry, make_settings(),
        tmp_path / "silver", "2026-01-01T00:00:00Z", datetime.now(timezone.utc),
    )

    df = pd.read_parquet(tmp_path / "silver" / "encounters.parquet")
    assert pd.api.types.is_datetime64_any_dtype(df["start"])
    assert pd.api.types.is_numeric_dtype(df["base_encounter_cost"])
    assert not pd.api.types.is_numeric_dtype(df["patient_id"])


# -- duplicate removal ----------------------------------------------------------


def test_duplicate_row_removal(tmp_path):
    bronze_dir = tmp_path / "bronze"
    df = make_conditions_df()
    df_with_dupe = pd.concat([df, df.iloc[[0]]], ignore_index=True)
    write_bronze_parquet(bronze_dir, "conditions.parquet", df_with_dupe)
    entry = make_bronze_entry("conditions.csv")

    result = st.transform_dataset(
        bronze_dir, sr.SCHEMA_REGISTRY["conditions"], entry, make_settings(),
        tmp_path / "silver", "2026-01-01T00:00:00Z", datetime.now(timezone.utc),
    )

    assert result["source_row_count"] == 3
    assert result["duplicate_rows_removed"] == 1
    assert result["target_row_count"] == 2


# -- encounters -------------------------------------------------------------------


def test_encounter_duration(tmp_path):
    bronze_dir = tmp_path / "bronze"
    write_bronze_parquet(bronze_dir, "encounters.parquet", make_encounters_df())
    entry = make_bronze_entry("encounters.csv")

    st.transform_dataset(
        bronze_dir, sr.SCHEMA_REGISTRY["encounters"], entry, make_settings(),
        tmp_path / "silver", "2026-01-01T00:00:00Z", datetime.now(timezone.utc),
    )

    df = pd.read_parquet(tmp_path / "silver" / "encounters.parquet").set_index("encounter_id")
    assert df.loc["e1", "encounter_duration_minutes"] == pytest.approx(90.0)
    assert df.loc["e1", "is_inpatient"] == False  # noqa: E712
    assert df.loc["e2", "is_emergency"] == True  # noqa: E712


def test_encounter_stop_before_start_flagged(tmp_path):
    bronze_dir = tmp_path / "bronze"
    write_bronze_parquet(bronze_dir, "encounters.parquet", make_encounters_df(stop_before_start=True))
    entry = make_bronze_entry("encounters.csv")

    st.transform_dataset(
        bronze_dir, sr.SCHEMA_REGISTRY["encounters"], entry, make_settings(),
        tmp_path / "silver", "2026-01-01T00:00:00Z", datetime.now(timezone.utc),
    )

    df = pd.read_parquet(tmp_path / "silver" / "encounters.parquet").set_index("encounter_id")
    assert df.loc["e2", "stop_before_start"] == True  # noqa: E712
    assert pd.isna(df.loc["e2", "encounter_duration_minutes"])
    # Not silently dropped -- the row is still present.
    assert "e2" in df.index


# -- conditions / medications -----------------------------------------------------


def test_condition_active_flag(tmp_path):
    bronze_dir = tmp_path / "bronze"
    write_bronze_parquet(bronze_dir, "conditions.parquet", make_conditions_df())
    entry = make_bronze_entry("conditions.csv")

    st.transform_dataset(
        bronze_dir, sr.SCHEMA_REGISTRY["conditions"], entry, make_settings(),
        tmp_path / "silver", "2026-01-01T00:00:00Z", datetime.now(timezone.utc),
    )

    df = pd.read_parquet(tmp_path / "silver" / "conditions.parquet")
    df = df.set_index("patient_id")
    assert df.loc["p1", "is_active"] == False  # noqa: E712  (has STOP)
    assert df.loc["p2", "is_active"] == True  # noqa: E712  (no STOP)


def test_medication_active_flag(tmp_path):
    bronze_dir = tmp_path / "bronze"
    write_bronze_parquet(bronze_dir, "medications.parquet", make_medications_df())
    entry = make_bronze_entry("medications.csv")

    st.transform_dataset(
        bronze_dir, sr.SCHEMA_REGISTRY["medications"], entry, make_settings(),
        tmp_path / "silver", "2026-01-01T00:00:00Z", datetime.now(timezone.utc),
    )

    df = pd.read_parquet(tmp_path / "silver" / "medications.parquet").set_index("patient_id")
    assert df.loc["p1", "is_active"] == False  # noqa: E712
    assert df.loc["p2", "is_active"] == True  # noqa: E712
    assert df.loc["p1", "medication_duration_days"] == 9


# -- observations -------------------------------------------------------------------


def test_observation_numeric_parsing(tmp_path):
    bronze_dir = tmp_path / "bronze"
    write_bronze_parquet(bronze_dir, "observations.parquet", make_observations_df())
    entry = make_bronze_entry("observations.csv")

    st.transform_dataset(
        bronze_dir, sr.SCHEMA_REGISTRY["observations"], entry, make_settings(),
        tmp_path / "silver", "2026-01-01T00:00:00Z", datetime.now(timezone.utc),
    )

    df = pd.read_parquet(tmp_path / "silver" / "observations.parquet")
    assert df.loc[df["value"] == "170.5", "numeric_value"].iloc[0] == pytest.approx(170.5)
    assert pd.isna(df.loc[df["value"] == "not-numeric", "numeric_value"].iloc[0])
    assert pd.isna(df.loc[df["value"] == "moderate", "numeric_value"].iloc[0])
    # Raw value retained regardless of parse outcome.
    assert set(df["value"]) == {"170.5", "not-numeric", "moderate"}


# -- missing columns / schema drift -----------------------------------------------


def test_missing_required_source_columns_fails_gracefully(tmp_path):
    bronze_dir = tmp_path / "bronze"
    df = make_patients_df().drop(columns=["BIRTHDATE"])
    write_bronze_parquet(bronze_dir, "patients.parquet", df)
    entry = make_bronze_entry("patients.csv")

    result = st.transform_dataset(
        bronze_dir, sr.SCHEMA_REGISTRY["patients"], entry, make_settings(),
        tmp_path / "silver", "2026-01-01T00:00:00Z", datetime.now(timezone.utc),
    )

    assert result["status"] == "failed"
    assert "BIRTHDATE" in result["error"]
    assert not (tmp_path / "silver" / "patients.parquet").exists()


# -- incremental / checksum-based skip / force / --dataset ------------------------


def test_checksum_based_skip(tmp_path):
    bronze_dir = tmp_path / "bronze"
    silver_dir = tmp_path / "silver"
    write_bronze_parquet(bronze_dir, "patients.parquet", make_patients_df())
    bronze_manifest = make_bronze_manifest([make_bronze_entry("patients.csv", checksum="same-checksum")])

    first = st.run_silver_build(bronze_dir, silver_dir, datasets=["patients"], bronze_manifest=bronze_manifest)
    assert first["datasets"][0]["status"] == "processed"

    second = st.run_silver_build(
        bronze_dir, silver_dir, datasets=["patients"],
        bronze_manifest=bronze_manifest, previous_manifest=first,
    )
    assert second["datasets"][0]["status"] == "skipped"
    assert second["datasets"][0]["source_checksum"] == "same-checksum"


def test_force_reprocessing_bypasses_skip(tmp_path):
    bronze_dir = tmp_path / "bronze"
    silver_dir = tmp_path / "silver"
    write_bronze_parquet(bronze_dir, "patients.parquet", make_patients_df())
    bronze_manifest = make_bronze_manifest([make_bronze_entry("patients.csv", checksum="same-checksum")])

    first = st.run_silver_build(bronze_dir, silver_dir, datasets=["patients"], bronze_manifest=bronze_manifest)
    second = st.run_silver_build(
        bronze_dir, silver_dir, datasets=["patients"], force=True,
        bronze_manifest=bronze_manifest, previous_manifest=first,
    )
    assert second["datasets"][0]["status"] == "processed"


def test_checksum_change_triggers_reprocessing(tmp_path):
    bronze_dir = tmp_path / "bronze"
    silver_dir = tmp_path / "silver"
    write_bronze_parquet(bronze_dir, "patients.parquet", make_patients_df())
    manifest_v1 = make_bronze_manifest([make_bronze_entry("patients.csv", checksum="checksum-v1")])
    first = st.run_silver_build(bronze_dir, silver_dir, datasets=["patients"], bronze_manifest=manifest_v1)

    manifest_v2 = make_bronze_manifest([make_bronze_entry("patients.csv", checksum="checksum-v2")])
    second = st.run_silver_build(
        bronze_dir, silver_dir, datasets=["patients"],
        bronze_manifest=manifest_v2, previous_manifest=first,
    )
    assert second["datasets"][0]["status"] == "processed"
    assert second["datasets"][0]["source_checksum"] == "checksum-v2"


def test_single_dataset_run_leaves_others_untouched(tmp_path):
    bronze_dir = tmp_path / "bronze"
    silver_dir = tmp_path / "silver"
    write_bronze_parquet(bronze_dir, "patients.parquet", make_patients_df())
    write_bronze_parquet(bronze_dir, "encounters.parquet", make_encounters_df())
    bronze_manifest = make_bronze_manifest([
        make_bronze_entry("patients.csv"), make_bronze_entry("encounters.csv"),
    ])

    first = st.run_silver_build(bronze_dir, silver_dir, datasets=["patients"], bronze_manifest=bronze_manifest)
    assert [e["dataset"] for e in first["datasets"]] == ["patients"]
    assert not (silver_dir / "encounters.parquet").exists()

    write_silver_manifest_for_next_run = first
    second = st.run_silver_build(
        bronze_dir, silver_dir, datasets=["encounters"],
        bronze_manifest=bronze_manifest, previous_manifest=write_silver_manifest_for_next_run,
    )
    datasets_present = {e["dataset"] for e in second["datasets"]}
    assert datasets_present == {"patients", "encounters"}
    statuses = {e["dataset"]: e["status"] for e in second["datasets"]}
    assert statuses["encounters"] == "processed"
    assert statuses["patients"] == "processed"  # carried forward from the first run's entry


# -- manifest / quality report output generation -----------------------------------


def test_silver_manifest_json_generation(tmp_path):
    bronze_dir = tmp_path / "bronze"
    silver_dir = tmp_path / "silver"
    write_bronze_parquet(bronze_dir, "patients.parquet", make_patients_df())
    bronze_manifest = make_bronze_manifest([make_bronze_entry("patients.csv")])

    manifest = st.run_silver_build(bronze_dir, silver_dir, datasets=["patients"], bronze_manifest=bronze_manifest)
    output_path = silver_dir / st.SILVER_MANIFEST_FILENAME
    st.write_silver_manifest_json(manifest, output_path)

    assert output_path.is_file()
    with output_path.open() as fh:
        loaded = json.load(fh)
    assert loaded == manifest
    for field in ("run_id", "started_at_utc", "completed_at_utc", "transformation_version", "schema_version"):
        assert field in loaded


def test_quality_report_generation(tmp_path):
    bronze_dir = tmp_path / "bronze"
    silver_dir = tmp_path / "silver"
    write_bronze_parquet(bronze_dir, "patients.parquet", make_patients_df())
    bronze_manifest = make_bronze_manifest([make_bronze_entry("patients.csv")])

    manifest = st.run_silver_build(bronze_dir, silver_dir, datasets=["patients"], bronze_manifest=bronze_manifest)
    report = st.build_silver_quality_report(silver_dir, manifest)

    assert report["summary"]["total_checks"] > 0
    check_ids = {c["check_id"] for c in report["checks"]}
    assert "primary_key_not_null:patients" in check_ids
    assert "primary_key_unique:patients" in check_ids

    json_path = tmp_path / "reports" / st.SILVER_QUALITY_REPORT_FILENAME
    csv_path = tmp_path / "reports" / st.SILVER_QUALITY_SUMMARY_FILENAME
    st.write_silver_quality_report_json(report, json_path)
    st.write_silver_quality_summary_csv(report, csv_path)

    assert json_path.is_file()
    assert csv_path.is_file()
    with json_path.open() as fh:
        assert json.load(fh) == report


def test_primary_key_uniqueness_check_flags_duplicates(tmp_path):
    bronze_dir = tmp_path / "bronze"
    silver_dir = tmp_path / "silver"
    write_bronze_parquet(bronze_dir, "patients.parquet", make_patients_df(n_dupe_id=True))
    bronze_manifest = make_bronze_manifest([make_bronze_entry("patients.csv")])

    manifest = st.run_silver_build(bronze_dir, silver_dir, datasets=["patients"], bronze_manifest=bronze_manifest)
    # patients-specific dedup on patient_id should have already collapsed
    # the duplicate before the uniqueness check even runs.
    assert manifest["datasets"][0]["target_row_count"] == 2
    report = st.build_silver_quality_report(silver_dir, manifest)
    pk_check = next(c for c in report["checks"] if c["check_id"] == "primary_key_unique:patients")
    assert pk_check["status"] == "pass"


# -- write scope / immutability ----------------------------------------------------


def test_run_silver_pipeline_writes_only_expected_locations(tmp_path):
    bronze_dir = tmp_path / "bronze"
    silver_dir = tmp_path / "silver"
    reports_dir = tmp_path / "reports"
    write_bronze_parquet(bronze_dir, "patients.parquet", make_patients_df())
    bronze_manifest = make_bronze_manifest([make_bronze_entry("patients.csv")])
    (bronze_dir / "bronze_manifest.json").write_text(json.dumps(bronze_manifest))

    before_bronze_files = set(bronze_dir.iterdir())

    manifest, report = st.run_silver_pipeline(
        bronze_dir=bronze_dir, silver_dir=silver_dir, reports_dir=reports_dir, datasets=["patients"],
    )

    assert set(bronze_dir.iterdir()) == before_bronze_files  # nothing added/removed in bronze
    assert set(silver_dir.glob("*.parquet")) == {silver_dir / "patients.parquet"}
    assert (silver_dir / st.SILVER_MANIFEST_FILENAME).is_file()
    assert (reports_dir / st.SILVER_QUALITY_REPORT_FILENAME).is_file()
    assert (reports_dir / st.SILVER_QUALITY_SUMMARY_FILENAME).is_file()
    # Nothing else under reports_dir.
    assert set(reports_dir.iterdir()) == {
        reports_dir / st.SILVER_QUALITY_REPORT_FILENAME,
        reports_dir / st.SILVER_QUALITY_SUMMARY_FILENAME,
    }


def test_bronze_files_are_not_modified(tmp_path):
    bronze_dir = tmp_path / "bronze"
    silver_dir = tmp_path / "silver"
    bronze_path = write_bronze_parquet(bronze_dir, "patients.parquet", make_patients_df())
    bronze_manifest_path = bronze_dir / "bronze_manifest.json"
    bronze_manifest_path.write_text(json.dumps(make_bronze_manifest([make_bronze_entry("patients.csv")])))

    before_bytes = bronze_path.read_bytes()
    before_manifest_bytes = bronze_manifest_path.read_bytes()

    st.run_silver_pipeline(
        bronze_dir=bronze_dir, silver_dir=silver_dir, reports_dir=tmp_path / "reports", datasets=["patients"],
    )

    assert bronze_path.read_bytes() == before_bytes
    assert bronze_manifest_path.read_bytes() == before_manifest_bytes
