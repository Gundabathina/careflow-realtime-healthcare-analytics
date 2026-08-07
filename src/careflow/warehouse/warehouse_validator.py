"""Validates a loaded PostgreSQL warehouse against Gold's own outputs.

Every check compares live PostgreSQL state to Gold Parquet files or
``gold_kpi_summary.json`` -- Gold remains the source of truth. Read-only:
this module never writes to the warehouse or to any upstream layer.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import psycopg
import pyarrow.parquet as pq

from careflow.logging_config import get_logger
from careflow.warehouse.postgres_client import PostgresConnectionConfig, get_connection, validate_identifier
from careflow.warehouse.schema_manager import ALLOWED_SCHEMA_NAMES, ALLOWED_TABLE_NAMES, WAREHOUSE_SCHEMAS, WAREHOUSE_TABLES

logger = get_logger(__name__)

DEFAULT_CURRENCY_TOLERANCE = 0.01
DEFAULT_KPI_TOLERANCE = 0.001

RESTRICTED_PII_COLUMNS: tuple[str, ...] = (
    "ssn", "passport", "drivers", "driver_license", "address", "street_address",
    "latitude", "longitude", "first", "middle", "last", "maiden", "full_name",
)

# (table, fk_column, referenced_table, referenced_column) -- includes both
# database-enforced FKs (dimension references, always expected to be zero
# orphans post-load) and the intentionally-unconstrained fact-to-fact
# encounter_key references, whose orphan counts are reported rather than
# enforced (see postgres_schema.sql header for the rationale).
FOREIGN_KEY_CHECKS: tuple[tuple[str, str, str, str], ...] = (
    ("fact_encounter", "patient_key", "dim_patient", "patient_key"),
    ("fact_encounter", "provider_key", "dim_provider", "provider_key"),
    ("fact_encounter", "organization_key", "dim_organization", "organization_key"),
    ("fact_encounter", "payer_key", "dim_payer", "payer_key"),
    ("fact_condition", "patient_key", "dim_patient", "patient_key"),
    ("fact_condition", "condition_key", "dim_condition", "condition_key"),
    ("fact_condition", "encounter_key", "fact_encounter", "encounter_key"),
    ("fact_procedure", "patient_key", "dim_patient", "patient_key"),
    ("fact_procedure", "procedure_key", "dim_procedure", "procedure_key"),
    ("fact_procedure", "encounter_key", "fact_encounter", "encounter_key"),
    ("fact_medication", "patient_key", "dim_patient", "patient_key"),
    ("fact_medication", "payer_key", "dim_payer", "payer_key"),
    ("fact_medication", "medication_key", "dim_medication", "medication_key"),
    ("fact_medication", "encounter_key", "fact_encounter", "encounter_key"),
    ("fact_observation", "patient_key", "dim_patient", "patient_key"),
    ("fact_observation", "encounter_key", "fact_encounter", "encounter_key"),
    ("fact_claim", "patient_key", "dim_patient", "patient_key"),
    ("fact_claim", "provider_key", "dim_provider", "provider_key"),
    ("fact_claim", "payer_key", "dim_payer", "payer_key"),
    ("fact_claim", "encounter_key", "fact_encounter", "encounter_key"),
    ("fact_immunization", "patient_key", "dim_patient", "patient_key"),
    ("fact_immunization", "encounter_key", "fact_encounter", "encounter_key"),
    ("fact_imaging_study", "patient_key", "dim_patient", "patient_key"),
    ("fact_imaging_study", "encounter_key", "fact_encounter", "encounter_key"),
)

DATE_KEY_CHECKS: tuple[tuple[str, str], ...] = (
    ("fact_encounter", "encounter_date_key"),
    ("fact_condition", "start_date_key"),
    ("fact_condition", "stop_date_key"),
    ("fact_procedure", "start_date_key"),
    ("fact_procedure", "stop_date_key"),
    ("fact_medication", "start_date_key"),
    ("fact_medication", "stop_date_key"),
    ("fact_observation", "observation_date_key"),
    ("fact_claim", "service_date_key"),
    ("fact_immunization", "immunization_date_key"),
    ("fact_imaging_study", "study_date_key"),
)

CURRENCY_RECONCILIATION_CHECKS: tuple[tuple[str, str], ...] = (
    ("fact_encounter", "total_claim_cost"),
    ("fact_encounter", "payer_coverage"),
    ("fact_encounter", "patient_responsibility"),
    ("fact_procedure", "base_cost"),
    ("fact_medication", "total_cost"),
)

VIEW_NAMES: tuple[str, ...] = (
    "vw_patient_summary", "vw_readmission_analysis", "vw_hospital_operations",
    "vw_financial_performance", "vw_provider_utilization", "vw_monthly_kpis",
)


def _check(check_id: str, table: str | None, category: str, status: str, details: str,
           records_evaluated: int | None = None, records_failed: int | None = None) -> dict:
    return {
        "check_id": check_id, "table": table, "category": category, "status": status, "details": details,
        "records_evaluated": records_evaluated, "records_failed": records_failed,
    }


# ---------------------------------------------------------------------------
# Structural checks
# ---------------------------------------------------------------------------


def check_schemas_exist(conn: psycopg.Connection) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT schema_name FROM information_schema.schemata")
        existing = {row[0] for row in cur.fetchall()}
    checks = []
    for schema in WAREHOUSE_SCHEMAS:
        present = schema in existing
        checks.append(_check(f"schema_exists:{schema}", None, "structural",
                              "pass" if present else "fail",
                              "Schema present" if present else "Schema missing", 1, 0 if present else 1))
    return checks


def check_tables_exist(conn: psycopg.Connection) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT table_schema, table_name FROM information_schema.tables")
        existing = {(row[0], row[1]) for row in cur.fetchall()}
    checks = []
    for key, spec in WAREHOUSE_TABLES.items():
        present = (spec.schema_name, spec.table_name) in existing
        checks.append(_check(f"table_exists:{key}", key, "structural",
                              "pass" if present else "fail",
                              "Table present" if present else "Table missing", 1, 0 if present else 1))
    return checks


def check_columns_match_gold(conn: psycopg.Connection, gold_dir: Path) -> list[dict]:
    """Every column present in the Gold Parquet file must exist in the loaded table."""
    checks = []
    for key, spec in WAREHOUSE_TABLES.items():
        gold_path = gold_dir / spec.gold_source_file
        if not gold_path.is_file():
            checks.append(_check(f"columns_match_gold:{key}", key, "structural", "skipped",
                                  f"Gold file not found: {gold_path}"))
            continue
        gold_columns = set(pd.read_parquet(gold_path).columns)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s",
                (spec.schema_name, spec.table_name),
            )
            pg_columns = {row[0] for row in cur.fetchall()}
        missing = sorted(gold_columns - pg_columns)
        checks.append(_check(f"columns_match_gold:{key}", key, "structural",
                              "pass" if not missing else "fail",
                              "All Gold columns present" if not missing else f"Missing columns: {missing}",
                              len(gold_columns), len(missing)))
    return checks


def check_row_counts(conn: psycopg.Connection, gold_dir: Path) -> list[dict]:
    checks = []
    for key, spec in WAREHOUSE_TABLES.items():
        gold_path = gold_dir / spec.gold_source_file
        if not gold_path.is_file():
            checks.append(_check(f"row_count:{key}", key, "completeness", "skipped", "Gold file not found"))
            continue
        gold_rows = pq.ParquetFile(str(gold_path)).metadata.num_rows
        validate_identifier(spec.schema_name, ALLOWED_SCHEMA_NAMES)
        validate_identifier(spec.table_name, ALLOWED_TABLE_NAMES)
        with conn.cursor() as cur:
            cur.execute(f'SELECT COUNT(*) FROM "{spec.schema_name}"."{spec.table_name}"')
            (pg_rows,) = cur.fetchone()
        match = pg_rows == gold_rows
        checks.append(_check(f"row_count:{key}", key, "completeness",
                              "pass" if match else "fail",
                              f"Gold rows={gold_rows}, PostgreSQL rows={pg_rows}",
                              gold_rows, 0 if match else abs(pg_rows - gold_rows)))
    return checks


# ---------------------------------------------------------------------------
# Key integrity checks
# ---------------------------------------------------------------------------


def check_primary_keys(conn: psycopg.Connection) -> list[dict]:
    checks = []
    for key, spec in WAREHOUSE_TABLES.items():
        if not spec.primary_key:
            continue
        validate_identifier(spec.schema_name, ALLOWED_SCHEMA_NAMES)
        validate_identifier(spec.table_name, ALLOWED_TABLE_NAMES)
        pk_cols = ", ".join(f'"{c}"' for c in spec.primary_key)
        null_predicate = " OR ".join(f'"{c}" IS NULL' for c in spec.primary_key)
        with conn.cursor() as cur:
            cur.execute(f'SELECT COUNT(*) FROM "{spec.schema_name}"."{spec.table_name}" WHERE {null_predicate}')
            (null_count,) = cur.fetchone()
            cur.execute(
                f'SELECT COUNT(*) FROM (SELECT {pk_cols} FROM "{spec.schema_name}"."{spec.table_name}" '
                f'GROUP BY {pk_cols} HAVING COUNT(*) > 1) dupes'
            )
            (dup_groups,) = cur.fetchone()
        checks.append(_check(f"primary_key_not_null:{key}", key, "completeness",
                              "pass" if null_count == 0 else "fail",
                              f"{null_count} row(s) with a null primary key", None, null_count))
        checks.append(_check(f"primary_key_unique:{key}", key, "uniqueness",
                              "pass" if dup_groups == 0 else "fail",
                              f"{dup_groups} duplicate primary key group(s)", None, dup_groups))
    return checks


def check_foreign_key_orphans(conn: psycopg.Connection) -> list[dict]:
    checks = []
    for table, fk_col, ref_table, ref_col in FOREIGN_KEY_CHECKS:
        spec = WAREHOUSE_TABLES[table]
        ref_spec = WAREHOUSE_TABLES[ref_table]
        for name in (spec.schema_name, ref_spec.schema_name):
            validate_identifier(name, ALLOWED_SCHEMA_NAMES)
        for name in (spec.table_name, ref_spec.table_name):
            validate_identifier(name, ALLOWED_TABLE_NAMES)
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT COUNT(*) FROM "{spec.schema_name}"."{spec.table_name}" t '
                f'LEFT JOIN "{ref_spec.schema_name}"."{ref_spec.table_name}" r ON t."{fk_col}" = r."{ref_col}" '
                f'WHERE t."{fk_col}" IS NOT NULL AND r."{ref_col}" IS NULL'
            )
            (orphans,) = cur.fetchone()
            cur.execute(f'SELECT COUNT(*) FROM "{spec.schema_name}"."{spec.table_name}" WHERE "{fk_col}" IS NOT NULL')
            (evaluated,) = cur.fetchone()
        checks.append(_check(
            f"foreign_key_orphans:{table}:{fk_col}", table, "referential",
            "pass" if orphans == 0 else "warning",
            f"{orphans} orphaned '{fk_col}' value(s) not present in {ref_table}.{ref_col}",
            evaluated, orphans,
        ))
    return checks


def check_date_keys_resolve(conn: psycopg.Connection) -> list[dict]:
    checks = []
    for table, col in DATE_KEY_CHECKS:
        spec = WAREHOUSE_TABLES[table]
        validate_identifier(spec.schema_name, ALLOWED_SCHEMA_NAMES)
        validate_identifier(spec.table_name, ALLOWED_TABLE_NAMES)
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT COUNT(*) FROM "{spec.schema_name}"."{spec.table_name}" t '
                f'LEFT JOIN careflow_dim.dim_date d ON t."{col}" = d.date_key '
                f'WHERE t."{col}" IS NOT NULL AND d.date_key IS NULL'
            )
            (unresolved,) = cur.fetchone()
            cur.execute(f'SELECT COUNT(*) FROM "{spec.schema_name}"."{spec.table_name}" WHERE "{col}" IS NOT NULL')
            (evaluated,) = cur.fetchone()
        checks.append(_check(
            f"date_key_resolves:{table}:{col}", table, "referential",
            "pass" if unresolved == 0 else "warning",
            f"{unresolved} '{col}' value(s) do not resolve to dim_date", evaluated, unresolved,
        ))
    return checks


# ---------------------------------------------------------------------------
# Reconciliation checks
# ---------------------------------------------------------------------------


def check_currency_reconciliation(conn: psycopg.Connection, gold_dir: Path, tolerance: float = DEFAULT_CURRENCY_TOLERANCE) -> list[dict]:
    checks = []
    for table, col in CURRENCY_RECONCILIATION_CHECKS:
        spec = WAREHOUSE_TABLES[table]
        gold_path = gold_dir / spec.gold_source_file
        if not gold_path.is_file():
            checks.append(_check(f"currency_reconciliation:{table}:{col}", table, "numeric", "skipped", "Gold file not found"))
            continue
        gold_df = pd.read_parquet(gold_path, columns=[col])
        gold_total = float(gold_df[col].dropna().sum())
        validate_identifier(spec.schema_name, ALLOWED_SCHEMA_NAMES)
        validate_identifier(spec.table_name, ALLOWED_TABLE_NAMES)
        with conn.cursor() as cur:
            cur.execute(f'SELECT COALESCE(SUM("{col}"), 0) FROM "{spec.schema_name}"."{spec.table_name}"')
            (pg_total,) = cur.fetchone()
        pg_total = float(pg_total)
        within_tolerance = abs(pg_total - gold_total) <= tolerance
        checks.append(_check(
            f"currency_reconciliation:{table}:{col}", table, "numeric",
            "pass" if within_tolerance else "fail",
            f"Gold sum={gold_total:.2f}, PostgreSQL sum={pg_total:.2f}, tolerance={tolerance}",
            1, 0 if within_tolerance else 1,
        ))
    return checks


def check_readmission_counts_match(conn: psycopg.Connection, gold_dir: Path) -> list[dict]:
    gold_path = gold_dir / "mart_readmission.parquet"
    if not gold_path.is_file():
        return [_check("readmission_counts_match", "mart_readmission", "completeness", "skipped", "Gold file not found")]
    gold_df = pd.read_parquet(gold_path)
    gold_rows = len(gold_df)
    gold_30d = int(gold_df["readmitted_within_30_days"].sum())
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*), COALESCE(SUM(readmitted_within_30_days::int), 0) FROM careflow_mart.mart_readmission")
        pg_rows, pg_30d = cur.fetchone()
    match = pg_rows == gold_rows and pg_30d == gold_30d
    return [_check(
        "readmission_counts_match", "mart_readmission", "completeness",
        "pass" if match else "fail",
        f"Gold rows={gold_rows}/30d={gold_30d}, PostgreSQL rows={pg_rows}/30d={pg_30d}",
        gold_rows, 0 if match else abs(pg_rows - gold_rows) + abs(pg_30d - gold_30d),
    )]


_KPI_QUERIES: dict[str, str] = {
    "cost_per_encounter": 'SELECT COALESCE(SUM(total_claim_cost),0), COUNT(total_claim_cost) FROM careflow_fact.fact_encounter WHERE total_claim_cost IS NOT NULL',
    "payer_coverage_ratio": 'SELECT COALESCE(SUM(payer_coverage),0), COALESCE(SUM(total_claim_cost),0) FROM careflow_fact.fact_encounter',
    "encounters_per_patient": 'SELECT COUNT(*), COUNT(DISTINCT patient_key) FROM careflow_fact.fact_encounter',
    "readmission_rate_30_day": 'SELECT COALESCE(SUM(readmitted_within_30_days::int),0), COUNT(*) FROM careflow_mart.mart_readmission',
}


def check_kpi_values_match(conn: psycopg.Connection, gold_kpi_summary_path: Path, tolerance: float = DEFAULT_KPI_TOLERANCE) -> list[dict]:
    if not gold_kpi_summary_path.is_file():
        return [_check("kpi_values_match", "kpi", "numeric", "skipped", f"Gold KPI summary not found: {gold_kpi_summary_path}")]
    with gold_kpi_summary_path.open("r", encoding="utf-8") as fh:
        gold_kpis = {k["kpi_name"]: k for k in json.load(fh).get("kpis", [])}

    checks = []
    for kpi_name, query in _KPI_QUERIES.items():
        gold_kpi = gold_kpis.get(kpi_name)
        if gold_kpi is None or gold_kpi.get("value") is None:
            checks.append(_check(f"kpi_matches_gold:{kpi_name}", "kpi", "numeric", "skipped", "KPI not present in Gold summary"))
            continue
        with conn.cursor() as cur:
            cur.execute(query)
            numerator, denominator = cur.fetchone()
        pg_value = (float(numerator) / float(denominator)) if denominator else None
        gold_value = gold_kpi["value"]
        matches = pg_value is not None and abs(pg_value - gold_value) <= tolerance
        checks.append(_check(
            f"kpi_matches_gold:{kpi_name}", "kpi", "numeric",
            "pass" if matches else "fail",
            f"Gold value={gold_value}, PostgreSQL value={pg_value}, tolerance={tolerance}",
            1, 0 if matches else 1,
        ))
    return checks


# ---------------------------------------------------------------------------
# View checks
# ---------------------------------------------------------------------------


def check_views_execute(conn: psycopg.Connection) -> list[dict]:
    checks = []
    for view in VIEW_NAMES:
        validate_identifier(view)
        try:
            with conn.cursor() as cur:
                cur.execute(f'SELECT * FROM "careflow_mart"."{view}" LIMIT 1')
                cur.fetchall()
            checks.append(_check(f"view_executes:{view}", view, "structural", "pass", "View executed successfully"))
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            checks.append(_check(f"view_executes:{view}", view, "structural", "fail", str(exc)))
    return checks


def check_views_exclude_pii(conn: psycopg.Connection) -> list[dict]:
    checks = []
    for view in VIEW_NAMES:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'careflow_mart' AND table_name = %s",
                (view,),
            )
            columns = {row[0].lower() for row in cur.fetchall()}
        exposed = sorted(columns & set(RESTRICTED_PII_COLUMNS))
        checks.append(_check(
            f"view_excludes_pii:{view}", view, "security",
            "pass" if not exposed else "fail",
            "No restricted PII columns exposed" if not exposed else f"Restricted column(s) exposed: {exposed}",
            len(RESTRICTED_PII_COLUMNS), len(exposed),
        ))
    return checks


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_validation(
    gold_dir: Path, reports_dir: Path, config: PostgresConnectionConfig | None = None,
) -> tuple[dict, list[dict]]:
    """Run every validation check; return (report, orphan_summary_rows)."""
    checks: list[dict] = []
    orphan_rows: list[dict] = []

    with get_connection(config) as conn:
        checks += check_schemas_exist(conn)
        checks += check_tables_exist(conn)
        checks += check_columns_match_gold(conn, gold_dir)
        checks += check_row_counts(conn, gold_dir)
        checks += check_primary_keys(conn)

        fk_checks = check_foreign_key_orphans(conn)
        checks += fk_checks
        for c in fk_checks:
            orphan_rows.append({
                "check_id": c["check_id"], "table": c["table"],
                "records_evaluated": c["records_evaluated"], "orphan_count": c["records_failed"],
                "status": c["status"],
            })

        checks += check_date_keys_resolve(conn)
        checks += check_currency_reconciliation(conn, gold_dir)
        checks += check_readmission_counts_match(conn, gold_dir)
        checks += check_kpi_values_match(conn, reports_dir / "gold_kpi_summary.json")
        checks += check_views_execute(conn)
        checks += check_views_exclude_pii(conn)

    status_counts = {"pass": 0, "warning": 0, "fail": 0, "skipped": 0}
    for c in checks:
        status_counts[c["status"]] += 1

    report = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": {
            "total_checks": len(checks),
            "passed": status_counts["pass"],
            "warnings": status_counts["warning"],
            "failed": status_counts["fail"],
            "skipped": status_counts["skipped"],
        },
        "checks": checks,
    }
    return report, orphan_rows


def write_validation_report_json(report: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
        fh.write("\n")


def write_orphan_summary_csv(orphan_rows: list[dict], output_path: Path) -> None:
    import csv as csv_module

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["check_id", "table", "records_evaluated", "orphan_count", "status"]
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv_module.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in orphan_rows:
            writer.writerow(row)
