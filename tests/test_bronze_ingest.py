"""Tests for careflow.bronze.ingest.

All tests use temporary CSV fixtures. None of them depend on the
generated Synthea dataset or a real Java/Synthea installation.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd
import pytest

from careflow.bronze import ingest as bz


def write_csv(path: Path, rows: list[list[str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerows(rows)
    return path


def make_relationship_summary(relationships: list[dict] | None = None) -> dict:
    return {"relationships": relationships or []}


def make_data_quality_report(rules: list[dict] | None = None) -> dict:
    return {"rules": rules or []}


# -- schema typing ------------------------------------------------------------


def test_string_columns_preserve_leading_zeros(tmp_path):
    write_csv(tmp_path / "patients.csv", [["Id", "ZIP"], ["p1", "02138"], ["p2", "00000"]])

    result = bz.ingest_file(
        tmp_path / "patients.csv", tmp_path / "out" / "patients.parquet",
        date_columns=set(), numeric_columns=set(),
    )

    assert result["status"] == "ingested"
    df = pd.read_parquet(tmp_path / "out" / "patients.parquet")
    assert df["ZIP"].tolist() == ["02138", "00000"]
    assert result["schema"]["ZIP"] == "string"


def test_date_column_typed_as_timestamp(tmp_path):
    write_csv(tmp_path / "encounters.csv", [
        ["Id", "START"], ["e1", "2020-01-01T10:00:00Z"], ["e2", "2020-02-01T10:00:00Z"],
    ])

    result = bz.ingest_file(
        tmp_path / "encounters.csv", tmp_path / "out" / "encounters.parquet",
        date_columns={"START"}, numeric_columns=set(),
    )

    assert result["status"] == "ingested"
    assert result["schema"]["START"] == "timestamp"
    df = pd.read_parquet(tmp_path / "out" / "encounters.parquet")
    assert pd.api.types.is_datetime64_any_dtype(df["START"])


def test_numeric_column_typed_as_double(tmp_path):
    write_csv(tmp_path / "procedures.csv", [["BASE_COST"], ["100.50"], ["25"]])

    result = bz.ingest_file(
        tmp_path / "procedures.csv", tmp_path / "out" / "procedures.parquet",
        date_columns=set(), numeric_columns={"BASE_COST"},
    )

    assert result["status"] == "ingested"
    assert result["schema"]["BASE_COST"] == "double"
    df = pd.read_parquet(tmp_path / "out" / "procedures.parquet")
    assert pd.api.types.is_float_dtype(df["BASE_COST"])
    assert df["BASE_COST"].tolist() == [100.5, 25.0]


def test_cast_failures_are_tracked(tmp_path):
    write_csv(tmp_path / "encounters.csv", [
        ["Id", "START"], ["e1", "2020-01-01T10:00:00Z"], ["e2", "not-a-date"],
    ])

    result = bz.ingest_file(
        tmp_path / "encounters.csv", tmp_path / "out" / "encounters.parquet",
        date_columns={"START"}, numeric_columns=set(),
    )

    assert result["cast_failures"] == {"START": 1}


# -- ingestion metadata ---------------------------------------------------------


def test_row_and_column_counts(tmp_path):
    write_csv(tmp_path / "patients.csv", [["Id", "GENDER"], ["p1", "M"], ["p2", "F"], ["p3", "M"]])

    result = bz.ingest_file(
        tmp_path / "patients.csv", tmp_path / "out" / "patients.parquet",
        date_columns=set(), numeric_columns=set(),
    )

    assert result["row_count"] == 3
    assert result["column_count"] == 2


def test_checksums_are_present_and_distinct(tmp_path):
    write_csv(tmp_path / "patients.csv", [["Id"], ["p1"], ["p2"]])

    result = bz.ingest_file(
        tmp_path / "patients.csv", tmp_path / "out" / "patients.parquet",
        date_columns=set(), numeric_columns=set(),
    )

    assert result["source_checksum"]
    assert result["bronze_checksum"]
    assert len(result["source_checksum"]) == 64
    assert len(result["bronze_checksum"]) == 64
    assert result["bronze_size_bytes"] > 0


# -- chunked reading across multiple chunks --------------------------------------


def test_ingest_across_multiple_chunks_matches_single_chunk(tmp_path):
    rows = [["Id", "VALUE"]] + [[f"p{i}", str(i * 1.5)] for i in range(20)]
    write_csv(tmp_path / "data.csv", rows)

    single = bz.ingest_file(
        tmp_path / "data.csv", tmp_path / "single.parquet",
        date_columns=set(), numeric_columns={"VALUE"}, chunk_size=1000,
    )
    multi = bz.ingest_file(
        tmp_path / "data.csv", tmp_path / "multi.parquet",
        date_columns=set(), numeric_columns={"VALUE"}, chunk_size=3,
    )

    assert single["row_count"] == multi["row_count"] == 20
    df_single = pd.read_parquet(tmp_path / "single.parquet")
    df_multi = pd.read_parquet(tmp_path / "multi.parquet")
    assert df_single["VALUE"].tolist() == df_multi["VALUE"].tolist()


# -- validation gate --------------------------------------------------------------


def test_blocked_file_is_not_ingested(tmp_path):
    write_csv(tmp_path / "patients.csv", [["Id"], ["p1"]])

    result = bz.ingest_file(
        tmp_path / "patients.csv", tmp_path / "out" / "patients.parquet",
        date_columns=set(), numeric_columns=set(),
        blocked_reasons=["data quality rule 'x' status=fail"],
    )

    assert result["status"] == "blocked"
    assert "status=fail" in result["reason"]
    assert not (tmp_path / "out" / "patients.parquet").exists()


def test_gate_reasons_flags_failing_relationship_for_child_file():
    relationship_summary = make_relationship_summary([
        {"relationship": "encounters.PATIENT -> patients.Id", "child_file": "encounters.csv", "status": "fail"},
    ])
    data_quality_report = make_data_quality_report([])

    reasons = bz.gate_reasons("encounters.csv", data_quality_report, relationship_summary, {"fail"})

    assert len(reasons) == 1
    assert "encounters.PATIENT" in reasons[0]


def test_gate_reasons_flags_failing_rule_for_source_file():
    data_quality_report = make_data_quality_report([
        {"rule_id": "not_null:patients.csv:Id", "source_file": "patients.csv", "status": "fail"},
    ])
    relationship_summary = make_relationship_summary([])

    reasons = bz.gate_reasons("patients.csv", data_quality_report, relationship_summary, {"fail"})

    assert len(reasons) == 1
    assert "not_null:patients.csv:Id" in reasons[0]


def test_gate_reasons_ignores_non_blocking_statuses():
    data_quality_report = make_data_quality_report([
        {"rule_id": "r1", "source_file": "patients.csv", "status": "warning"},
    ])
    relationship_summary = make_relationship_summary([])

    reasons = bz.gate_reasons("patients.csv", data_quality_report, relationship_summary, {"fail"})

    assert reasons == []


def test_build_bronze_manifest_blocks_file_via_injected_reports(tmp_path):
    write_csv(tmp_path / "patients.csv", [["Id"], ["p1"]])
    write_csv(tmp_path / "encounters.csv", [["Id", "PATIENT"], ["e1", "p1"]])

    relationship_summary = make_relationship_summary([
        {"relationship": "encounters.PATIENT -> patients.Id", "child_file": "encounters.csv", "status": "fail"},
    ])
    data_quality_report = make_data_quality_report([])

    settings = bz.BronzeSettings(chunk_size=50_000, gate_enabled=True, block_on_statuses=("fail",), date_columns=())
    manifest = bz.build_bronze_manifest(
        tmp_path, tmp_path / "bronze", settings=settings,
        relationship_summary=relationship_summary, data_quality_report=data_quality_report,
    )

    by_file = {f["filename"]: f for f in manifest["files"]}
    assert by_file["encounters.csv"]["status"] == "blocked"
    assert by_file["patients.csv"]["status"] == "ingested"
    assert manifest["summary"]["blocked"] == 1
    assert manifest["summary"]["ingested"] == 1
    assert not (tmp_path / "bronze" / "encounters.parquet").exists()
    assert (tmp_path / "bronze" / "patients.parquet").exists()


def test_gate_disabled_ingests_even_with_failing_status(tmp_path):
    write_csv(tmp_path / "encounters.csv", [["Id", "PATIENT"], ["e1", "p1"]])

    relationship_summary = make_relationship_summary([
        {"relationship": "encounters.PATIENT -> patients.Id", "child_file": "encounters.csv", "status": "fail"},
    ])
    data_quality_report = make_data_quality_report([])

    settings = bz.BronzeSettings(chunk_size=50_000, gate_enabled=False, block_on_statuses=("fail",), date_columns=())
    manifest = bz.build_bronze_manifest(
        tmp_path, tmp_path / "bronze", settings=settings,
        relationship_summary=relationship_summary, data_quality_report=data_quality_report,
    )

    assert manifest["files"][0]["status"] == "ingested"
    assert manifest["summary"]["blocked"] == 0


# -- missing / empty / malformed source handling -----------------------------------


def test_missing_source_file_is_skipped(tmp_path):
    result = bz.ingest_file(
        tmp_path / "does_not_exist.csv", tmp_path / "out.parquet",
        date_columns=set(), numeric_columns=set(),
    )
    assert result["status"] == "skipped"
    assert "missing" in result["reason"].lower()


def test_empty_source_file_is_skipped(tmp_path):
    (tmp_path / "empty.csv").write_text("")
    result = bz.ingest_file(
        tmp_path / "empty.csv", tmp_path / "out.parquet",
        date_columns=set(), numeric_columns=set(),
    )
    assert result["status"] == "skipped"
    assert not (tmp_path / "out.parquet").exists()


def test_header_only_csv_is_skipped(tmp_path):
    write_csv(tmp_path / "header_only.csv", [["Id", "VALUE"]])
    result = bz.ingest_file(
        tmp_path / "header_only.csv", tmp_path / "out.parquet",
        date_columns=set(), numeric_columns=set(),
    )
    assert result["status"] == "skipped"
    assert "no data rows" in result["reason"].lower()
    assert not (tmp_path / "out.parquet").exists()


def test_malformed_csv_is_skipped_without_crashing(tmp_path):
    # Unbalanced quote breaks CSV tokenization entirely.
    (tmp_path / "malformed.csv").write_text('Id,VALUE\np1,"broken\np2,2\n')

    result = bz.ingest_file(
        tmp_path / "malformed.csv", tmp_path / "out.parquet",
        date_columns=set(), numeric_columns=set(),
    )

    assert result["status"] == "skipped"
    assert result["reason"]
    assert not (tmp_path / "out.parquet").exists()


# -- dynamic discovery / full orchestration -----------------------------------------


def test_dynamic_discovery_ingests_multiple_files(tmp_path):
    write_csv(tmp_path / "a.csv", [["Id"], ["a1"]])
    write_csv(tmp_path / "b.csv", [["Id"], ["b1"]])

    settings = bz.BronzeSettings(chunk_size=50_000, gate_enabled=False, block_on_statuses=("fail",), date_columns=())
    manifest = bz.build_bronze_manifest(
        tmp_path, tmp_path / "bronze", settings=settings,
        relationship_summary=make_relationship_summary([]), data_quality_report=make_data_quality_report([]),
    )

    assert manifest["summary"]["total_files"] == 2
    assert manifest["summary"]["ingested"] == 2
    assert (tmp_path / "bronze" / "a.parquet").exists()
    assert (tmp_path / "bronze" / "b.parquet").exists()


def test_bronze_manifest_json_generation(tmp_path):
    write_csv(tmp_path / "a.csv", [["Id"], ["a1"]])
    settings = bz.BronzeSettings(chunk_size=50_000, gate_enabled=False, block_on_statuses=("fail",), date_columns=())
    manifest = bz.build_bronze_manifest(
        tmp_path, tmp_path / "bronze", settings=settings,
        relationship_summary=make_relationship_summary([]), data_quality_report=make_data_quality_report([]),
    )
    output_path = tmp_path / "bronze" / bz.BRONZE_MANIFEST_FILENAME
    bz.write_bronze_manifest_json(manifest, output_path)

    assert output_path.is_file()
    with output_path.open() as fh:
        loaded = json.load(fh)
    assert loaded == manifest
    assert loaded["bronze_version"] == bz.BRONZE_VERSION


def test_run_bronze_ingestion_does_not_modify_source(tmp_path):
    # Uses real default config end-to-end, so IDs must satisfy the default
    # uuid_format gate rule (patients.csv:Id) rather than an arbitrary string.
    source_dir = tmp_path / "csv"
    source_dir.mkdir()
    write_csv(source_dir / "patients.csv", [
        ["Id"],
        ["aee7bbe1-0c45-c028-1e62-1f4cdb30c273"],
        ["5e688e99-61b3-5c88-3f60-21df8aaced27"],
    ])
    before = (source_dir / "patients.csv").read_text()

    bronze_dir = tmp_path / "bronze"
    reports_dir = tmp_path / "reports"
    manifest = bz.run_bronze_ingestion(csv_dir=source_dir, bronze_dir=bronze_dir, reports_dir=reports_dir)

    assert (bronze_dir / bz.BRONZE_MANIFEST_FILENAME).is_file()
    by_file = {f["filename"]: f for f in manifest["files"]}
    assert by_file["patients.csv"]["status"] == "ingested"
    assert (bronze_dir / "patients.parquet").is_file()
    assert (reports_dir / "relationship_summary.json").is_file()
    assert (reports_dir / "data_quality_report.json").is_file()
    assert manifest["summary"]["total_files"] == 1
    assert (source_dir / "patients.csv").read_text() == before
