"""Tests for careflow.profiling.relationship_profiler.

All tests use temporary CSV fixtures. None of them depend on the
generated Synthea dataset.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from careflow.profiling import relationship_profiler as rp


def write_csv(path: Path, rows: list[list[str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerows(rows)
    return path


def make_relationship(**overrides) -> rp.RelationshipConfig:
    defaults = dict(
        name="encounters.PATIENT -> patients.Id",
        parent_file="patients.csv",
        parent_key="Id",
        child_file="encounters.csv",
        child_key="PATIENT",
    )
    defaults.update(overrides)
    return rp.RelationshipConfig(**defaults)


# -- valid / unmatched / null foreign keys ------------------------------------


def test_valid_foreign_keys_all_match(tmp_path):
    write_csv(tmp_path / "patients.csv", [["Id"], ["p1"], ["p2"], ["p3"]])
    write_csv(tmp_path / "encounters.csv", [["Id", "PATIENT"], ["e1", "p1"], ["e2", "p2"], ["e3", "p3"]])

    result = rp.evaluate_relationship(tmp_path, make_relationship())

    assert result["status"] == "pass"
    assert result["records_evaluated"] == 3
    assert result["non_null_foreign_keys"] == 3
    assert result["matched_references"] == 3
    assert result["unmatched_references"] == 0
    assert result["match_percentage"] == 100.0


def test_unmatched_foreign_keys_are_reported(tmp_path):
    write_csv(tmp_path / "patients.csv", [["Id"], ["p1"], ["p2"]])
    write_csv(tmp_path / "encounters.csv", [
        ["Id", "PATIENT"], ["e1", "p1"], ["e2", "p2"], ["e3", "ghost-1"], ["e4", "ghost-2"],
    ])

    result = rp.evaluate_relationship(tmp_path, make_relationship())

    assert result["unmatched_references"] == 2
    assert result["matched_references"] == 2
    assert set(result["sample_unmatched_values"]) == {"ghost-1", "ghost-2"}
    assert result["match_percentage"] == 50.0


def test_null_foreign_keys_excluded_from_match_percentage(tmp_path):
    write_csv(tmp_path / "patients.csv", [["Id"], ["p1"], ["p2"]])
    write_csv(tmp_path / "encounters.csv", [
        ["Id", "PATIENT"], ["e1", "p1"], ["e2", "p2"], ["e3", ""],
    ])

    result = rp.evaluate_relationship(tmp_path, make_relationship())

    assert result["records_evaluated"] == 3
    assert result["null_foreign_keys"] == 1
    assert result["non_null_foreign_keys"] == 2
    assert result["match_percentage"] == 100.0


def test_duplicate_parent_keys_are_counted(tmp_path):
    write_csv(tmp_path / "patients.csv", [["Id"], ["p1"], ["p1"], ["p2"], ["p1"]])
    write_csv(tmp_path / "encounters.csv", [["Id", "PATIENT"], ["e1", "p1"]])

    result = rp.evaluate_relationship(tmp_path, make_relationship())

    # p1 appears 3 times -> 2 duplicate occurrences beyond the first.
    assert result["duplicate_parent_keys"] == 2


# -- skip conditions ------------------------------------------------------------


def test_missing_parent_file_is_skipped(tmp_path):
    write_csv(tmp_path / "encounters.csv", [["Id", "PATIENT"], ["e1", "p1"]])

    result = rp.evaluate_relationship(tmp_path, make_relationship())

    assert result["status"] == "skipped"
    assert "Parent file missing" in result["skipped_reason"]
    assert result["match_percentage"] is None


def test_missing_child_file_is_skipped(tmp_path):
    write_csv(tmp_path / "patients.csv", [["Id"], ["p1"]])

    result = rp.evaluate_relationship(tmp_path, make_relationship())

    assert result["status"] == "skipped"
    assert "Child file missing" in result["skipped_reason"]


def test_missing_parent_column_is_skipped(tmp_path):
    write_csv(tmp_path / "patients.csv", [["NOT_ID"], ["p1"]])
    write_csv(tmp_path / "encounters.csv", [["Id", "PATIENT"], ["e1", "p1"]])

    result = rp.evaluate_relationship(tmp_path, make_relationship())

    assert result["status"] == "skipped"
    assert "Parent column" in result["skipped_reason"]


def test_missing_child_column_is_skipped(tmp_path):
    write_csv(tmp_path / "patients.csv", [["Id"], ["p1"]])
    write_csv(tmp_path / "encounters.csv", [["Id", "NOT_PATIENT"], ["e1", "p1"]])

    result = rp.evaluate_relationship(tmp_path, make_relationship())

    assert result["status"] == "skipped"
    assert "Child column" in result["skipped_reason"]


# -- thresholds -----------------------------------------------------------------


def test_warning_threshold_between_fail_and_warning_pct(tmp_path):
    # 5 matched, 5 unmatched -> 50% match. warning_pct=60, fail_pct=40 -> warning.
    write_csv(tmp_path / "patients.csv", [["Id"]] + [[f"p{i}"] for i in range(5)])
    rows = [["Id", "PATIENT"]] + [[f"e{i}", f"p{i}"] for i in range(5)] + [[f"x{i}", f"ghost{i}"] for i in range(5)]
    write_csv(tmp_path / "encounters.csv", rows)

    result = rp.evaluate_relationship(tmp_path, make_relationship(), warning_pct=60.0, fail_pct=40.0)

    assert result["match_percentage"] == 50.0
    assert result["status"] == "warning"


def test_fail_threshold_below_fail_pct(tmp_path):
    write_csv(tmp_path / "patients.csv", [["Id"], ["p1"]])
    rows = [["Id", "PATIENT"], ["e1", "p1"]] + [[f"x{i}", f"ghost{i}"] for i in range(9)]
    write_csv(tmp_path / "encounters.csv", rows)

    result = rp.evaluate_relationship(tmp_path, make_relationship(), warning_pct=90.0, fail_pct=50.0)

    assert result["match_percentage"] == 10.0
    assert result["status"] == "fail"


# -- summary / discovery ---------------------------------------------------------


def test_build_relationship_summary_counts_statuses(tmp_path):
    write_csv(tmp_path / "patients.csv", [["Id"], ["p1"]])
    write_csv(tmp_path / "encounters.csv", [["Id", "PATIENT"], ["e1", "p1"]])

    relationships = [
        make_relationship(name="ok"),
        make_relationship(name="missing", parent_file="nope.csv"),
    ]
    summary = rp.build_relationship_summary(tmp_path, relationships=relationships)

    assert summary["summary"]["total_relationships"] == 2
    assert summary["summary"]["passed"] == 1
    assert summary["summary"]["skipped"] == 1
    assert len(summary["relationships"]) == 2


def test_default_relationships_cover_required_set():
    names = {c.name for c in rp.DEFAULT_RELATIONSHIPS}
    assert "encounters.PATIENT -> patients.Id" in names
    assert "claims.PATIENTID -> patients.Id" in names
    assert "claims.APPOINTMENTID -> encounters.Id" in names
    assert len(rp.DEFAULT_RELATIONSHIPS) == 22


# -- output generation / no raw-data modification --------------------------------


def test_json_output_generation(tmp_path):
    write_csv(tmp_path / "patients.csv", [["Id"], ["p1"]])
    write_csv(tmp_path / "encounters.csv", [["Id", "PATIENT"], ["e1", "p1"]])

    summary = rp.build_relationship_summary(tmp_path, relationships=[make_relationship()])
    output_path = tmp_path / "out" / rp.RELATIONSHIP_SUMMARY_FILENAME
    rp.write_relationship_summary_json(summary, output_path)

    assert output_path.is_file()
    with output_path.open() as fh:
        loaded = json.load(fh)
    assert loaded == summary
    assert "generated_at_utc" in loaded


def test_run_relationship_validation_does_not_modify_source(tmp_path):
    source_dir = tmp_path / "csv"
    source_dir.mkdir()
    write_csv(source_dir / "patients.csv", [["Id"], ["p1"]])
    write_csv(source_dir / "encounters.csv", [["Id", "PATIENT"], ["e1", "p1"]])
    before_patients = (source_dir / "patients.csv").read_text()
    before_encounters = (source_dir / "encounters.csv").read_text()

    output_dir = tmp_path / "reports"
    summary = rp.run_relationship_validation(
        csv_dir=source_dir, output_dir=output_dir, relationships=[make_relationship()]
    )

    assert (output_dir / rp.RELATIONSHIP_SUMMARY_FILENAME).is_file()
    assert summary["summary"]["total_relationships"] == 1
    assert (source_dir / "patients.csv").read_text() == before_patients
    assert (source_dir / "encounters.csv").read_text() == before_encounters
