"""Tests for careflow.warehouse.gold_loader.

All tests use mocks or lightweight fakes; none require a running
PostgreSQL server.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import pandas as pd
import pytest

from careflow.gold.schema import UNKNOWN_SURROGATE_KEY
from careflow.warehouse import gold_loader as gl
from careflow.warehouse.schema_manager import (
    DIMENSION_LOAD_ORDER,
    FACT_LOAD_ORDER,
    MART_LOAD_ORDER,
    WAREHOUSE_LOAD_ORDER,
    WAREHOUSE_TABLES,
)


# -- prepare_dataframe_for_load -------------------------------------------------


def test_prepare_dataframe_nulls_unknown_sentinel_in_fk_columns():
    df = pd.DataFrame({"encounter_key": [1, 2, 3], "patient_key": [10, UNKNOWN_SURROGATE_KEY, 30]})
    prepared = gl.prepare_dataframe_for_load(df, primary_key=("encounter_key",))
    assert prepared["patient_key"].tolist() == [10, None, 30]


def test_prepare_dataframe_never_nulls_the_primary_key():
    df = pd.DataFrame({"patient_key": [1, 2, 3]})
    prepared = gl.prepare_dataframe_for_load(df, primary_key=("patient_key",))
    assert None not in prepared["patient_key"].tolist()


def test_prepare_dataframe_preserves_large_integer_keys_as_int_not_float():
    large_key = 902392607725440182
    df = pd.DataFrame({"patient_key": [large_key], "encounter_key": [1]})
    prepared = gl.prepare_dataframe_for_load(df, primary_key=("encounter_key",))
    value = prepared["patient_key"].iloc[0]
    assert value == large_key
    assert isinstance(value, int)


def test_prepare_dataframe_converts_nan_to_none():
    df = pd.DataFrame({"encounter_key": [1], "cost": [float("nan")]})
    prepared = gl.prepare_dataframe_for_load(df, primary_key=("encounter_key",))
    assert prepared["cost"].iloc[0] is None


# -- explicit staging type mapping (name + dtype rules, not blind inference) -----


def test_date_key_columns_are_integer_not_bigint():
    assert gl._infer_staging_pg_type("encounter_date_key", "Int64") == "INTEGER"
    assert gl._infer_staging_pg_type("service_date_key", "Int64") == "INTEGER"


def test_surrogate_key_columns_are_bigint():
    assert gl._infer_staging_pg_type("patient_key", "int64") == "BIGINT"
    assert gl._infer_staging_pg_type("encounter_key", "Int64") == "BIGINT"


def test_is_missing_flag_columns_are_boolean():
    assert gl._infer_staging_pg_type("patient_key_is_missing", "boolean") == "BOOLEAN"


def test_boolean_dtype_columns_are_boolean_regardless_of_name():
    assert gl._infer_staging_pg_type("is_deceased", "bool") == "BOOLEAN"
    assert gl._infer_staging_pg_type("is_weekend", "bool") == "BOOLEAN"


def test_currency_named_columns_are_numeric():
    assert gl._infer_staging_pg_type("total_claim_cost", "float64") == "NUMERIC(18,2)"
    assert gl._infer_staging_pg_type("healthcare_expenses", "float64") == "NUMERIC(18,2)"


def test_date_columns_by_name_are_date_type():
    assert gl._infer_staging_pg_type("birth_date", "datetime64[ns, UTC]") == "DATE"


def test_generic_datetime_columns_are_timestamptz():
    assert gl._infer_staging_pg_type("start_timestamp", "datetime64[ns, UTC]") == "TIMESTAMPTZ"


def test_unrecognized_text_column_defaults_to_text():
    assert gl._infer_staging_pg_type("description", "object") == "TEXT"


# -- fakes for SQL / transaction / COPY testing -----------------------------------


class FakeCopy:
    def __init__(self, rows: list):
        self.rows = rows

    def write_row(self, row):
        self.rows.append(row)


class FakeCursor:
    def __init__(self, log: list):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.log.append(("execute", sql, params))

    def fetchone(self):
        return (0,)

    @contextmanager
    def copy(self, sql):
        rows: list = []
        self.log.append(("copy_start", sql))
        yield FakeCopy(rows)
        self.log.append(("copy_end", sql, rows))


class FakeTransaction:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.conn.commits += 1
        else:
            self.conn.rollbacks += 1
        return False  # never swallow the exception


class FakeConnection:
    def __init__(self):
        self.log: list = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return FakeCursor(self.log)

    def transaction(self):
        return FakeTransaction(self)

    def commit(self):
        pass

    def close(self):
        self.closed = True


def make_gold_dataset(tmp_path: Path) -> tuple[Path, dict]:
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir()
    df = pd.DataFrame({"patient_key": [1, 2, 3], "patient_id": ["p1", "p2", "p3"]})
    df.to_parquet(gold_dir / "dim_patient.parquet", engine="pyarrow", index=False)
    gold_entry = {"table": "dim_patient", "status": "processed", "source_checksum": "chk-1", "target_rows": 3}
    return gold_dir, gold_entry


# -- load_table: staging / COPY / transaction commit & rollback ------------------


def test_load_table_processed_calls_steps_in_order(tmp_path, monkeypatch):
    gold_dir, gold_entry = make_gold_dataset(tmp_path)
    calls = []
    monkeypatch.setattr(gl, "create_staging_table", lambda *a, **k: calls.append("create_staging"))
    monkeypatch.setattr(gl, "copy_dataframe", lambda *a, **k: calls.append("copy") or 3)
    monkeypatch.setattr(gl, "validate_staging_rowcount", lambda *a, **k: calls.append("validate") or 3)
    monkeypatch.setattr(gl, "atomic_replace", lambda *a, **k: calls.append("replace"))
    monkeypatch.setattr(gl, "drop_staging_table", lambda *a, **k: calls.append("drop"))
    monkeypatch.setattr(gl, "latest_successful_checksum", lambda *a, **k: None)

    conn = FakeConnection()
    entry = gl.load_table(conn, gold_dir, "dim_patient", gold_entry, force=False, run_id="run-1")

    assert entry["status"] == "processed"
    assert calls == ["create_staging", "copy", "validate", "replace", "drop"]
    assert conn.commits == 1
    assert conn.rollbacks == 0


def test_load_table_rolls_back_transaction_on_failure(tmp_path, monkeypatch):
    gold_dir, gold_entry = make_gold_dataset(tmp_path)
    monkeypatch.setattr(gl, "create_staging_table", lambda *a, **k: None)

    def boom(*a, **k):
        raise RuntimeError("copy failed")

    monkeypatch.setattr(gl, "copy_dataframe", boom)
    monkeypatch.setattr(gl, "latest_successful_checksum", lambda *a, **k: None)

    conn = FakeConnection()
    entry = gl.load_table(conn, gold_dir, "dim_patient", gold_entry, force=False, run_id="run-1")

    assert entry["status"] == "failed"
    assert "copy failed" in entry["error_message"]
    assert conn.rollbacks == 1
    assert conn.commits == 0


def test_load_table_skips_when_checksum_unchanged(tmp_path, monkeypatch):
    gold_dir, gold_entry = make_gold_dataset(tmp_path)
    monkeypatch.setattr(gl, "latest_successful_checksum", lambda *a, **k: "chk-1")
    called = []
    monkeypatch.setattr(gl, "copy_dataframe", lambda *a, **k: called.append("copy"))

    conn = FakeConnection()
    entry = gl.load_table(conn, gold_dir, "dim_patient", gold_entry, force=False, run_id="run-1")

    assert entry["status"] == "skipped"
    assert called == []


def test_load_table_force_bypasses_skip(tmp_path, monkeypatch):
    gold_dir, gold_entry = make_gold_dataset(tmp_path)
    monkeypatch.setattr(gl, "latest_successful_checksum", lambda *a, **k: "chk-1")  # same checksum
    monkeypatch.setattr(gl, "create_staging_table", lambda *a, **k: None)
    monkeypatch.setattr(gl, "copy_dataframe", lambda *a, **k: 3)
    monkeypatch.setattr(gl, "validate_staging_rowcount", lambda *a, **k: 3)
    monkeypatch.setattr(gl, "atomic_replace", lambda *a, **k: None)
    monkeypatch.setattr(gl, "drop_staging_table", lambda *a, **k: None)

    conn = FakeConnection()
    entry = gl.load_table(conn, gold_dir, "dim_patient", gold_entry, force=True, run_id="run-1")

    assert entry["status"] == "processed"


def test_load_table_missing_gold_file_fails_gracefully(tmp_path):
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir()
    gold_entry = {"table": "dim_patient", "status": "processed", "source_checksum": "chk-1", "target_rows": 3}
    conn = FakeConnection()
    entry = gl.load_table(conn, gold_dir, "dim_patient", gold_entry, force=False, run_id="run-1")
    assert entry["status"] == "failed"
    assert "missing" in entry["error_message"].lower()


def test_load_table_gold_status_not_processed_fails(tmp_path):
    gold_dir, _entry = make_gold_dataset(tmp_path)
    conn = FakeConnection()
    entry = gl.load_table(conn, gold_dir, "dim_patient", {"status": "failed"}, force=False, run_id="run-1")
    assert entry["status"] == "failed"


def test_load_table_staging_validation_failure_rolls_back(tmp_path, monkeypatch):
    gold_dir, gold_entry = make_gold_dataset(tmp_path)
    monkeypatch.setattr(gl, "create_staging_table", lambda *a, **k: None)
    monkeypatch.setattr(gl, "copy_dataframe", lambda *a, **k: 3)

    def bad_rowcount(*a, **k):
        raise ValueError("Staging row count mismatch: expected 3, got 2")

    monkeypatch.setattr(gl, "validate_staging_rowcount", bad_rowcount)
    monkeypatch.setattr(gl, "latest_successful_checksum", lambda *a, **k: None)
    replace_called = []
    monkeypatch.setattr(gl, "atomic_replace", lambda *a, **k: replace_called.append(True))

    conn = FakeConnection()
    entry = gl.load_table(conn, gold_dir, "dim_patient", gold_entry, force=False, run_id="run-1")

    assert entry["status"] == "failed"
    assert replace_called == []  # never reached the replace step
    assert conn.rollbacks == 1


# -- SQL-building helpers: COPY not row-by-row INSERT, safe identifiers ----------


def test_copy_dataframe_uses_copy_not_row_by_row_insert():
    conn = FakeConnection()
    df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    rows_loaded = gl.copy_dataframe(conn, "careflow_dim", "dim_test", df, ["a", "b"])
    assert rows_loaded == 2
    copy_calls = [e for e in conn.log if e[0] == "copy_start"]
    assert len(copy_calls) == 1
    assert "COPY" in copy_calls[0][1]
    insert_value_calls = [e for e in conn.log if e[0] == "execute" and "INSERT INTO" in e[1] and "VALUES" in e[1]]
    assert insert_value_calls == []


def test_create_staging_table_rejects_unsafe_schema_name():
    conn = FakeConnection()
    with pytest.raises(Exception):
        gl.create_staging_table(conn, "careflow_dim; DROP SCHEMA public", "stg_x", ["a"], {"a": "int64"})


def test_atomic_replace_rejects_table_not_in_registry():
    conn = FakeConnection()
    with pytest.raises(Exception):
        gl.atomic_replace(conn, "careflow_dim", "not_a_real_table", "stg_x", ["a"])


def test_atomic_replace_uses_delete_not_truncate():
    """TRUNCATE is blocked by inbound FK references (e.g. fact_encounter ->
    dim_patient); DELETE FROM has no such restriction."""
    conn = FakeConnection()
    gl.atomic_replace(conn, "careflow_dim", "dim_patient", "stg_dim_patient", ["patient_key"])
    executed_sql = [e[1] for e in conn.log if e[0] == "execute"]
    assert any("DELETE FROM" in sql for sql in executed_sql)
    assert not any("TRUNCATE" in sql for sql in executed_sql)


# -- run_gold_load orchestration: order, --table, --fail-fast, --schema-only -----


def _patch_full_run(monkeypatch, table_results: dict[str, str]) -> list[str]:
    monkeypatch.setattr(gl, "check_connectivity", lambda config=None: (True, None))

    @contextmanager
    def fake_get_connection(config=None):
        yield FakeConnection()

    monkeypatch.setattr(gl, "get_connection", fake_get_connection)
    monkeypatch.setattr(gl, "ensure_schema", lambda conn: None)
    monkeypatch.setattr(gl, "record_load_manifest_entry", lambda conn, entry: None)
    monkeypatch.setattr(gl, "record_load_error", lambda *a, **k: None)
    monkeypatch.setattr(gl, "record_load_run", lambda conn, entry: None)

    order: list[str] = []

    def fake_load_table(conn, gd, table_key, gold_entry, force, run_id):
        order.append(table_key)
        status = table_results.get(table_key, "processed")
        return {
            "run_id": run_id, "schema_name": "s", "table_name": table_key, "source_path": None,
            "source_checksum": "chk", "source_rows": 1, "loaded_rows": 1 if status == "processed" else None,
            "load_method": "copy", "status": status, "error_message": None if status == "processed" else "boom",
            "duration_seconds": 0.1, "schema_version": "1.0.0", "loader_version": "1.0.0",
            "started_at_utc": "2026-01-01T00:00:00Z", "completed_at_utc": "2026-01-01T00:00:01Z",
        }

    monkeypatch.setattr(gl, "load_table", fake_load_table)
    return order


def _write_gold_manifest(gold_dir: Path, table_names: list[str]) -> None:
    gold_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "run_id": "g1",
        "tables": [{"table": t, "status": "processed", "source_checksum": "c", "target_rows": 1} for t in table_names],
    }
    (gold_dir / gl.GOLD_MANIFEST_FILENAME).write_text(json.dumps(manifest))


def test_full_run_loads_dimensions_before_facts_before_marts(tmp_path, monkeypatch):
    gold_dir = tmp_path / "gold"
    _write_gold_manifest(gold_dir, list(WAREHOUSE_TABLES.keys()))
    order = _patch_full_run(monkeypatch, {})

    report = gl.run_gold_load(gold_dir)

    dim_positions = [order.index(t) for t, s in WAREHOUSE_TABLES.items() if s.kind == "dimension"]
    fact_positions = [order.index(t) for t, s in WAREHOUSE_TABLES.items() if s.kind == "fact"]
    mart_positions = [order.index(t) for t, s in WAREHOUSE_TABLES.items() if s.kind == "mart"]
    assert max(dim_positions) < min(fact_positions)
    assert max(fact_positions) < min(mart_positions)
    assert report["summary"]["total_tables"] == len(WAREHOUSE_TABLES)


def test_single_table_load_restricts_scope(tmp_path, monkeypatch):
    gold_dir = tmp_path / "gold"
    _write_gold_manifest(gold_dir, ["dim_patient"])
    order = _patch_full_run(monkeypatch, {})

    report = gl.run_gold_load(gold_dir, tables=["dim_patient"])

    assert order == ["dim_patient"]
    assert report["summary"]["total_tables"] == 1


def test_fail_fast_stops_after_first_failure(tmp_path, monkeypatch):
    gold_dir = tmp_path / "gold"
    tables = ["dim_patient", "dim_provider", "dim_organization"]
    _write_gold_manifest(gold_dir, tables)
    order = _patch_full_run(monkeypatch, {"dim_patient": "failed"})

    report = gl.run_gold_load(gold_dir, tables=tables, fail_fast=True)

    assert order == ["dim_patient"]
    assert report["summary"]["failed"] == 1


def test_without_fail_fast_continues_after_failure(tmp_path, monkeypatch):
    gold_dir = tmp_path / "gold"
    tables = ["dim_patient", "dim_provider", "dim_organization"]
    _write_gold_manifest(gold_dir, tables)
    order = _patch_full_run(monkeypatch, {"dim_patient": "failed"})

    gl.run_gold_load(gold_dir, tables=tables, fail_fast=False)

    assert order == tables


def test_schema_only_mode_does_not_load_any_table(tmp_path, monkeypatch):
    gold_dir = tmp_path / "gold"
    _write_gold_manifest(gold_dir, [])
    order = _patch_full_run(monkeypatch, {})

    report = gl.run_gold_load(gold_dir, schema_only=True)

    assert order == []
    assert report["mode"] == "schema_only"


def test_missing_gold_manifest_raises(tmp_path, monkeypatch):
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir()
    monkeypatch.setattr(gl, "check_connectivity", lambda config=None: (True, None))
    with pytest.raises(gl.GoldManifestNotFoundError):
        gl.run_gold_load(gold_dir)


def test_connection_failure_raises_warehouse_connection_error(tmp_path, monkeypatch):
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir()
    monkeypatch.setattr(gl, "check_connectivity", lambda config=None: (False, "sanitized reason"))
    with pytest.raises(gl.WarehouseConnectionError):
        gl.run_gold_load(gold_dir)


def test_unknown_table_name_raises(tmp_path, monkeypatch):
    gold_dir = tmp_path / "gold"
    _write_gold_manifest(gold_dir, ["dim_patient"])
    _patch_full_run(monkeypatch, {})
    with pytest.raises(ValueError):
        gl.run_gold_load(gold_dir, tables=["not_a_real_table"])


# -- transactional --force reload: dependency-safe clear/reload order, rollback --
#
# Regression coverage for the idempotency bug where a repeated --force run
# failed a dimension DELETE because fact tables still referenced it via
# foreign key. See run_force_reload / _force_reload_one_table.


def _write_force_reload_gold_files(gold_dir: Path, table_keys: list[str]) -> dict:
    """Write a minimal real Parquet file per table and a matching gold_entries dict.

    Used to call ``gl.run_force_reload`` directly (it takes gold_entries
    as an argument, unlike ``run_gold_load`` which reads it from
    gold_manifest.json on disk).
    """
    gold_dir.mkdir(parents=True, exist_ok=True)
    gold_entries = {}
    for key in table_keys:
        spec = WAREHOUSE_TABLES[key]
        df = pd.DataFrame({spec.primary_key[0]: [1]})
        df.to_parquet(gold_dir / spec.gold_source_file, engine="pyarrow", index=False)
        gold_entries[key] = {"table": key, "status": "processed", "source_checksum": f"chk-{key}", "target_rows": 1}
    return gold_entries


def _patch_force_reload_staging_primitives(monkeypatch, fail_on_table: str | None = None) -> None:
    monkeypatch.setattr(gl, "create_staging_table", lambda *a, **k: None)

    def fake_copy(conn, schema_name, table_name, df, columns):
        if fail_on_table and table_name == f"stg_{fail_on_table}":
            raise RuntimeError(f"simulated copy failure for {fail_on_table}")
        return 1

    monkeypatch.setattr(gl, "copy_dataframe", fake_copy)
    monkeypatch.setattr(gl, "validate_staging_rowcount", lambda *a, **k: 1)
    monkeypatch.setattr(gl, "insert_from_staging", lambda *a, **k: None)
    monkeypatch.setattr(gl, "drop_staging_table", lambda *a, **k: None)


def test_run_force_reload_clears_marts_then_facts_then_dimensions(tmp_path, monkeypatch):
    gold_dir = tmp_path / "gold"
    table_keys = ["dim_patient", "fact_encounter", "mart_patient_360"]
    gold_entries = _write_force_reload_gold_files(gold_dir, table_keys)
    _patch_force_reload_staging_primitives(monkeypatch)

    conn = FakeConnection()
    gl.run_force_reload(conn, gold_dir, gold_entries, set(table_keys), "run-1")

    delete_sql = [e[1] for e in conn.log if e[0] == "execute" and e[1].startswith("DELETE FROM")]
    mart_idx = next(i for i, sql in enumerate(delete_sql) if "mart_patient_360" in sql)
    fact_idx = next(i for i, sql in enumerate(delete_sql) if "fact_encounter" in sql)
    dim_idx = next(i for i, sql in enumerate(delete_sql) if "dim_patient" in sql)
    assert mart_idx < fact_idx < dim_idx, "marts must clear before facts, facts before dimensions"


def test_run_force_reload_reloads_dimensions_then_facts_then_marts(tmp_path, monkeypatch):
    gold_dir = tmp_path / "gold"
    table_keys = ["dim_patient", "fact_encounter", "mart_patient_360"]
    gold_entries = _write_force_reload_gold_files(gold_dir, table_keys)
    _patch_force_reload_staging_primitives(monkeypatch)

    conn = FakeConnection()
    entries = gl.run_force_reload(conn, gold_dir, gold_entries, set(table_keys), "run-1")

    assert [e["table_name"] for e in entries] == ["dim_patient", "fact_encounter", "mart_patient_360"]
    assert all(e["status"] == "processed" for e in entries)


def test_run_force_reload_full_registry_never_clears_a_dimension_before_its_facts(tmp_path, monkeypatch):
    """Structural proxy for 'foreign keys remain valid': across every
    registered table (not just a 3-table sample), every fact clears
    before any dimension, and every mart clears before any fact --
    exactly the ordering fact_* -> dim_* foreign keys require."""
    gold_dir = tmp_path / "gold"
    table_keys = list(WAREHOUSE_TABLES.keys())
    gold_entries = _write_force_reload_gold_files(gold_dir, table_keys)
    _patch_force_reload_staging_primitives(monkeypatch)

    conn = FakeConnection()
    entries = gl.run_force_reload(conn, gold_dir, gold_entries, set(table_keys), "run-1")

    delete_sql = [e[1] for e in conn.log if e[0] == "execute" and e[1].startswith("DELETE FROM")]

    def clear_index(table_name: str) -> int:
        return next(i for i, sql in enumerate(delete_sql) if f'"{table_name}"' in sql)

    assert max(clear_index(t) for t in MART_LOAD_ORDER) < min(clear_index(t) for t in FACT_LOAD_ORDER)
    assert max(clear_index(t) for t in FACT_LOAD_ORDER) < min(clear_index(t) for t in DIMENSION_LOAD_ORDER)
    assert [e["table_name"] for e in entries] == list(WAREHOUSE_LOAD_ORDER)


def test_run_force_reload_uses_a_single_transaction_not_one_per_table(tmp_path, monkeypatch):
    gold_dir = tmp_path / "gold"
    table_keys = ["dim_patient", "fact_encounter", "mart_patient_360"]
    gold_entries = _write_force_reload_gold_files(gold_dir, table_keys)
    _patch_force_reload_staging_primitives(monkeypatch)

    conn = FakeConnection()
    gl.run_force_reload(conn, gold_dir, gold_entries, set(table_keys), "run-1")

    assert conn.commits == 1
    assert conn.rollbacks == 0


def test_run_force_reload_rolls_back_entire_batch_on_injected_failure(tmp_path, monkeypatch):
    gold_dir = tmp_path / "gold"
    table_keys = ["dim_patient", "fact_encounter"]
    gold_entries = _write_force_reload_gold_files(gold_dir, table_keys)
    _patch_force_reload_staging_primitives(monkeypatch, fail_on_table="fact_encounter")

    conn = FakeConnection()
    with pytest.raises(gl.ForceReloadStageError) as excinfo:
        gl.run_force_reload(conn, gold_dir, gold_entries, set(table_keys), "run-1")

    assert excinfo.value.table_key == "fact_encounter"
    assert excinfo.value.stage == "reload"
    # dim_patient's own staging succeeded before fact_encounter failed, but
    # the whole batch is one transaction -- there is no partial commit.
    assert conn.commits == 0
    assert conn.rollbacks == 1


class _FakeWarehouseState:
    """Minimal in-memory 'database' used only to prove commit/rollback
    semantics: ``committed`` is durable state; a transaction's mutations
    go through ``pending`` and are merged into ``committed`` on commit or
    discarded entirely on rollback -- exactly what a real Postgres
    transaction guarantees, at the level these tests need."""

    def __init__(self, committed: dict[str, int]):
        self.committed = dict(committed)
        self.pending: dict[str, int] | None = None

    def begin(self) -> None:
        self.pending = dict(self.committed)

    def commit(self) -> None:
        self.committed = dict(self.pending)
        self.pending = None

    def rollback(self) -> None:
        self.pending = None

    def clear(self, table_key: str) -> None:
        self.pending[table_key] = 0

    def load(self, table_key: str, rows: int) -> None:
        self.pending[table_key] = rows


class _FakeStatefulTransaction:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        self.conn.state.begin()
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.conn.state.commit()
            self.conn.commits += 1
        else:
            self.conn.state.rollback()
            self.conn.rollbacks += 1
        return False


class _FakeStatefulConnection(FakeConnection):
    def __init__(self, state: _FakeWarehouseState):
        super().__init__()
        self.state = state

    def transaction(self):
        return _FakeStatefulTransaction(self)


def test_force_reload_rollback_preserves_previous_data_after_injected_failure(tmp_path, monkeypatch):
    gold_dir = tmp_path / "gold"
    table_keys = ["dim_patient", "fact_encounter"]
    gold_entries = _write_force_reload_gold_files(gold_dir, table_keys)

    state = _FakeWarehouseState({"dim_patient": 3, "fact_encounter": 5})
    monkeypatch.setattr(gl, "_clear_table_rows", lambda conn, spec: state.clear(spec.table_name))
    monkeypatch.setattr(gl, "create_staging_table", lambda *a, **k: None)
    monkeypatch.setattr(gl, "copy_dataframe", lambda *a, **k: 1)
    monkeypatch.setattr(gl, "validate_staging_rowcount", lambda *a, **k: 1)

    def fake_insert(conn, schema_name, table_name, staging_table, columns):
        if table_name == "fact_encounter":
            raise RuntimeError("simulated insert failure")
        state.load(table_name, 1)

    monkeypatch.setattr(gl, "insert_from_staging", fake_insert)
    monkeypatch.setattr(gl, "drop_staging_table", lambda *a, **k: None)

    conn = _FakeStatefulConnection(state)
    with pytest.raises(gl.ForceReloadStageError):
        gl.run_force_reload(conn, gold_dir, gold_entries, set(table_keys), "run-1")

    assert state.committed == {"dim_patient": 3, "fact_encounter": 5}
    assert state.pending is None
    assert conn.commits == 0
    assert conn.rollbacks == 1


def test_two_consecutive_force_reloads_succeed_with_identical_row_counts(tmp_path, monkeypatch):
    gold_dir = tmp_path / "gold"
    table_keys = ["dim_patient", "fact_encounter", "mart_patient_360"]
    gold_entries = _write_force_reload_gold_files(gold_dir, table_keys)
    row_counts = {"dim_patient": 3, "fact_encounter": 5, "mart_patient_360": 3}

    state = _FakeWarehouseState(dict(row_counts))
    monkeypatch.setattr(gl, "_clear_table_rows", lambda conn, spec: state.clear(spec.table_name))
    monkeypatch.setattr(gl, "create_staging_table", lambda *a, **k: None)
    monkeypatch.setattr(gl, "copy_dataframe", lambda *a, **k: 1)
    monkeypatch.setattr(gl, "validate_staging_rowcount", lambda *a, **k: 1)
    monkeypatch.setattr(
        gl, "insert_from_staging",
        lambda conn, schema_name, table_name, staging_table, columns: state.load(table_name, row_counts[table_name]),
    )
    monkeypatch.setattr(gl, "drop_staging_table", lambda *a, **k: None)

    conn1 = _FakeStatefulConnection(state)
    entries1 = gl.run_force_reload(conn1, gold_dir, gold_entries, set(table_keys), "run-1")
    counts_after_first = dict(state.committed)

    conn2 = _FakeStatefulConnection(state)  # second --force run, no docker reset
    entries2 = gl.run_force_reload(conn2, gold_dir, gold_entries, set(table_keys), "run-2")
    counts_after_second = dict(state.committed)

    assert [e["status"] for e in entries1] == ["processed"] * 3
    assert [e["status"] for e in entries2] == ["processed"] * 3
    assert counts_after_first == counts_after_second == row_counts
    assert conn1.commits == 1 and conn1.rollbacks == 0
    assert conn2.commits == 1 and conn2.rollbacks == 0


# -- run_gold_load(force=True) orchestration: whole-batch rollback + reporting ---


def _patch_force_full_run(monkeypatch, fail_on_table: str | None = None) -> list:
    monkeypatch.setattr(gl, "check_connectivity", lambda config=None: (True, None))

    @contextmanager
    def fake_get_connection(config=None):
        yield FakeConnection()

    monkeypatch.setattr(gl, "get_connection", fake_get_connection)
    monkeypatch.setattr(gl, "ensure_schema", lambda conn: None)
    recorded: list = []
    monkeypatch.setattr(gl, "record_load_manifest_entry", lambda conn, entry: recorded.append(entry))
    monkeypatch.setattr(gl, "record_load_error", lambda *a, **k: None)
    monkeypatch.setattr(gl, "record_load_run", lambda conn, entry: None)
    _patch_force_reload_staging_primitives(monkeypatch, fail_on_table=fail_on_table)
    return recorded


def test_run_gold_load_two_consecutive_force_runs_succeed(tmp_path, monkeypatch):
    gold_dir = tmp_path / "gold"
    table_keys = ["dim_patient", "fact_encounter", "mart_patient_360"]
    _write_force_reload_gold_files(gold_dir, table_keys)
    _write_gold_manifest(gold_dir, table_keys)
    _patch_force_full_run(monkeypatch)

    first = gl.run_gold_load(gold_dir, tables=table_keys, force=True)
    second = gl.run_gold_load(gold_dir, tables=table_keys, force=True)

    assert first["summary"] == {"total_tables": 3, "processed": 3, "skipped": 0, "failed": 0}
    assert second["summary"] == {"total_tables": 3, "processed": 3, "skipped": 0, "failed": 0}


def test_run_gold_load_force_failure_rolls_back_whole_batch_and_reports_all_failed(tmp_path, monkeypatch):
    gold_dir = tmp_path / "gold"
    table_keys = ["dim_patient", "fact_encounter"]
    _write_force_reload_gold_files(gold_dir, table_keys)
    _write_gold_manifest(gold_dir, table_keys)
    recorded = _patch_force_full_run(monkeypatch, fail_on_table="fact_encounter")

    report = gl.run_gold_load(gold_dir, tables=table_keys, force=True)

    assert report["summary"]["processed"] == 0
    assert report["summary"]["failed"] == 2
    assert all(t["status"] == "failed" for t in report["tables"])
    # the previously-valid warehouse is untouched: nothing was ever
    # recorded as processed, only the rolled-back failure for every table
    assert all(e["status"] == "failed" for e in recorded)


def test_run_gold_load_force_bad_gold_file_rolls_back_before_any_insert(tmp_path, monkeypatch):
    gold_dir = tmp_path / "gold"
    table_keys = ["dim_patient", "fact_encounter"]
    _write_force_reload_gold_files(gold_dir, table_keys)
    (gold_dir / WAREHOUSE_TABLES["fact_encounter"].gold_source_file).unlink()  # missing Gold source
    _write_gold_manifest(gold_dir, table_keys)
    _patch_force_full_run(monkeypatch)

    report = gl.run_gold_load(gold_dir, tables=table_keys, force=True)

    assert report["summary"]["failed"] == 2
    assert report["summary"]["processed"] == 0


def test_non_force_rerun_still_skips_unchanged_tables_end_to_end(tmp_path, monkeypatch):
    """Confirms the --force fix left the incremental checksum-skip path
    (run_gold_load with force=False) completely unaffected."""
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir()
    df = pd.DataFrame({"patient_key": [1, 2, 3]})
    df.to_parquet(gold_dir / "dim_patient.parquet", engine="pyarrow", index=False)
    _write_gold_manifest(gold_dir, ["dim_patient"])

    monkeypatch.setattr(gl, "check_connectivity", lambda config=None: (True, None))

    @contextmanager
    def fake_get_connection(config=None):
        yield FakeConnection()

    monkeypatch.setattr(gl, "get_connection", fake_get_connection)
    monkeypatch.setattr(gl, "ensure_schema", lambda conn: None)
    monkeypatch.setattr(gl, "record_load_error", lambda *a, **k: None)
    monkeypatch.setattr(gl, "record_load_run", lambda conn, entry: None)
    monkeypatch.setattr(gl, "create_staging_table", lambda *a, **k: None)
    monkeypatch.setattr(gl, "copy_dataframe", lambda *a, **k: 3)
    monkeypatch.setattr(gl, "validate_staging_rowcount", lambda *a, **k: 3)
    monkeypatch.setattr(gl, "atomic_replace", lambda *a, **k: None)
    monkeypatch.setattr(gl, "drop_staging_table", lambda *a, **k: None)

    persisted_checksum: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(
        gl, "latest_successful_checksum",
        lambda conn, schema_name, table_name: persisted_checksum.get((schema_name, table_name)),
    )

    def fake_record_manifest(conn, entry):
        if entry["status"] in ("processed", "skipped"):
            persisted_checksum[(entry["schema_name"], entry["table_name"])] = entry["source_checksum"]

    monkeypatch.setattr(gl, "record_load_manifest_entry", fake_record_manifest)

    first = gl.run_gold_load(gold_dir, tables=["dim_patient"], force=False)
    second = gl.run_gold_load(gold_dir, tables=["dim_patient"], force=False)

    assert first["summary"] == {"total_tables": 1, "processed": 1, "skipped": 0, "failed": 0}
    assert second["summary"] == {"total_tables": 1, "processed": 0, "skipped": 1, "failed": 0}


# -- report generation / no upstream Gold-file modification ----------------------


def test_write_load_report_json(tmp_path):
    report = {"run_id": "r1", "tables": [], "summary": {"total_tables": 0, "processed": 0, "skipped": 0, "failed": 0}}
    output_path = tmp_path / "report.json"
    gl.write_load_report_json(report, output_path)
    assert output_path.is_file()
    assert json.loads(output_path.read_text()) == report


def test_write_table_counts_csv(tmp_path):
    report = {"tables": [
        {"schema_name": "careflow_dim", "table_name": "dim_patient", "status": "processed",
         "source_rows": 58, "loaded_rows": 58, "load_method": "copy", "duration_seconds": 0.5},
    ]}
    output_path = tmp_path / "counts.csv"
    gl.write_table_counts_csv(report, output_path)
    content = output_path.read_text()
    assert "dim_patient" in content
    assert "58" in content


def test_no_upstream_gold_file_modification(tmp_path, monkeypatch):
    gold_dir, _entry = make_gold_dataset(tmp_path)
    _write_gold_manifest(gold_dir, ["dim_patient"])
    before = (gold_dir / "dim_patient.parquet").read_bytes()

    _patch_full_run(monkeypatch, {})
    gl.run_gold_load(gold_dir, tables=["dim_patient"])

    assert (gold_dir / "dim_patient.parquet").read_bytes() == before
