"""Tests for careflow.warehouse.warehouse_validator.

All tests use lightweight fakes; none require a running PostgreSQL server.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from careflow.warehouse import warehouse_validator as wv
from careflow.warehouse.schema_manager import WarehouseTableSpec


class QueueCursor:
    def __init__(self, results: list):
        self._results = results  # shared reference across cursor() calls

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        pass

    def fetchone(self):
        return self._results.pop(0)

    def fetchall(self):
        return self._results.pop(0)


class QueueConnection:
    def __init__(self, results: list):
        self._results = list(results)
        self.rollback_calls = 0

    def cursor(self):
        return QueueCursor(self._results)

    def rollback(self):
        self.rollback_calls += 1


# -- structural checks ---------------------------------------------------------------


def test_check_schemas_exist_all_present():
    conn = QueueConnection([[
        ("careflow_meta",), ("careflow_dim",), ("careflow_fact",), ("careflow_mart",), ("careflow_audit",),
    ]])
    checks = wv.check_schemas_exist(conn)
    assert len(checks) == 5
    assert all(c["status"] == "pass" for c in checks)


def test_check_schemas_exist_reports_missing_schema():
    conn = QueueConnection([[("careflow_meta",)]])
    checks = wv.check_schemas_exist(conn)
    statuses = {c["check_id"]: c["status"] for c in checks}
    assert statuses["schema_exists:careflow_meta"] == "pass"
    assert statuses["schema_exists:careflow_dim"] == "fail"


def test_check_tables_exist_reports_missing_table(monkeypatch):
    fake_tables = {"dim_test": WarehouseTableSpec("careflow_dim", "dim_test", "dimension", ("k",), "dim_test.parquet")}
    monkeypatch.setattr(wv, "WAREHOUSE_TABLES", fake_tables)
    conn = QueueConnection([[]])  # no tables present
    checks = wv.check_tables_exist(conn)
    assert checks[0]["status"] == "fail"


def test_check_columns_match_gold_detects_missing_column(tmp_path, monkeypatch):
    df = pd.DataFrame({"a": [1], "b": [2]})
    df.to_parquet(tmp_path / "dim_test.parquet", engine="pyarrow", index=False)
    fake_tables = {"dim_test": WarehouseTableSpec("careflow_dim", "dim_test", "dimension", ("a",), "dim_test.parquet")}
    monkeypatch.setattr(wv, "WAREHOUSE_TABLES", fake_tables)

    conn = QueueConnection([[("a",)]])  # postgres only has column 'a'
    checks = wv.check_columns_match_gold(conn, tmp_path)
    assert checks[0]["status"] == "fail"
    assert "b" in checks[0]["details"]


def test_check_columns_match_gold_pass(tmp_path, monkeypatch):
    df = pd.DataFrame({"a": [1], "b": [2]})
    df.to_parquet(tmp_path / "dim_test.parquet", engine="pyarrow", index=False)
    fake_tables = {"dim_test": WarehouseTableSpec("careflow_dim", "dim_test", "dimension", ("a",), "dim_test.parquet")}
    monkeypatch.setattr(wv, "WAREHOUSE_TABLES", fake_tables)

    conn = QueueConnection([[("a",), ("b",)]])
    checks = wv.check_columns_match_gold(conn, tmp_path)
    assert checks[0]["status"] == "pass"


# -- row counts -----------------------------------------------------------------------


def test_check_row_counts_match(tmp_path, monkeypatch):
    df = pd.DataFrame({"a": [1, 2, 3]})
    df.to_parquet(tmp_path / "dim_test.parquet", engine="pyarrow", index=False)
    fake_tables = {"dim_test": WarehouseTableSpec("careflow_dim", "dim_test", "dimension", ("a",), "dim_test.parquet")}
    monkeypatch.setattr(wv, "WAREHOUSE_TABLES", fake_tables)
    monkeypatch.setattr(wv, "ALLOWED_TABLE_NAMES", {"dim_test"})

    conn = QueueConnection([(3,)])
    checks = wv.check_row_counts(conn, tmp_path)
    assert checks[0]["status"] == "pass"


def test_check_row_counts_mismatch(tmp_path, monkeypatch):
    df = pd.DataFrame({"a": [1, 2, 3]})
    df.to_parquet(tmp_path / "dim_test.parquet", engine="pyarrow", index=False)
    fake_tables = {"dim_test": WarehouseTableSpec("careflow_dim", "dim_test", "dimension", ("a",), "dim_test.parquet")}
    monkeypatch.setattr(wv, "WAREHOUSE_TABLES", fake_tables)
    monkeypatch.setattr(wv, "ALLOWED_TABLE_NAMES", {"dim_test"})

    conn = QueueConnection([(2,)])
    checks = wv.check_row_counts(conn, tmp_path)
    assert checks[0]["status"] == "fail"


# -- primary key validation -----------------------------------------------------------


def test_check_primary_keys_detects_nulls_and_duplicates(monkeypatch):
    fake_tables = {"dim_test": WarehouseTableSpec("careflow_dim", "dim_test", "dimension", ("test_key",), "dim_test.parquet")}
    monkeypatch.setattr(wv, "WAREHOUSE_TABLES", fake_tables)
    monkeypatch.setattr(wv, "ALLOWED_TABLE_NAMES", {"dim_test"})

    conn = QueueConnection([(2,), (1,)])  # 2 nulls, 1 duplicate group
    checks = wv.check_primary_keys(conn)

    null_check = next(c for c in checks if c["check_id"] == "primary_key_not_null:dim_test")
    unique_check = next(c for c in checks if c["check_id"] == "primary_key_unique:dim_test")
    assert null_check["status"] == "fail"
    assert null_check["records_failed"] == 2
    assert unique_check["status"] == "fail"
    assert unique_check["records_failed"] == 1


def test_check_primary_keys_pass_when_clean(monkeypatch):
    fake_tables = {"dim_test": WarehouseTableSpec("careflow_dim", "dim_test", "dimension", ("test_key",), "dim_test.parquet")}
    monkeypatch.setattr(wv, "WAREHOUSE_TABLES", fake_tables)
    monkeypatch.setattr(wv, "ALLOWED_TABLE_NAMES", {"dim_test"})

    conn = QueueConnection([(0,), (0,)])
    checks = wv.check_primary_keys(conn)
    assert all(c["status"] == "pass" for c in checks)


def test_check_primary_keys_skips_tables_without_a_primary_key(monkeypatch):
    fake_tables = {"mart_test": WarehouseTableSpec("careflow_mart", "mart_test", "mart", (), "mart_test.parquet")}
    monkeypatch.setattr(wv, "WAREHOUSE_TABLES", fake_tables)
    conn = QueueConnection([])
    checks = wv.check_primary_keys(conn)
    assert checks == []


# -- foreign-key orphan reporting -----------------------------------------------------


def test_check_foreign_key_orphans_reports_count(monkeypatch):
    monkeypatch.setattr(wv, "FOREIGN_KEY_CHECKS", (("fact_encounter", "patient_key", "dim_patient", "patient_key"),))
    conn = QueueConnection([(5,), (100,)])
    checks = wv.check_foreign_key_orphans(conn)
    assert len(checks) == 1
    assert checks[0]["status"] == "warning"
    assert checks[0]["records_failed"] == 5
    assert checks[0]["records_evaluated"] == 100


def test_check_foreign_key_orphans_pass_when_zero(monkeypatch):
    monkeypatch.setattr(wv, "FOREIGN_KEY_CHECKS", (("fact_encounter", "patient_key", "dim_patient", "patient_key"),))
    conn = QueueConnection([(0,), (100,)])
    checks = wv.check_foreign_key_orphans(conn)
    assert checks[0]["status"] == "pass"


# -- date key resolution ---------------------------------------------------------------


def test_check_date_keys_resolve_reports_unresolved(monkeypatch):
    monkeypatch.setattr(wv, "DATE_KEY_CHECKS", (("fact_encounter", "encounter_date_key"),))
    conn = QueueConnection([(3,), (500,)])
    checks = wv.check_date_keys_resolve(conn)
    assert checks[0]["status"] == "warning"
    assert checks[0]["records_failed"] == 3


def test_check_date_keys_resolve_pass(monkeypatch):
    monkeypatch.setattr(wv, "DATE_KEY_CHECKS", (("fact_encounter", "encounter_date_key"),))
    conn = QueueConnection([(0,), (500,)])
    checks = wv.check_date_keys_resolve(conn)
    assert checks[0]["status"] == "pass"


# -- currency reconciliation -----------------------------------------------------------


def test_currency_reconciliation_within_tolerance(tmp_path, monkeypatch):
    df = pd.DataFrame({"total_claim_cost": [100.0, 200.0]})
    df.to_parquet(tmp_path / "fact_encounter.parquet", engine="pyarrow", index=False)
    fake_tables = {"fact_encounter": WarehouseTableSpec("careflow_fact", "fact_encounter", "fact", ("encounter_key",), "fact_encounter.parquet")}
    monkeypatch.setattr(wv, "WAREHOUSE_TABLES", fake_tables)
    monkeypatch.setattr(wv, "CURRENCY_RECONCILIATION_CHECKS", (("fact_encounter", "total_claim_cost"),))

    conn = QueueConnection([(300.005,)])  # within 0.01 of gold sum (300.0)
    checks = wv.check_currency_reconciliation(conn, tmp_path, tolerance=0.01)
    assert checks[0]["status"] == "pass"


def test_currency_reconciliation_outside_tolerance(tmp_path, monkeypatch):
    df = pd.DataFrame({"total_claim_cost": [100.0, 200.0]})
    df.to_parquet(tmp_path / "fact_encounter.parquet", engine="pyarrow", index=False)
    fake_tables = {"fact_encounter": WarehouseTableSpec("careflow_fact", "fact_encounter", "fact", ("encounter_key",), "fact_encounter.parquet")}
    monkeypatch.setattr(wv, "WAREHOUSE_TABLES", fake_tables)
    monkeypatch.setattr(wv, "CURRENCY_RECONCILIATION_CHECKS", (("fact_encounter", "total_claim_cost"),))

    conn = QueueConnection([(500.0,)])
    checks = wv.check_currency_reconciliation(conn, tmp_path, tolerance=0.01)
    assert checks[0]["status"] == "fail"


# -- readmission mart counts ------------------------------------------------------------


def test_readmission_counts_match(tmp_path):
    df = pd.DataFrame({"readmitted_within_30_days": [True, False, True]})
    df.to_parquet(tmp_path / "mart_readmission.parquet", engine="pyarrow", index=False)
    conn = QueueConnection([(3, 2)])
    checks = wv.check_readmission_counts_match(conn, tmp_path)
    assert checks[0]["status"] == "pass"


def test_readmission_counts_mismatch(tmp_path):
    df = pd.DataFrame({"readmitted_within_30_days": [True, False, True]})
    df.to_parquet(tmp_path / "mart_readmission.parquet", engine="pyarrow", index=False)
    conn = QueueConnection([(3, 1)])
    checks = wv.check_readmission_counts_match(conn, tmp_path)
    assert checks[0]["status"] == "fail"


def test_readmission_counts_skipped_when_gold_file_missing(tmp_path):
    conn = QueueConnection([])
    checks = wv.check_readmission_counts_match(conn, tmp_path)
    assert checks[0]["status"] == "skipped"


# -- KPI value reconciliation ------------------------------------------------------------


def test_kpi_values_match_within_tolerance(tmp_path):
    gold_kpi_path = tmp_path / "gold_kpi_summary.json"
    gold_kpi_path.write_text(json.dumps({"kpis": [{"kpi_name": "cost_per_encounter", "value": 100.0}]}))
    conn = QueueConnection([(1000.0, 10)])  # sum=1000, count=10 -> 100.0
    checks = wv.check_kpi_values_match(conn, gold_kpi_path, tolerance=0.01)
    matching = [c for c in checks if c["check_id"] == "kpi_matches_gold:cost_per_encounter"]
    assert matching[0]["status"] == "pass"


def test_kpi_values_missing_gold_file_is_skipped(tmp_path):
    conn = QueueConnection([])
    checks = wv.check_kpi_values_match(conn, tmp_path / "does_not_exist.json")
    assert checks[0]["status"] == "skipped"


def test_kpi_values_mismatch_fails(tmp_path):
    gold_kpi_path = tmp_path / "gold_kpi_summary.json"
    gold_kpi_path.write_text(json.dumps({"kpis": [{"kpi_name": "cost_per_encounter", "value": 999.0}]}))
    conn = QueueConnection([(1000.0, 10)])
    checks = wv.check_kpi_values_match(conn, gold_kpi_path, tolerance=0.01)
    matching = [c for c in checks if c["check_id"] == "kpi_matches_gold:cost_per_encounter"]
    assert matching[0]["status"] == "fail"


def test_kpi_zero_denominator_handled_safely(tmp_path):
    gold_kpi_path = tmp_path / "gold_kpi_summary.json"
    gold_kpi_path.write_text(json.dumps({"kpis": [{"kpi_name": "cost_per_encounter", "value": 100.0}]}))
    conn = QueueConnection([(0.0, 0)])  # zero denominator in postgres
    checks = wv.check_kpi_values_match(conn, gold_kpi_path, tolerance=0.01)
    matching = [c for c in checks if c["check_id"] == "kpi_matches_gold:cost_per_encounter"]
    assert matching[0]["status"] == "fail"  # pg value is None (0 denom), gold expects 100.0 -- correctly flagged, not a crash


# -- reporting views: execute successfully + exclude restricted PII --------------------


def test_check_views_execute_pass():
    conn = QueueConnection([[(1,)] for _ in wv.VIEW_NAMES])
    checks = wv.check_views_execute(conn)
    assert all(c["status"] == "pass" for c in checks)


def test_check_views_execute_reports_failure_and_rolls_back():
    class FailingCursor(QueueCursor):
        def execute(self, sql, params=None):
            raise RuntimeError("view broken")

    class FailingConnection(QueueConnection):
        def cursor(self):
            return FailingCursor(self._results)

    conn = FailingConnection([])
    checks = wv.check_views_execute(conn)
    assert all(c["status"] == "fail" for c in checks)
    assert conn.rollback_calls == len(wv.VIEW_NAMES)


def test_check_views_exclude_pii_pass():
    conn = QueueConnection([[("patient_key",), ("gender",), ("state",)] for _ in wv.VIEW_NAMES])
    checks = wv.check_views_exclude_pii(conn)
    assert all(c["status"] == "pass" for c in checks)


def test_check_views_exclude_pii_flags_restricted_column():
    results = []
    for i, _view in enumerate(wv.VIEW_NAMES):
        if i == 0:
            results.append([("patient_key",), ("ssn",)])
        else:
            results.append([("patient_key",)])
    conn = QueueConnection(results)
    checks = wv.check_views_exclude_pii(conn)
    assert checks[0]["status"] == "fail"
    assert "ssn" in checks[0]["details"]


def test_check_views_exclude_pii_flags_lat_lon():
    results = [[("patient_key",), ("latitude",), ("longitude",)]] + [[("x",)] for _ in wv.VIEW_NAMES[1:]]
    conn = QueueConnection(results)
    checks = wv.check_views_exclude_pii(conn)
    assert checks[0]["status"] == "fail"
    assert "latitude" in checks[0]["details"] or "longitude" in checks[0]["details"]


# -- report generation --------------------------------------------------------------------


def test_write_validation_report_json(tmp_path):
    report = {"summary": {"total_checks": 0}, "checks": []}
    path = tmp_path / "report.json"
    wv.write_validation_report_json(report, path)
    assert json.loads(path.read_text()) == report


def test_write_orphan_summary_csv(tmp_path):
    rows = [{"check_id": "x", "table": "t", "records_evaluated": 10, "orphan_count": 2, "status": "warning"}]
    path = tmp_path / "orphans.csv"
    wv.write_orphan_summary_csv(rows, path)
    content = path.read_text()
    assert "orphan_count" in content
    assert "2" in content
