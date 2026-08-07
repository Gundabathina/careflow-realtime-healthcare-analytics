"""Warehouse schema, index, and view management, plus the table registry.

``WAREHOUSE_TABLES`` is the single source of truth for which
schema/table names are ever allowed to appear in dynamically-built SQL
(used to gate ``postgres_client.validate_identifier`` calls throughout
this package) and drives Gold-to-Postgres load ordering.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from careflow.config import get_project_root
from careflow.logging_config import get_logger

logger = get_logger(__name__)

SCHEMA_SQL_RELATIVE_PATH = "config/postgres_schema.sql"
INDEXES_SQL_RELATIVE_PATH = "config/postgres_indexes.sql"
VIEWS_SQL_RELATIVE_PATH = "config/postgres_views.sql"

WAREHOUSE_SCHEMAS: tuple[str, ...] = (
    "careflow_meta", "careflow_dim", "careflow_fact", "careflow_mart", "careflow_audit",
)

WAREHOUSE_SCHEMA_VERSION = "1.0.0"
LOADER_VERSION = "1.0.0"


@dataclass(frozen=True)
class WarehouseTableSpec:
    """One Gold table's mapping into the warehouse: schema, kind, and primary key."""

    schema_name: str
    table_name: str
    kind: str  # "dimension" | "fact" | "mart"
    primary_key: tuple[str, ...]
    gold_source_file: str


WAREHOUSE_TABLES: dict[str, WarehouseTableSpec] = {
    "dim_patient": WarehouseTableSpec("careflow_dim", "dim_patient", "dimension", ("patient_key",), "dim_patient.parquet"),
    "dim_provider": WarehouseTableSpec("careflow_dim", "dim_provider", "dimension", ("provider_key",), "dim_provider.parquet"),
    "dim_organization": WarehouseTableSpec("careflow_dim", "dim_organization", "dimension", ("organization_key",), "dim_organization.parquet"),
    "dim_payer": WarehouseTableSpec("careflow_dim", "dim_payer", "dimension", ("payer_key",), "dim_payer.parquet"),
    "dim_date": WarehouseTableSpec("careflow_dim", "dim_date", "dimension", ("date_key",), "dim_date.parquet"),
    "dim_condition": WarehouseTableSpec("careflow_dim", "dim_condition", "dimension", ("condition_key",), "dim_condition.parquet"),
    "dim_procedure": WarehouseTableSpec("careflow_dim", "dim_procedure", "dimension", ("procedure_key",), "dim_procedure.parquet"),
    "dim_medication": WarehouseTableSpec("careflow_dim", "dim_medication", "dimension", ("medication_key",), "dim_medication.parquet"),
    "fact_encounter": WarehouseTableSpec("careflow_fact", "fact_encounter", "fact", ("encounter_key",), "fact_encounter.parquet"),
    "fact_condition": WarehouseTableSpec("careflow_fact", "fact_condition", "fact", ("condition_event_key",), "fact_condition.parquet"),
    "fact_procedure": WarehouseTableSpec("careflow_fact", "fact_procedure", "fact", ("procedure_event_key",), "fact_procedure.parquet"),
    "fact_medication": WarehouseTableSpec("careflow_fact", "fact_medication", "fact", ("medication_event_key",), "fact_medication.parquet"),
    "fact_observation": WarehouseTableSpec("careflow_fact", "fact_observation", "fact", ("observation_key",), "fact_observation.parquet"),
    "fact_claim": WarehouseTableSpec("careflow_fact", "fact_claim", "fact", ("claim_key",), "fact_claim.parquet"),
    "fact_immunization": WarehouseTableSpec("careflow_fact", "fact_immunization", "fact", ("immunization_key",), "fact_immunization.parquet"),
    "fact_imaging_study": WarehouseTableSpec("careflow_fact", "fact_imaging_study", "fact", ("imaging_study_key",), "fact_imaging_study.parquet"),
    "mart_patient_360": WarehouseTableSpec("careflow_mart", "mart_patient_360", "mart", ("patient_key",), "mart_patient_360.parquet"),
    "mart_readmission": WarehouseTableSpec("careflow_mart", "mart_readmission", "mart", ("index_encounter_key",), "mart_readmission.parquet"),
    "mart_hospital_operations": WarehouseTableSpec(
        "careflow_mart", "mart_hospital_operations", "mart",
        ("organization_key", "encounter_date_key", "encounter_class"), "mart_hospital_operations.parquet",
    ),
    "mart_financial_performance": WarehouseTableSpec(
        "careflow_mart", "mart_financial_performance", "mart",
        ("payer_key", "organization_key", "year_month"), "mart_financial_performance.parquet",
    ),
    "mart_provider_utilization": WarehouseTableSpec(
        "careflow_mart", "mart_provider_utilization", "mart", ("provider_key", "year_month"), "mart_provider_utilization.parquet",
    ),
    "mart_monthly_kpis": WarehouseTableSpec("careflow_mart", "mart_monthly_kpis", "mart", ("year_month",), "mart_monthly_kpis.parquet"),
}

DIMENSION_LOAD_ORDER: tuple[str, ...] = tuple(k for k, v in WAREHOUSE_TABLES.items() if v.kind == "dimension")
FACT_LOAD_ORDER: tuple[str, ...] = tuple(k for k, v in WAREHOUSE_TABLES.items() if v.kind == "fact")
MART_LOAD_ORDER: tuple[str, ...] = tuple(k for k, v in WAREHOUSE_TABLES.items() if v.kind == "mart")
WAREHOUSE_LOAD_ORDER: tuple[str, ...] = DIMENSION_LOAD_ORDER + FACT_LOAD_ORDER + MART_LOAD_ORDER

ALLOWED_TABLE_NAMES: set[str] = set(WAREHOUSE_TABLES.keys())
ALLOWED_SCHEMA_NAMES: set[str] = set(WAREHOUSE_SCHEMAS)


def _read_sql(relative_path: str) -> str:
    path = get_project_root() / relative_path
    return path.read_text(encoding="utf-8")


def apply_sql_file(conn: psycopg.Connection, relative_path: str) -> None:
    sql = _read_sql(relative_path)
    with conn.cursor() as cur:
        cur.execute(sql)


def ensure_schema(conn: psycopg.Connection, apply_indexes: bool = True, apply_views: bool = True) -> None:
    """Create schemas, metadata/audit/dim/fact/mart tables, indexes, and views if missing."""
    apply_sql_file(conn, SCHEMA_SQL_RELATIVE_PATH)
    if apply_indexes:
        apply_sql_file(conn, INDEXES_SQL_RELATIVE_PATH)
    if apply_views:
        apply_sql_file(conn, VIEWS_SQL_RELATIVE_PATH)
    sync_table_registry(conn)
    record_schema_version(conn)
    conn.commit()


def record_schema_version(conn: psycopg.Connection, description: str = "CareFlow Gold warehouse schema") -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO careflow_meta.schema_version (schema_version, applied_at_utc, description)
            VALUES (%s, %s, %s)
            ON CONFLICT (schema_version) DO NOTHING
            """,
            (WAREHOUSE_SCHEMA_VERSION, datetime.now(timezone.utc), description),
        )


def sync_table_registry(conn: psycopg.Connection) -> None:
    """Mirror WAREHOUSE_TABLES into careflow_meta.table_registry for introspection."""
    with conn.cursor() as cur:
        for spec in WAREHOUSE_TABLES.values():
            cur.execute(
                """
                INSERT INTO careflow_meta.table_registry
                    (schema_name, table_name, table_kind, primary_key, gold_source_file)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (schema_name, table_name) DO UPDATE SET
                    table_kind = EXCLUDED.table_kind,
                    primary_key = EXCLUDED.primary_key,
                    gold_source_file = EXCLUDED.gold_source_file
                """,
                (spec.schema_name, spec.table_name, spec.kind, ",".join(spec.primary_key), spec.gold_source_file),
            )
