"""Tests for careflow.profiling.file_profiler.

All tests use temporary CSV fixtures written to tmp_path. None of them
depend on a real Synthea installation or generated Synthea data.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from careflow.profiling import file_profiler as fp


def write_csv(path: Path, rows: list[list[str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerows(rows)
    return path


def column_by_name(file_profile: dict, name: str) -> dict:
    return next(c for c in file_profile["columns"] if c["name"] == name)


# -- row counts, nulls, duplicates --------------------------------------------


def test_row_counts(tmp_path):
    path = write_csv(
        tmp_path / "simple.csv",
        [["id", "value"], ["1", "a"], ["2", "b"], ["3", "c"]],
    )
    profile = fp.profile_file(path)
    assert profile["status"] == "ok"
    assert profile["total_rows"] == 3
    assert profile["total_columns"] == 2


def test_null_counts(tmp_path):
    path = write_csv(
        tmp_path / "nulls.csv",
        [["id", "value"], ["1", "a"], ["2", ""], ["3", ""], ["4", "d"]],
    )
    profile = fp.profile_file(path)
    value_col = column_by_name(profile, "value")
    assert value_col["null_count"] == 2
    assert value_col["null_percentage"] == pytest.approx(50.0)


def test_duplicates(tmp_path):
    path = write_csv(
        tmp_path / "dupes.csv",
        [
            ["id", "value"],
            ["1", "a"],
            ["2", "b"],
            ["1", "a"],
            ["3", "c"],
            ["1", "a"],
        ],
    )
    profile = fp.profile_file(path)
    assert profile["total_rows"] == 5
    assert profile["duplicate_rows"] == 2
    assert profile["duplicate_percentage"] == pytest.approx(40.0)


def test_no_duplicates_reports_zero(tmp_path):
    path = write_csv(
        tmp_path / "unique.csv",
        [["id", "value"], ["1", "a"], ["2", "b"], ["3", "c"]],
    )
    profile = fp.profile_file(path)
    assert profile["duplicate_rows"] == 0
    assert profile["duplicate_percentage"] == 0.0


# -- classification heuristics -------------------------------------------------


def test_identifier_detection(tmp_path):
    rows = [["Id", "GENDER"]] + [[str(i), "M"] for i in range(10)]
    path = write_csv(tmp_path / "patients.csv", rows)
    profile = fp.profile_file(path)
    id_col = column_by_name(profile, "Id")
    gender_col = column_by_name(profile, "GENDER")
    assert id_col["classifications"]["is_possible_identifier"] is True
    assert gender_col["classifications"]["is_possible_identifier"] is False


def test_date_detection(tmp_path):
    rows = [["BIRTHDATE", "NOTES"]] + [
        [f"19{50 + i}-01-{(i % 28) + 1:02d}", "text"] for i in range(10)
    ]
    path = write_csv(tmp_path / "dates.csv", rows)
    profile = fp.profile_file(path)
    date_col = column_by_name(profile, "BIRTHDATE")
    assert date_col["classifications"]["is_possible_date"] is True


def test_numeric_detection(tmp_path):
    rows = [["AMOUNT", "LABEL"]] + [[str(i * 1.5), "x"] for i in range(10)]
    path = write_csv(tmp_path / "numeric.csv", rows)
    profile = fp.profile_file(path)
    amount_col = column_by_name(profile, "AMOUNT")
    assert amount_col["classifications"]["is_possible_numeric"] is True
    assert amount_col["dtype"].startswith("float")


def test_categorical_detection(tmp_path):
    values = ["M", "F"] * 10
    rows = [["GENDER"]] + [[v] for v in values]
    path = write_csv(tmp_path / "categorical.csv", rows)
    profile = fp.profile_file(path)
    gender_col = column_by_name(profile, "GENDER")
    assert gender_col["classifications"]["is_possible_categorical"] is True
    assert gender_col["unique_values"] == 2


def test_high_cardinality_detection(tmp_path):
    rows = [["FREE_TEXT"]] + [[f"unique-value-{i}"] for i in range(30)]
    path = write_csv(tmp_path / "highcard.csv", rows)
    profile = fp.profile_file(path)
    col = column_by_name(profile, "FREE_TEXT")
    assert col["classifications"]["is_possible_high_cardinality"] is True


# -- edge cases: empty / malformed / unreadable --------------------------------


def test_empty_csv_zero_bytes(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("")
    profile = fp.profile_file(path)
    assert profile["status"] == "empty"
    assert profile["total_rows"] == 0
    assert profile["columns"] == []


def test_header_only_csv(tmp_path):
    path = write_csv(tmp_path / "header_only.csv", [["id", "value"]])
    profile = fp.profile_file(path)
    assert profile["status"] == "ok"
    assert profile["total_rows"] == 0
    assert profile["total_columns"] == 2


def test_malformed_csv_reports_error_without_raising(tmp_path):
    path = tmp_path / "malformed.csv"
    path.write_text("id,value\n1,a\n2,b,extra,fields,here\n3,c\n")
    profile = fp.profile_file(path)
    assert profile["status"] == "error"
    assert profile["error"]


def test_directory_profiling_continues_after_malformed_file(tmp_path):
    write_csv(tmp_path / "good.csv", [["id", "value"], ["1", "a"], ["2", "b"]])
    (tmp_path / "bad.csv").write_text("id,value\n1,a\n2,b,extra,fields\n")

    manifest = fp.build_dataset_profile(tmp_path)

    statuses = {f["filename"]: f["status"] for f in manifest["files"]}
    assert statuses["good.csv"] == "ok"
    assert statuses["bad.csv"] == "error"
    assert manifest["dataset_summary"]["total_files"] == 2
    assert manifest["dataset_summary"]["files_ok"] == 1
    assert manifest["dataset_summary"]["files_error"] == 1


# -- chunked reading -------------------------------------------------------------


def test_profile_file_across_multiple_chunks(tmp_path):
    rows = [["id", "value"]] + [[str(i), "dup" if i % 3 == 0 else f"v{i}"] for i in range(20)]
    path = write_csv(tmp_path / "chunked.csv", rows)

    single_chunk = fp.profile_file(path, chunk_size=1000)
    multi_chunk = fp.profile_file(path, chunk_size=3)

    assert multi_chunk["total_rows"] == single_chunk["total_rows"] == 20
    assert multi_chunk["duplicate_rows"] == single_chunk["duplicate_rows"]
    assert (
        column_by_name(multi_chunk, "value")["unique_values"]
        == column_by_name(single_chunk, "value")["unique_values"]
    )


# -- dynamic discovery -------------------------------------------------------------


def test_discover_csv_files_ignores_non_csv(tmp_path):
    write_csv(tmp_path / "a.csv", [["x"], ["1"]])
    write_csv(tmp_path / "b.csv", [["x"], ["1"]])
    (tmp_path / "notes.txt").write_text("not a csv")

    discovered = fp.discover_csv_files(tmp_path)
    assert [p.name for p in discovered] == ["a.csv", "b.csv"]


def test_discover_csv_files_missing_directory(tmp_path):
    missing = tmp_path / "does_not_exist"
    assert fp.discover_csv_files(missing) == []


def test_build_dataset_profile_no_files(tmp_path):
    manifest = fp.build_dataset_profile(tmp_path)
    assert manifest["dataset_summary"]["total_files"] == 0
    assert manifest["files"] == []
    assert manifest["profiling_version"] == fp.PROFILING_VERSION


# -- output generation -----------------------------------------------------------


def test_json_generation(tmp_path):
    write_csv(tmp_path / "a.csv", [["id", "value"], ["1", "a"], ["2", "b"]])
    output_dir = tmp_path / "out"

    manifest = fp.build_dataset_profile(tmp_path)
    output_path = output_dir / fp.DATASET_PROFILE_FILENAME
    fp.write_dataset_profile_json(manifest, output_path)

    assert output_path.is_file()
    with output_path.open() as fh:
        loaded = json.load(fh)
    assert loaded == manifest
    assert "profiled_at_utc" in loaded
    assert "dataset_summary" in loaded


def test_csv_report_generation(tmp_path):
    write_csv(tmp_path / "a.csv", [["id", "value"], ["1", "a"], ["2", "b"]])
    output_dir = tmp_path / "out"

    manifest = fp.build_dataset_profile(tmp_path)
    output_path = output_dir / fp.COLUMN_PROFILE_FILENAME
    fp.write_column_profile_csv(manifest, output_path)

    assert output_path.is_file()
    with output_path.open(newline="") as fh:
        rows = list(csv.DictReader(fh))

    assert rows[0].keys() == set(fp.COLUMN_PROFILE_FIELDNAMES)
    assert len(rows) == 2  # one row per column: id, value
    columns_seen = {row["column"] for row in rows}
    assert columns_seen == {"id", "value"}
    for row in rows:
        assert row["file"] == "a.csv"


def test_run_profiling_writes_both_reports_and_never_touches_source(tmp_path):
    source_dir = tmp_path / "csv"
    source_dir.mkdir()
    write_csv(source_dir / "a.csv", [["id", "value"], ["1", "a"], ["2", "b"]])
    before = (source_dir / "a.csv").read_text()

    output_dir = tmp_path / "reports"
    manifest = fp.run_profiling(csv_dir=source_dir, output_dir=output_dir)

    assert (output_dir / fp.DATASET_PROFILE_FILENAME).is_file()
    assert (output_dir / fp.COLUMN_PROFILE_FILENAME).is_file()
    assert manifest["dataset_summary"]["total_files"] == 1
    assert (source_dir / "a.csv").read_text() == before
