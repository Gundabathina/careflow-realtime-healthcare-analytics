"""Tests for careflow.warehouse.schema_manager.

All tests use mocks; none require a running PostgreSQL server.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from careflow.warehouse import schema_manager as sm


def make_fake_conn():
    cursor = MagicMock()
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor = MagicMock(return_value=cursor)
    return conn, cursor


# -- table registry -----------------------------------------------------------------


def test_warehouse_tables_registry_covers_all_required_tables():
    expected = {
        "dim_patient", "dim_provider", "dim_organization", "dim_payer", "dim_date",
        "dim_condition", "dim_procedure", "dim_medication",
        "fact_encounter", "fact_condition", "fact_procedure", "fact_medication",
        "fact_observation", "fact_claim", "fact_immunization", "fact_imaging_study",
        "mart_patient_360", "mart_readmission", "mart_hospital_operations",
        "mart_financial_performance", "mart_provider_utilization", "mart_monthly_kpis",
    }
    assert set(sm.WAREHOUSE_TABLES.keys()) == expected


def test_dimensions_loaded_before_facts_before_marts():
    order = sm.WAREHOUSE_LOAD_ORDER
    dim_positions = [order.index(t) for t in sm.DIMENSION_LOAD_ORDER]
    fact_positions = [order.index(t) for t in sm.FACT_LOAD_ORDER]
    mart_positions = [order.index(t) for t in sm.MART_LOAD_ORDER]
    assert max(dim_positions) < min(fact_positions)
    assert max(fact_positions) < min(mart_positions)


def test_imaging_study_primary_key_is_not_bare_source_id():
    """Regression test: imaging_studies.Id is not row-level unique (Phase 2F/3A
    finding). The warehouse must key fact_imaging_study on the surrogate
    imaging_study_key (a hash of id+series_uid+instance_uid), never on the
    bare source study id alone."""
    spec = sm.WAREHOUSE_TABLES["fact_imaging_study"]
    assert spec.primary_key == ("imaging_study_key",)
    assert "study_id" not in spec.primary_key


def test_dimension_primary_keys_are_single_column():
    for key in sm.DIMENSION_LOAD_ORDER:
        assert len(sm.WAREHOUSE_TABLES[key].primary_key) == 1


def test_all_tables_have_a_gold_source_file():
    for spec in sm.WAREHOUSE_TABLES.values():
        assert spec.gold_source_file.endswith(".parquet")


def test_allowed_names_match_registry():
    assert sm.ALLOWED_TABLE_NAMES == set(sm.WAREHOUSE_TABLES.keys())
    assert sm.ALLOWED_SCHEMA_NAMES == set(sm.WAREHOUSE_SCHEMAS)


# -- schema/table/index/view application --------------------------------------------


def test_apply_sql_file_executes_file_contents(tmp_path, monkeypatch):
    sql_path = tmp_path / "test.sql"
    sql_path.write_text("CREATE SCHEMA IF NOT EXISTS example;")
    monkeypatch.setattr(sm, "get_project_root", lambda: tmp_path)

    conn, cursor = make_fake_conn()
    sm.apply_sql_file(conn, "test.sql")
    cursor.execute.assert_called_once_with("CREATE SCHEMA IF NOT EXISTS example;")


def test_ensure_schema_applies_schema_indexes_and_views_in_order(monkeypatch):
    conn, _cursor = make_fake_conn()
    applied = []
    monkeypatch.setattr(sm, "apply_sql_file", lambda c, path: applied.append(path))
    monkeypatch.setattr(sm, "sync_table_registry", lambda c: None)
    monkeypatch.setattr(sm, "record_schema_version", lambda c: None)

    sm.ensure_schema(conn)

    assert applied == [sm.SCHEMA_SQL_RELATIVE_PATH, sm.INDEXES_SQL_RELATIVE_PATH, sm.VIEWS_SQL_RELATIVE_PATH]
    conn.commit.assert_called_once()


def test_ensure_schema_can_skip_indexes_and_views(monkeypatch):
    conn, _cursor = make_fake_conn()
    applied = []
    monkeypatch.setattr(sm, "apply_sql_file", lambda c, path: applied.append(path))
    monkeypatch.setattr(sm, "sync_table_registry", lambda c: None)
    monkeypatch.setattr(sm, "record_schema_version", lambda c: None)

    sm.ensure_schema(conn, apply_indexes=False, apply_views=False)

    assert applied == [sm.SCHEMA_SQL_RELATIVE_PATH]


def test_sync_table_registry_inserts_one_row_per_table():
    conn, cursor = make_fake_conn()
    sm.sync_table_registry(conn)
    assert cursor.execute.call_count == len(sm.WAREHOUSE_TABLES)


def test_record_schema_version_inserts_version_row():
    conn, cursor = make_fake_conn()
    sm.record_schema_version(conn)
    cursor.execute.assert_called_once()
    args, _kwargs = cursor.execute.call_args
    assert sm.WAREHOUSE_SCHEMA_VERSION in args[1]
