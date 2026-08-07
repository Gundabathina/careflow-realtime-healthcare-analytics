"""Transactional, incremental Gold-to-PostgreSQL loader.

Reads only from data/gold/. Writes only to PostgreSQL. Never modifies
data/raw, data/bronze, data/silver, or Gold Parquet files.

Each table load is staged (COPY into an unlogged, unconstrained staging
table), row-count validated, then atomically swapped into the target
table (DELETE + INSERT ... SELECT) inside a single transaction that
rolls back completely on any failure. A table is skipped when its Gold
``source_checksum`` matches the checksum recorded the last time it was
successfully loaded.

``--force``/``--truncate-and-load`` runs use a different, whole-batch
path (:func:`run_force_reload`): every in-scope table is cleared in
reverse dependency order (marts, facts, dimensions) and then reloaded
in dependency order (dimensions, facts, marts), all inside one
transaction, so a repeated force run never trips a foreign-key
violation deleting a dimension that a fact table still references, and
any failure rolls back the entire batch rather than leaving the
warehouse partially reloaded.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import psycopg

from careflow.gold.schema import UNKNOWN_SURROGATE_KEY
from careflow.logging_config import get_logger
from careflow.profiling.file_profiler import _relative_to_root
from careflow.warehouse.postgres_client import (
    PostgresConnectionConfig,
    WarehouseConnectionError,
    check_connectivity,
    get_connection,
    validate_identifier,
)
from careflow.warehouse.schema_manager import (
    ALLOWED_SCHEMA_NAMES,
    ALLOWED_TABLE_NAMES,
    DIMENSION_LOAD_ORDER,
    FACT_LOAD_ORDER,
    LOADER_VERSION,
    MART_LOAD_ORDER,
    WAREHOUSE_LOAD_ORDER,
    WAREHOUSE_SCHEMA_VERSION,
    WAREHOUSE_TABLES,
    WarehouseTableSpec,
    ensure_schema,
)

logger = get_logger(__name__)

GOLD_MANIFEST_FILENAME = "gold_manifest.json"


class GoldManifestNotFoundError(Exception):
    """Raised when data/gold/gold_manifest.json cannot be found or read."""


def _load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_entry(
    run_id: str, spec: WarehouseTableSpec, status: str,
    source_path: Path | None = None, source_checksum: str | None = None,
    source_rows: int | None = None, loaded_rows: int | None = None,
    started_at: datetime | None = None, completed_at: datetime | None = None,
    load_method: str | None = None, error: str | None = None,
) -> dict:
    duration = (completed_at - started_at).total_seconds() if started_at and completed_at else None
    return {
        "run_id": run_id,
        "schema_name": spec.schema_name,
        "table_name": spec.table_name,
        "source_path": _relative_to_root(source_path) if source_path else None,
        "source_checksum": source_checksum,
        "source_rows": source_rows,
        "loaded_rows": loaded_rows,
        "load_method": load_method,
        "status": status,
        "error_message": error,
        "duration_seconds": duration,
        "schema_version": WAREHOUSE_SCHEMA_VERSION,
        "loader_version": LOADER_VERSION,
        "started_at_utc": started_at.strftime("%Y-%m-%dT%H:%M:%SZ") if started_at else None,
        "completed_at_utc": completed_at.strftime("%Y-%m-%dT%H:%M:%SZ") if completed_at else None,
    }


# ---------------------------------------------------------------------------
# Data preparation
# ---------------------------------------------------------------------------


def prepare_dataframe_for_load(df: pd.DataFrame, primary_key: tuple[str, ...]) -> pd.DataFrame:
    """Null out unresolved dimension references; convert all missing values to None.

    Gold uses ``UNKNOWN_SURROGATE_KEY`` (-1) as an in-Parquet sentinel for
    an unresolved foreign key. PostgreSQL foreign keys must be either a
    valid reference or NULL, so every ``*_key`` column that is not this
    table's own primary key has -1 replaced with NULL before loading --
    the record is still loaded, never dropped, and the corresponding
    ``*_key_is_missing`` flag column (loaded as-is) records why.
    """
    working = df.copy()
    for col in working.columns:
        if col.endswith("_key") and not col.endswith("_is_missing"):
            # Cast to pandas' nullable Int64 *before* masking so a masked
            # value becomes pd.NA, not a float64 upcast -- floats lose
            # BIGINT-safe text formatting (e.g. large surrogate keys would
            # otherwise serialize as "3.575e+17", which COPY rejects).
            working[col] = working[col].astype("Int64")
            if col not in primary_key:
                working[col] = working[col].mask(working[col] == UNKNOWN_SURROGATE_KEY)

    null_mask = working.isna()
    object_df = working.astype(object)
    return object_df.where(~null_mask, None)


def _infer_staging_pg_type(column: str, dtype: object) -> str:
    """Rule-based (name + dtype) PostgreSQL type for an unconstrained staging column.

    Not a blind pandas-dtype-to-SQL-type mapping: surrogate/foreign keys,
    date keys, currency, ratios, and timestamps are recognized by name
    first. Only used for ephemeral staging tables -- the permanent
    dim/fact/mart tables are defined explicitly in postgres_schema.sql.
    """
    name = column.lower()
    dtype_str = str(dtype)

    if name.endswith("_key_is_missing"):
        return "BOOLEAN"
    if name.endswith("date_key"):
        return "INTEGER"
    if name.endswith("_key"):
        return "BIGINT"
    if name in ("day", "week_of_year", "month", "quarter", "year"):
        return "INTEGER"
    if "bool" in dtype_str.lower():
        return "BOOLEAN"
    if name in ("birth_date", "death_date", "full_date", "first_encounter_date", "last_encounter_date"):
        return "DATE"
    if dtype_str.startswith("datetime64") or name == "transformation_timestamp_utc":
        return "TIMESTAMPTZ"
    if any(token in name for token in ("cost", "coverage", "responsibility", "expenses", "revenue", "amount", "income")):
        return "NUMERIC(18,2)"
    if name in ("latitude", "longitude", "coverage_ratio", "readmission_rate_30_day"):
        return "DOUBLE PRECISION"
    if "duration" in name or name.startswith("average_") or name == "days_to_readmission":
        return "DOUBLE PRECISION"
    if "int" in dtype_str.lower():
        return "INTEGER"
    if "float" in dtype_str.lower():
        return "DOUBLE PRECISION"
    return "TEXT"


# ---------------------------------------------------------------------------
# Staging / COPY / atomic replace
# ---------------------------------------------------------------------------


def create_staging_table(
    conn: psycopg.Connection, schema_name: str, staging_table: str, columns: list[str], dtypes: dict,
) -> None:
    """Create the staging table using the *original* (pre-load-prep) dtypes.

    ``dtypes`` must come from the source DataFrame before
    :func:`prepare_dataframe_for_load` converts everything to ``object``
    dtype for COPY -- inferring types from the already-object-converted
    frame loses all type signal and silently produces TEXT columns.
    """
    validate_identifier(schema_name, ALLOWED_SCHEMA_NAMES)
    validate_identifier(staging_table)  # derived name; regex-safe, not from user input
    columns_sql = ", ".join(f'"{col}" {_infer_staging_pg_type(col, dtypes[col])}' for col in columns)
    with conn.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS "{schema_name}"."{staging_table}"')
        cur.execute(f'CREATE UNLOGGED TABLE "{schema_name}"."{staging_table}" ({columns_sql})')


def copy_dataframe(conn: psycopg.Connection, schema_name: str, table_name: str, df: pd.DataFrame, columns: list[str]) -> int:
    """Bulk-load ``df`` via PostgreSQL COPY (never row-by-row INSERT)."""
    validate_identifier(schema_name, ALLOWED_SCHEMA_NAMES)
    validate_identifier(table_name)
    col_list = ", ".join(f'"{c}"' for c in columns)
    target = f'"{schema_name}"."{table_name}"'
    with conn.cursor() as cur:
        with cur.copy(f"COPY {target} ({col_list}) FROM STDIN") as copy:
            for row in df[columns].itertuples(index=False, name=None):
                copy.write_row(row)
    return len(df)


def validate_staging_rowcount(conn: psycopg.Connection, schema_name: str, staging_table: str, expected_rows: int) -> int:
    validate_identifier(schema_name, ALLOWED_SCHEMA_NAMES)
    validate_identifier(staging_table)
    with conn.cursor() as cur:
        cur.execute(f'SELECT COUNT(*) FROM "{schema_name}"."{staging_table}"')
        (actual,) = cur.fetchone()
    if actual != expected_rows:
        raise ValueError(f"Staging row count mismatch: expected {expected_rows}, got {actual}")
    return actual


def atomic_replace(conn: psycopg.Connection, schema_name: str, table_name: str, staging_table: str, columns: list[str]) -> None:
    """Replace all rows in the target table with staging's contents, atomically.

    Uses ``DELETE FROM`` rather than ``TRUNCATE``: dimension tables are
    referenced by foreign keys from fact tables, and PostgreSQL refuses
    to ``TRUNCATE`` a table that has inbound FK references (without
    ``CASCADE``, which would destructively wipe the referencing fact
    data too -- not what an independent dimension reload should do).
    ``DELETE`` has no such restriction and is still fully transactional.
    """
    validate_identifier(schema_name, ALLOWED_SCHEMA_NAMES)
    validate_identifier(table_name, ALLOWED_TABLE_NAMES)
    validate_identifier(staging_table)
    col_list = ", ".join(f'"{c}"' for c in columns)
    target = f'"{schema_name}"."{table_name}"'
    staging = f'"{schema_name}"."{staging_table}"'
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM {target}")
        cur.execute(f"INSERT INTO {target} ({col_list}) SELECT {col_list} FROM {staging}")


def insert_from_staging(conn: psycopg.Connection, schema_name: str, table_name: str, staging_table: str, columns: list[str]) -> None:
    """Copy staging's rows into a target table that has already been cleared.

    Used by the transactional force-reload path (:func:`run_force_reload`),
    where every table's rows were already removed, in dependency-safe
    order, earlier in the same transaction -- so no DELETE is needed here.
    """
    validate_identifier(schema_name, ALLOWED_SCHEMA_NAMES)
    validate_identifier(table_name, ALLOWED_TABLE_NAMES)
    validate_identifier(staging_table)
    col_list = ", ".join(f'"{c}"' for c in columns)
    target = f'"{schema_name}"."{table_name}"'
    staging = f'"{schema_name}"."{staging_table}"'
    with conn.cursor() as cur:
        cur.execute(f"INSERT INTO {target} ({col_list}) SELECT {col_list} FROM {staging}")


def drop_staging_table(conn: psycopg.Connection, schema_name: str, staging_table: str) -> None:
    validate_identifier(schema_name, ALLOWED_SCHEMA_NAMES)
    validate_identifier(staging_table)
    with conn.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS "{schema_name}"."{staging_table}"')


# ---------------------------------------------------------------------------
# Metadata / audit persistence
# ---------------------------------------------------------------------------


def latest_successful_checksum(conn: psycopg.Connection, schema_name: str, table_name: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT source_checksum FROM careflow_meta.load_manifest
            WHERE schema_name = %s AND table_name = %s AND status IN ('processed', 'skipped')
            ORDER BY completed_at_utc DESC NULLS LAST, id DESC
            LIMIT 1
            """,
            (schema_name, table_name),
        )
        row = cur.fetchone()
    return row[0] if row else None


def record_load_manifest_entry(conn: psycopg.Connection, entry: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO careflow_meta.load_manifest
                (run_id, schema_name, table_name, source_path, source_checksum, source_rows, loaded_rows,
                 load_method, status, error_message, duration_seconds, schema_version, loader_version,
                 started_at_utc, completed_at_utc)
            VALUES (%(run_id)s, %(schema_name)s, %(table_name)s, %(source_path)s, %(source_checksum)s,
                    %(source_rows)s, %(loaded_rows)s, %(load_method)s, %(status)s, %(error_message)s,
                    %(duration_seconds)s, %(schema_version)s, %(loader_version)s, %(started_at_utc)s,
                    %(completed_at_utc)s)
            """,
            entry,
        )
    conn.commit()


def record_load_error(conn: psycopg.Connection, run_id: str, table_name: str | None, message: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO careflow_audit.load_error (run_id, table_name, error_message, occurred_at_utc) VALUES (%s, %s, %s, %s)",
            (run_id, table_name, message, datetime.now(timezone.utc)),
        )
    conn.commit()


def record_load_run(conn: psycopg.Connection, entry: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO careflow_audit.load_run
                (run_id, started_at_utc, completed_at_utc, gold_manifest_checksum, status,
                 tables_processed, tables_skipped, tables_failed)
            VALUES (%(run_id)s, %(started_at_utc)s, %(completed_at_utc)s, %(gold_manifest_checksum)s,
                    %(status)s, %(tables_processed)s, %(tables_skipped)s, %(tables_failed)s)
            ON CONFLICT (run_id) DO UPDATE SET
                completed_at_utc = EXCLUDED.completed_at_utc,
                status = EXCLUDED.status,
                tables_processed = EXCLUDED.tables_processed,
                tables_skipped = EXCLUDED.tables_skipped,
                tables_failed = EXCLUDED.tables_failed
            """,
            entry,
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Per-table load
# ---------------------------------------------------------------------------


def load_table(
    conn: psycopg.Connection,
    gold_dir: Path,
    table_key: str,
    gold_entry: dict | None,
    force: bool,
    run_id: str,
) -> dict:
    """Load one Gold table into PostgreSQL, or skip/fail with a recorded reason.

    Never raises: every outcome (processed/skipped/failed) is returned as
    a load-manifest-shaped dict so the caller can continue with the rest
    of the run.
    """
    spec = WAREHOUSE_TABLES[table_key]
    validate_identifier(spec.schema_name, ALLOWED_SCHEMA_NAMES)
    validate_identifier(spec.table_name, ALLOWED_TABLE_NAMES)
    started_at = datetime.now(timezone.utc)

    if gold_entry is None or gold_entry.get("status") not in ("processed", "skipped"):
        status = gold_entry.get("status") if gold_entry else "missing"
        return _load_entry(run_id, spec, "failed", started_at=started_at, completed_at=datetime.now(timezone.utc),
                            error=f"Gold table '{table_key}' not available (status={status})")

    source_path = gold_dir / spec.gold_source_file
    source_checksum = gold_entry.get("source_checksum")
    source_rows = gold_entry.get("target_rows")

    if not source_path.is_file():
        return _load_entry(run_id, spec, "failed", source_path, source_checksum, source_rows,
                            started_at=started_at, completed_at=datetime.now(timezone.utc),
                            error=f"Gold Parquet file missing: {source_path}")

    previous_checksum = latest_successful_checksum(conn, spec.schema_name, spec.table_name)
    if not force and previous_checksum is not None and previous_checksum == source_checksum:
        completed_at = datetime.now(timezone.utc)
        logger.info("Warehouse skip %s (Gold checksum unchanged)", table_key)
        return _load_entry(run_id, spec, "skipped", source_path, source_checksum, source_rows, source_rows,
                            started_at, completed_at, load_method="skipped")

    try:
        df = pd.read_parquet(source_path)
    except Exception as exc:  # noqa: BLE001
        return _load_entry(run_id, spec, "failed", source_path, source_checksum, source_rows,
                            started_at=started_at, completed_at=datetime.now(timezone.utc),
                            error=f"Could not read Gold Parquet file: {exc}")

    original_dtypes = df.dtypes.to_dict()
    prepared = prepare_dataframe_for_load(df, spec.primary_key)
    columns = list(prepared.columns)
    staging_table = f"stg_{spec.table_name}"

    try:
        with conn.transaction():
            create_staging_table(conn, spec.schema_name, staging_table, columns, original_dtypes)
            copy_dataframe(conn, spec.schema_name, staging_table, prepared, columns)
            validate_staging_rowcount(conn, spec.schema_name, staging_table, len(prepared))
            atomic_replace(conn, spec.schema_name, spec.table_name, staging_table, columns)
            drop_staging_table(conn, spec.schema_name, staging_table)
    except Exception as exc:  # noqa: BLE001 - conn.transaction() has already rolled back
        logger.warning("Warehouse load %s -> failed: %s", table_key, exc)
        return _load_entry(run_id, spec, "failed", source_path, source_checksum, source_rows,
                            started_at=started_at, completed_at=datetime.now(timezone.utc), error=str(exc))

    completed_at = datetime.now(timezone.utc)
    logger.info("Warehouse load %s -> processed (%d rows)", table_key, len(prepared))
    return _load_entry(run_id, spec, "processed", source_path, source_checksum, source_rows, len(prepared),
                        started_at, completed_at, load_method="copy")


# ---------------------------------------------------------------------------
# Transactional force reload
#
# A plain per-table DELETE+INSERT (as ``load_table`` does for the
# checksum-skipping incremental path) is safe incrementally, but not for
# a full ``--force`` reload: dimensions are loaded before facts, so
# deleting a dimension's rows fails immediately on the inbound foreign
# key from a fact table that still holds its *previous* load's rows.
# The fix is a single all-or-nothing transaction that clears every
# in-scope table in reverse dependency order (marts, then facts, then
# dimensions -- so a DELETE never has a live inbound reference) and only
# then reloads in dependency order (dimensions, then facts, then marts).
# Any failure anywhere raises out of the transaction, rolling back every
# clear and reload together and leaving the previously-loaded warehouse
# exactly as it was.
# ---------------------------------------------------------------------------


class ForceReloadStageError(Exception):
    """A specific table failed during a specific stage of a transactional force reload."""

    def __init__(self, table_key: str, stage: str, original: BaseException):
        self.table_key = table_key
        self.stage = stage
        self.original = original
        super().__init__(f"{stage} failed for '{table_key}': {original}")


def _clear_table_rows(conn: psycopg.Connection, spec: WarehouseTableSpec) -> None:
    """DELETE every row from one warehouse table (never TRUNCATE/DROP/CASCADE)."""
    validate_identifier(spec.schema_name, ALLOWED_SCHEMA_NAMES)
    validate_identifier(spec.table_name, ALLOWED_TABLE_NAMES)
    with conn.cursor() as cur:
        cur.execute(f'DELETE FROM "{spec.schema_name}"."{spec.table_name}"')


def _force_reload_one_table(
    conn: psycopg.Connection, gold_dir: Path, table_key: str, gold_entry: dict | None, run_id: str,
) -> dict:
    """Stage and load one table's Gold data into its already-cleared target.

    Only used inside :func:`run_force_reload`'s single transaction --
    raises on any failure instead of returning a "failed" entry, so the
    caller's transaction rolls back in full rather than committing a
    partially-reloaded warehouse.
    """
    spec = WAREHOUSE_TABLES[table_key]
    started_at = datetime.now(timezone.utc)

    if gold_entry is None or gold_entry.get("status") not in ("processed", "skipped"):
        status = gold_entry.get("status") if gold_entry else "missing"
        raise RuntimeError(f"Gold table '{table_key}' not available (status={status})")

    source_path = gold_dir / spec.gold_source_file
    source_checksum = gold_entry.get("source_checksum")
    source_rows = gold_entry.get("target_rows")
    if not source_path.is_file():
        raise RuntimeError(f"Gold Parquet file missing: {source_path}")

    df = pd.read_parquet(source_path)
    original_dtypes = df.dtypes.to_dict()
    prepared = prepare_dataframe_for_load(df, spec.primary_key)
    columns = list(prepared.columns)
    staging_table = f"stg_{spec.table_name}"

    create_staging_table(conn, spec.schema_name, staging_table, columns, original_dtypes)
    copy_dataframe(conn, spec.schema_name, staging_table, prepared, columns)
    validate_staging_rowcount(conn, spec.schema_name, staging_table, len(prepared))
    insert_from_staging(conn, spec.schema_name, spec.table_name, staging_table, columns)
    drop_staging_table(conn, spec.schema_name, staging_table)

    completed_at = datetime.now(timezone.utc)
    logger.info("Force reload %s -> processed (%d rows)", table_key, len(prepared))
    return _load_entry(run_id, spec, "processed", source_path, source_checksum, source_rows, len(prepared),
                        started_at, completed_at, load_method="force_reload")


def run_force_reload(
    conn: psycopg.Connection, gold_dir: Path, gold_entries: dict, in_scope: set[str], run_id: str,
) -> list[dict]:
    """Clear and reload every in-scope table in one all-or-nothing transaction.

    Clear order is marts, then facts, then dimensions (reverse of load
    dependency order); reload order is dimensions, then facts, then
    marts. Raises :class:`ForceReloadStageError` -- and rolls the entire
    transaction back, clears included -- on the first failure anywhere.
    """
    clear_order = [k for k in MART_LOAD_ORDER + FACT_LOAD_ORDER + DIMENSION_LOAD_ORDER if k in in_scope]
    reload_order = [k for k in WAREHOUSE_LOAD_ORDER if k in in_scope]

    entries: list[dict] = []
    with conn.transaction():
        for table_key in clear_order:
            try:
                _clear_table_rows(conn, WAREHOUSE_TABLES[table_key])
            except Exception as exc:  # noqa: BLE001 - re-raised with context; transaction rolls back on exit
                raise ForceReloadStageError(table_key, "clear", exc) from exc
        for table_key in reload_order:
            try:
                entry = _force_reload_one_table(conn, gold_dir, table_key, gold_entries.get(table_key), run_id)
            except Exception as exc:  # noqa: BLE001
                raise ForceReloadStageError(table_key, "reload", exc) from exc
            entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


def run_gold_load(
    gold_dir: Path,
    tables: list[str] | None = None,
    force: bool = False,
    schema_only: bool = False,
    truncate_and_load: bool = False,
    fail_fast: bool = False,
    config: PostgresConnectionConfig | None = None,
) -> dict:
    """Run the full incremental, transactional Gold -> PostgreSQL load.

    ``truncate_and_load`` forces a full reload of every selected table
    (equivalent to ``force``) rather than relying on the checksum-based
    skip -- useful for an explicit "rebuild everything" run.
    """
    run_id = f"warehouse_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    started_at = datetime.now(timezone.utc)
    effective_force = force or truncate_and_load

    ok, reason = check_connectivity(config)
    if not ok:
        raise WarehouseConnectionError(reason or "PostgreSQL is not reachable")

    gold_manifest_path = gold_dir / GOLD_MANIFEST_FILENAME
    gold_manifest = _load_json(gold_manifest_path)
    if gold_manifest is None:
        raise GoldManifestNotFoundError(f"Gold manifest not found: {gold_manifest_path}")
    gold_entries = {e["table"]: e for e in gold_manifest.get("tables", [])}
    gold_manifest_checksum = gold_manifest.get("run_id", "")

    if tables:
        unknown = set(tables) - set(WAREHOUSE_LOAD_ORDER)
        if unknown:
            raise ValueError(f"Unknown warehouse table(s) requested: {sorted(unknown)}")
        in_scope = set(tables)
    else:
        in_scope = set(WAREHOUSE_LOAD_ORDER)

    entries: list[dict] = []
    with get_connection(config) as conn:
        ensure_schema(conn)

        if schema_only:
            completed_at = datetime.now(timezone.utc)
            run_entry = {
                "run_id": run_id, "started_at_utc": started_at, "completed_at_utc": completed_at,
                "gold_manifest_checksum": gold_manifest_checksum, "status": "schema_only",
                "tables_processed": 0, "tables_skipped": 0, "tables_failed": 0,
            }
            record_load_run(conn, run_entry)
            return {
                "run_id": run_id,
                "started_at_utc": started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "completed_at_utc": completed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "gold_manifest_path": _relative_to_root(gold_manifest_path),
                "gold_manifest_checksum": gold_manifest_checksum,
                "schema_version": WAREHOUSE_SCHEMA_VERSION,
                "loader_version": LOADER_VERSION,
                "mode": "schema_only",
                "tables": [],
                "summary": {"total_tables": 0, "processed": 0, "skipped": 0, "failed": 0},
            }

        if effective_force:
            # Whole-batch transactional reload (see run_force_reload):
            # avoids the FK-violation-on-dimension-delete idempotency bug
            # that a per-table delete+insert loop hits on a repeated
            # --force run. Any failure anywhere rolls back every clear
            # and reload together, so the previously-loaded warehouse is
            # left exactly as it was -- there is no partial "fail-fast
            # but keep what succeeded" mode here by design.
            try:
                entries = run_force_reload(conn, gold_dir, gold_entries, in_scope, run_id)
            except ForceReloadStageError as exc:
                logger.warning("Force reload rolled back in full: %s", exc)
                failure_time = datetime.now(timezone.utc)
                entries = []
                for table_key in (k for k in WAREHOUSE_LOAD_ORDER if k in in_scope):
                    if table_key == exc.table_key:
                        message = f"Force reload rolled back ({exc.stage} stage): {exc.original}"
                    else:
                        message = f"Force reload rolled back because '{exc.table_key}' failed during its {exc.stage} stage"
                    entries.append(_load_entry(run_id, WAREHOUSE_TABLES[table_key], "failed",
                                                started_at=failure_time, completed_at=failure_time, error=message))
                for entry in entries:
                    record_load_manifest_entry(conn, entry)
                record_load_error(conn, run_id, exc.table_key, str(exc.original))
            else:
                for entry in entries:
                    record_load_manifest_entry(conn, entry)
        else:
            for table_key in WAREHOUSE_LOAD_ORDER:
                if table_key not in in_scope:
                    continue
                entry = load_table(conn, gold_dir, table_key, gold_entries.get(table_key), effective_force, run_id)
                entries.append(entry)
                record_load_manifest_entry(conn, entry)
                if entry["status"] == "failed":
                    record_load_error(conn, run_id, table_key, entry["error_message"] or "unknown error")
                    if fail_fast:
                        logger.warning("Stopping warehouse load early: --fail-fast and '%s' failed", table_key)
                        break

        completed_at = datetime.now(timezone.utc)
        counts = {"processed": 0, "skipped": 0, "failed": 0}
        for entry in entries:
            counts[entry["status"]] = counts.get(entry["status"], 0) + 1
        run_status = "failed" if counts["failed"] else "completed"
        record_load_run(conn, {
            "run_id": run_id, "started_at_utc": started_at, "completed_at_utc": completed_at,
            "gold_manifest_checksum": gold_manifest_checksum, "status": run_status,
            "tables_processed": counts["processed"], "tables_skipped": counts["skipped"],
            "tables_failed": counts["failed"],
        })

    return {
        "run_id": run_id,
        "started_at_utc": started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "completed_at_utc": completed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gold_manifest_path": _relative_to_root(gold_manifest_path),
        "gold_manifest_checksum": gold_manifest_checksum,
        "schema_version": WAREHOUSE_SCHEMA_VERSION,
        "loader_version": LOADER_VERSION,
        "mode": "full",
        "tables": entries,
        "summary": {
            "total_tables": len(entries),
            "processed": counts["processed"],
            "skipped": counts["skipped"],
            "failed": counts["failed"],
        },
    }


def write_load_report_json(report: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
        fh.write("\n")


def write_table_counts_csv(report: dict, output_path: Path) -> None:
    import csv as csv_module

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["schema_name", "table_name", "status", "source_rows", "loaded_rows", "load_method", "duration_seconds"]
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv_module.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for entry in report["tables"]:
            writer.writerow({key: entry.get(key) for key in fieldnames})
