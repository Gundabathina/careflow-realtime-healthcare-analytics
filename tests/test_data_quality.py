"""Tests for careflow.profiling.data_quality.

All tests use temporary CSV fixtures. None of them depend on the
generated Synthea dataset.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd
import pytest

from careflow.profiling import data_quality as dq
from careflow.profiling.relationship_profiler import RelationshipConfig


def write_csv(path: Path, rows: list[list[str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerows(rows)
    return path


# -- date parsing -----------------------------------------------------------------


def test_valid_dates_all_parse(tmp_path):
    write_csv(tmp_path / "encounters.csv", [["Id", "START"], ["e1", "2020-01-01T10:00:00Z"], ["e2", "2020-02-01T10:00:00Z"]])

    result = dq._check_date_parses(
        tmp_path, "encounters.csv", "START", "r1", "START parses", "reason", "high",
        0.0, 1.0, 10, 50_000, required=True,
    )

    assert result["status"] == "pass"
    assert result["records_evaluated"] == 2
    assert result["records_failed"] == 0


def test_invalid_dates_are_counted(tmp_path):
    write_csv(tmp_path / "encounters.csv", [["Id", "START"], ["e1", "2020-01-01T10:00:00Z"], ["e2", "not-a-date"]])

    result = dq._check_date_parses(
        tmp_path, "encounters.csv", "START", "r1", "START parses", "reason", "high",
        0.0, 1.0, 10, 50_000, required=True,
    )

    assert result["records_failed"] == 1
    assert result["status"] in ("warning", "fail")


# -- date ordering ----------------------------------------------------------------


def test_stop_date_before_start_date_is_flagged(tmp_path):
    write_csv(tmp_path / "procedures.csv", [
        ["PATIENT", "ENCOUNTER", "START", "STOP"],
        ["p1", "e1", "2020-01-10", "2020-01-05"],  # STOP before START
        ["p2", "e2", "2020-01-01", "2020-01-02"],  # valid
    ])

    result = dq._check_date_order_same_file(
        tmp_path, "procedures.csv", "START", "STOP", "r1", "STOP not before START", "reason",
        "high", 1.0, 5.0, 10, 50_000, identifier_columns=("PATIENT", "ENCOUNTER"),
    )

    assert result["records_evaluated"] == 2
    assert result["records_failed"] == 1
    assert result["status"] == "fail"
    assert result["sample_failures"][0]["patient"] == "p1"


def test_no_stop_before_start_passes(tmp_path):
    write_csv(tmp_path / "medications.csv", [
        ["PATIENT", "ENCOUNTER", "START", "STOP"],
        ["p1", "e1", "2020-01-01", "2020-01-05"],
        ["p2", "e2", "2020-01-01", ""],
    ])

    result = dq._check_date_order_same_file(
        tmp_path, "medications.csv", "START", "STOP", "r1", "STOP not before START", "reason",
        "high", 1.0, 5.0, 10, 50_000,
    )

    assert result["records_evaluated"] == 1  # only rows with STOP present count
    assert result["records_failed"] == 0
    assert result["status"] == "pass"


def test_death_before_birth_is_flagged(tmp_path):
    write_csv(tmp_path / "patients.csv", [
        ["Id", "BIRTHDATE", "DEATHDATE"],
        ["p1", "2000-01-01", "1990-01-01"],  # death before birth: impossible
        ["p2", "2000-01-01", "2050-01-01"],
    ])

    result = dq._check_date_order_same_file(
        tmp_path, "patients.csv", "BIRTHDATE", "DEATHDATE", "r1", "DEATHDATE not before BIRTHDATE",
        "reason", "critical", 0.0, 1.0, 10, 50_000, identifier_columns=("Id",),
    )

    assert result["records_failed"] == 1
    assert result["status"] in ("warning", "fail")


# -- cross-file: birth date after encounter --------------------------------------


def test_birth_date_after_encounter_is_flagged(tmp_path):
    write_csv(tmp_path / "patients.csv", [["Id", "BIRTHDATE"], ["p1", "2020-06-01"]])
    write_csv(tmp_path / "encounters.csv", [
        ["Id", "PATIENT", "START"],
        ["e1", "p1", "2020-01-01T00:00:00Z"],  # before birth: impossible
        ["e2", "p1", "2021-01-01T00:00:00Z"],  # fine
    ])

    result = dq._check_birthdate_before_encounter(
        tmp_path, "patients.csv", "Id", "BIRTHDATE", "encounters.csv", "Id", "PATIENT", "START",
        "r1", "birth before encounter", "reason", "high", 1.0, 5.0, 10, 50_000,
    )

    assert result["records_evaluated"] == 2
    assert result["records_failed"] == 1
    assert result["sample_failures"][0]["patient_id"] == "p1"


def test_birth_date_before_all_encounters_passes(tmp_path):
    write_csv(tmp_path / "patients.csv", [["Id", "BIRTHDATE"], ["p1", "2000-01-01"]])
    write_csv(tmp_path / "encounters.csv", [["Id", "PATIENT", "START"], ["e1", "p1", "2020-01-01T00:00:00Z"]])

    result = dq._check_birthdate_before_encounter(
        tmp_path, "patients.csv", "Id", "BIRTHDATE", "encounters.csv", "Id", "PATIENT", "START",
        "r1", "birth before encounter", "reason", "high", 1.0, 5.0, 10, 50_000,
    )

    assert result["records_failed"] == 0
    assert result["status"] == "pass"


# -- numeric costs ------------------------------------------------------------------


def test_negative_costs_are_flagged(tmp_path):
    write_csv(tmp_path / "procedures.csv", [["BASE_COST"], ["100.0"], ["-50.0"], ["25.0"]])

    result = dq._check_numeric_non_negative(
        tmp_path, "procedures.csv", "BASE_COST", "r1", "numeric", "reason", "high", 0.0, 1.0, 10, 50_000,
    )

    assert result["records_evaluated"] == 3
    assert result["records_failed"] == 1
    assert result["status"] in ("warning", "fail")


def test_all_non_negative_costs_pass(tmp_path):
    write_csv(tmp_path / "procedures.csv", [["BASE_COST"], ["100.0"], ["0.0"], ["25.0"]])

    result = dq._check_numeric_non_negative(
        tmp_path, "procedures.csv", "BASE_COST", "r1", "numeric", "reason", "high", 0.0, 1.0, 10, 50_000,
    )

    assert result["records_failed"] == 0
    assert result["status"] == "pass"


def test_coverage_greater_than_total_cost_is_flagged(tmp_path):
    write_csv(tmp_path / "encounters.csv", [
        ["Id", "PAYER_COVERAGE", "TOTAL_CLAIM_COST"],
        ["e1", "500.0", "100.0"],  # coverage > total: impossible
        ["e2", "50.0", "100.0"],
    ])

    result = dq._check_coverage_not_exceeds_total(
        tmp_path, "encounters.csv", "PAYER_COVERAGE", "TOTAL_CLAIM_COST", "r1",
        "coverage <= total", "reason", "high", 1.0, 5.0, 10, 50_000, identifier_columns=("Id",),
    )

    assert result["records_evaluated"] == 2
    assert result["records_failed"] == 1
    assert result["sample_failures"][0]["id"] == "e1"


# -- uniqueness / completeness ----------------------------------------------------


def test_duplicate_ids_are_counted(tmp_path):
    write_csv(tmp_path / "patients.csv", [["Id"], ["p1"], ["p2"], ["p1"], ["p1"]])

    result = dq._check_unique(
        tmp_path, "patients.csv", "Id", "r1", "Id unique", "uniqueness", "reason", "critical",
        0.0, 1.0, 10, 50_000,
    )

    # p1 appears 3 times -> 2 duplicate occurrences.
    assert result["records_failed"] == 2
    assert result["status"] in ("warning", "fail")


def test_unique_ids_pass(tmp_path):
    write_csv(tmp_path / "patients.csv", [["Id"], ["p1"], ["p2"], ["p3"]])

    result = dq._check_unique(
        tmp_path, "patients.csv", "Id", "r1", "Id unique", "uniqueness", "reason", "critical",
        0.0, 1.0, 10, 50_000,
    )

    assert result["records_failed"] == 0
    assert result["status"] == "pass"


def test_null_critical_fields_are_flagged(tmp_path):
    write_csv(tmp_path / "encounters.csv", [["Id", "PATIENT"], ["e1", "p1"], ["e2", ""], ["e3", "p3"]])

    result = dq._check_not_null(
        tmp_path, "encounters.csv", "PATIENT", "r1", "PATIENT not null", "completeness", "reason",
        "high", 0.0, 1.0, 10, 50_000,
    )

    assert result["records_evaluated"] == 3
    assert result["records_failed"] == 1
    assert result["status"] in ("warning", "fail")


def test_no_nulls_passes(tmp_path):
    write_csv(tmp_path / "encounters.csv", [["Id", "PATIENT"], ["e1", "p1"], ["e2", "p2"]])

    result = dq._check_not_null(
        tmp_path, "encounters.csv", "PATIENT", "r1", "PATIENT not null", "completeness", "reason",
        "high", 0.0, 1.0, 10, 50_000,
    )

    assert result["records_failed"] == 0
    assert result["status"] == "pass"


# -- allowed values / encounter classes --------------------------------------------


def test_allowed_encounter_classes_pass(tmp_path):
    write_csv(tmp_path / "encounters.csv", [["Id", "ENCOUNTERCLASS"], ["e1", "ambulatory"], ["e2", "wellness"]])

    result = dq._check_allowed_values(
        tmp_path, "encounters.csv", "ENCOUNTERCLASS", ["ambulatory", "wellness"], "r1",
        "class allowed", "reason", "medium", 1.0, 5.0, 10, 50_000, identifier_columns=("Id",),
    )

    assert result["records_failed"] == 0
    assert result["status"] == "pass"


def test_unexpected_encounter_class_is_flagged(tmp_path):
    write_csv(tmp_path / "encounters.csv", [
        ["Id", "ENCOUNTERCLASS"], ["e1", "ambulatory"], ["e2", "not-a-real-class"],
    ])

    result = dq._check_allowed_values(
        tmp_path, "encounters.csv", "ENCOUNTERCLASS", ["ambulatory", "wellness"], "r1",
        "class allowed", "reason", "medium", 1.0, 5.0, 10, 50_000, identifier_columns=("Id",),
    )

    assert result["records_failed"] == 1
    assert result["sample_failures"][0]["value"] == "not-a-real-class"


# -- UUID format --------------------------------------------------------------------


def test_invalid_uuids_are_flagged(tmp_path):
    write_csv(tmp_path / "patients.csv", [
        ["Id"], ["aee7bbe1-0c45-c028-1e62-1f4cdb30c273"], ["not-a-uuid"],
    ])

    result = dq._check_uuid_format(
        tmp_path, "patients.csv", "Id", "r1", "format", "reason", "low", 1.0, 5.0, 10, 50_000,
    )

    assert result["records_evaluated"] == 2
    assert result["records_failed"] == 1


def test_valid_uuids_pass(tmp_path):
    write_csv(tmp_path / "patients.csv", [
        ["Id"], ["aee7bbe1-0c45-c028-1e62-1f4cdb30c273"], ["5e688e99-61b3-5c88-3f60-21df8aaced27"],
    ])

    result = dq._check_uuid_format(
        tmp_path, "patients.csv", "Id", "r1", "format", "reason", "low", 1.0, 5.0, 10, 50_000,
    )

    assert result["records_failed"] == 0
    assert result["status"] == "pass"


def test_zip_leading_zeros_not_invalidated(tmp_path):
    write_csv(tmp_path / "patients.csv", [["ZIP"], ["02138"], ["00000"], ["94103"]])

    result = dq._check_zip_text_format(
        tmp_path, "patients.csv", "ZIP", "r1", "format", "reason", "low", 1.0, 5.0, 10, 50_000,
    )

    assert result["records_failed"] == 0
    assert result["status"] == "pass"


# -- impossible ages ------------------------------------------------------------------


def test_impossible_age_over_max_is_flagged(tmp_path):
    write_csv(tmp_path / "patients.csv", [["Id", "BIRTHDATE"], ["p1", "1850-01-01"], ["p2", "2000-01-01"]])
    reference = pd.Timestamp("2026-01-01", tz="UTC")

    result = dq._check_impossible_age(
        tmp_path, "patients.csv", "BIRTHDATE", "DEATHDATE", 115, "r1", "age plausible", "reason",
        "medium", 1.0, 5.0, 10, 50_000, identifier_columns=("Id",), reference_date=reference,
    )

    assert result["records_evaluated"] == 2
    assert result["records_failed"] == 1
    assert result["sample_failures"][0]["id"] == "p1"


def test_plausible_ages_pass(tmp_path):
    write_csv(tmp_path / "patients.csv", [["Id", "BIRTHDATE"], ["p1", "1990-01-01"]])
    reference = pd.Timestamp("2026-01-01", tz="UTC")

    result = dq._check_impossible_age(
        tmp_path, "patients.csv", "BIRTHDATE", "DEATHDATE", 115, "r1", "age plausible", "reason",
        "medium", 1.0, 5.0, 10, 50_000, reference_date=reference,
    )

    assert result["records_failed"] == 0
    assert result["status"] == "pass"


# -- skip conditions ----------------------------------------------------------------


def test_missing_file_is_skipped(tmp_path):
    result = dq._check_not_null(
        tmp_path, "patients.csv", "Id", "r1", "Id not null", "completeness", "reason", "critical",
        0.0, 1.0, 10, 50_000,
    )

    assert result["status"] == "skipped"
    assert "missing" in result["skipped_reason"].lower()
    assert result["records_evaluated"] is None
    assert result["sample_failures"] == []


def test_missing_column_is_skipped(tmp_path):
    write_csv(tmp_path / "patients.csv", [["NOT_ID"], ["p1"]])

    result = dq._check_not_null(
        tmp_path, "patients.csv", "Id", "r1", "Id not null", "completeness", "reason", "critical",
        0.0, 1.0, 10, 50_000,
    )

    assert result["status"] == "skipped"
    assert "column" in result["skipped_reason"].lower()


def test_required_foreign_keys_exist_flags_missing_column(tmp_path):
    write_csv(tmp_path / "encounters.csv", [["Id", "NOT_PATIENT"], ["e1", "p1"]])
    relationships = [
        RelationshipConfig("encounters.PATIENT -> patients.Id", "patients.csv", "Id", "encounters.csv", "PATIENT"),
    ]

    result = dq._check_required_foreign_keys_exist(
        tmp_path, relationships, "r1", "FK columns exist", "reason", "critical", 0.0, 1.0, 10,
    )

    assert result["records_evaluated"] == 1
    assert result["records_failed"] == 1
    assert result["status"] in ("warning", "fail")


def test_required_foreign_keys_exist_skips_when_no_child_files(tmp_path):
    relationships = [
        RelationshipConfig("encounters.PATIENT -> patients.Id", "patients.csv", "Id", "encounters.csv", "PATIENT"),
    ]

    result = dq._check_required_foreign_keys_exist(
        tmp_path, relationships, "r1", "FK columns exist", "reason", "critical", 0.0, 1.0, 10,
    )

    assert result["status"] == "skipped"


def test_malformed_csv_is_skipped_without_crashing(tmp_path):
    # An unbalanced quote breaks CSV tokenization entirely, even with usecols.
    (tmp_path / "patients.csv").write_text('Id,BIRTHDATE\np1,"2020-01-01\np2,2020-01-01\n')

    result = dq._check_not_null(
        tmp_path, "patients.csv", "Id", "r1", "Id not null", "completeness", "reason", "critical",
        0.0, 1.0, 10, 50_000,
    )

    assert result["status"] == "skipped"
    assert result["skipped_reason"]


# -- thresholds -------------------------------------------------------------------


def test_warning_threshold_status(tmp_path):
    rows = [["Id"]] + [[f"p{i}"] for i in range(100)]
    rows[5] = [""]  # 1 null out of 100 -> 1% failure
    write_csv(tmp_path / "patients.csv", rows)

    result = dq._check_not_null(
        tmp_path, "patients.csv", "Id", "r1", "Id not null", "completeness", "reason", "critical",
        warning_pct=0.5, fail_pct=5.0, max_samples=10, chunk_size=50_000,
    )

    assert result["status"] == "warning"


def test_fail_threshold_status(tmp_path):
    rows = [["Id"]] + [[f"p{i}"] for i in range(10)]
    rows[1] = [""]
    rows[2] = [""]
    write_csv(tmp_path / "patients.csv", rows)

    result = dq._check_not_null(
        tmp_path, "patients.csv", "Id", "r1", "Id not null", "completeness", "reason", "critical",
        warning_pct=0.5, fail_pct=5.0, max_samples=10, chunk_size=50_000,
    )

    assert result["status"] == "fail"


# -- full report orchestration -----------------------------------------------------


def test_build_data_quality_report_counts_rules(tmp_path):
    write_csv(tmp_path / "patients.csv", [["Id", "BIRTHDATE"], ["p1", "1990-01-01"]])
    settings = dq.DataQualitySettings(
        chunk_size=50_000, max_samples=10, warning_threshold_pct=1.0, fail_threshold_pct=5.0,
        max_patient_age_years=115, allowed_encounter_classes=("ambulatory",),
        critical_columns=(("patients.csv", "BIRTHDATE"),), cost_fields=(), uuid_columns=(), zip_columns=(),
    )
    report = dq.build_data_quality_report(tmp_path, settings=settings, relationships=[])

    assert report["data_quality_version"] == dq.DATA_QUALITY_VERSION
    assert report["summary"]["total_rules"] == len(report["rules"])
    assert report["summary"]["total_rules"] > 0
    statuses = {r["status"] for r in report["rules"]}
    assert statuses <= {"pass", "warning", "fail", "skipped"}
    assert report["summary"]["skipped"] > 0  # encounters.csv absent -> most rules skipped


# -- output generation --------------------------------------------------------------


def test_json_report_generation(tmp_path):
    write_csv(tmp_path / "patients.csv", [["Id"], ["p1"], ["p2"]])
    settings = dq.DataQualitySettings(
        chunk_size=50_000, max_samples=10, warning_threshold_pct=1.0, fail_threshold_pct=5.0,
        max_patient_age_years=115, allowed_encounter_classes=(),
        critical_columns=(), cost_fields=(), uuid_columns=(), zip_columns=(),
    )
    report = dq.build_data_quality_report(tmp_path, settings=settings, relationships=[])
    output_path = tmp_path / "out" / dq.DATA_QUALITY_REPORT_FILENAME
    dq.write_data_quality_report_json(report, output_path)

    assert output_path.is_file()
    with output_path.open() as fh:
        loaded = json.load(fh)
    assert loaded == report


def test_csv_summary_generation(tmp_path):
    write_csv(tmp_path / "patients.csv", [["Id"], ["p1"], ["p2"]])
    settings = dq.DataQualitySettings(
        chunk_size=50_000, max_samples=10, warning_threshold_pct=1.0, fail_threshold_pct=5.0,
        max_patient_age_years=115, allowed_encounter_classes=(),
        critical_columns=(), cost_fields=(), uuid_columns=(), zip_columns=(),
    )
    report = dq.build_data_quality_report(tmp_path, settings=settings, relationships=[])
    output_path = tmp_path / "out" / dq.DATA_QUALITY_SUMMARY_FILENAME
    dq.write_data_quality_summary_csv(report, output_path)

    assert output_path.is_file()
    with output_path.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0].keys() == set(dq.DATA_QUALITY_SUMMARY_FIELDNAMES)
    assert len(rows) == len(report["rules"])


def test_failed_record_samples_combines_rules_and_relationships(tmp_path):
    write_csv(tmp_path / "patients.csv", [["Id"], ["p1"], ["p1"]])
    settings = dq.DataQualitySettings(
        chunk_size=50_000, max_samples=10, warning_threshold_pct=0.0, fail_threshold_pct=1.0,
        max_patient_age_years=115, allowed_encounter_classes=(),
        critical_columns=(), cost_fields=(), uuid_columns=(), zip_columns=(),
    )
    # Only include the uniqueness rule by using an otherwise-empty relationship list.
    report = dq.build_data_quality_report(tmp_path, settings=settings, relationships=[])

    relationship_summary = {
        "relationships": [
            {
                "relationship": "x -> y", "child_file": "x.csv", "child_key": "y",
                "status": "fail", "sample_unmatched_values": ["bad-1"],
            }
        ]
    }

    samples = dq.build_failed_record_samples(report, relationship_summary)

    assert any(r["status"] == "fail" for r in samples["rule_failures"])
    assert samples["relationship_failures"][0]["relationship"] == "x -> y"


def test_run_data_quality_validation_writes_all_files_and_does_not_modify_source(tmp_path):
    source_dir = tmp_path / "csv"
    source_dir.mkdir()
    write_csv(source_dir / "patients.csv", [["Id"], ["p1"], ["p2"]])
    before = (source_dir / "patients.csv").read_text()

    settings = dq.DataQualitySettings(
        chunk_size=50_000, max_samples=10, warning_threshold_pct=1.0, fail_threshold_pct=5.0,
        max_patient_age_years=115, allowed_encounter_classes=(),
        critical_columns=(), cost_fields=(), uuid_columns=(), zip_columns=(),
    )
    output_dir = tmp_path / "reports"
    report = dq.run_data_quality_validation(
        csv_dir=source_dir, output_dir=output_dir, settings=settings, relationships=[]
    )

    assert (output_dir / dq.DATA_QUALITY_REPORT_FILENAME).is_file()
    assert (output_dir / dq.DATA_QUALITY_SUMMARY_FILENAME).is_file()
    assert (output_dir / dq.FAILED_RECORD_SAMPLES_FILENAME).is_file()
    assert report["summary"]["total_rules"] > 0
    assert (source_dir / "patients.csv").read_text() == before
