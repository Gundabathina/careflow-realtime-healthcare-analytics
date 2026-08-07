"""Loads existing pipeline quality/run reports for the Data Quality page.

Reads only the JSON/CSV report artifacts each phase already writes
(reports/profiling, reports/warehouse, reports/dbt, reports/airflow,
plus the Bronze/Silver/Gold manifests) -- never re-runs a check, never
touches data/raw, data/bronze, data/silver, or Gold Parquet files
itself.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dashboard.config import get_project_root

REPORT_PATHS = {
    "bronze_manifest": "data/bronze/bronze_manifest.json",
    "silver_manifest": "data/silver/silver_manifest.json",
    "gold_manifest": "data/gold/gold_manifest.json",
    "silver_quality": "reports/profiling/silver_quality_report.json",
    "gold_quality": "reports/profiling/gold_quality_report.json",
    "raw_data_quality": "reports/profiling/data_quality_report.json",
    "postgres_validation": "reports/warehouse/postgres_validation_report.json",
    "dbt_test_summary": "reports/dbt/dbt_test_summary.json",
    "dbt_run_summary": "reports/dbt/dbt_run_summary.json",
    "airflow_run_summary": "reports/airflow/airflow_run_summary.json",
}


def _load_json(relative_path: str) -> dict[str, Any] | None:
    path = Path(get_project_root()) / relative_path
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None


def _stage_summary(name: str, report: dict[str, Any] | None, summary_key: str = "summary") -> dict[str, Any]:
    if report is None:
        return {"stage": name, "available": False}
    summary = report.get(summary_key, {})
    return {
        "stage": name,
        "available": True,
        "generated_at_utc": (
            report.get("generated_at_utc")
            or report.get("completed_at_utc")
            or report.get("ingested_at_utc")
            or report.get("started_at_utc")
        ),
        **summary,
    }


def load_pipeline_reports() -> dict[str, Any]:
    """A compact, dashboard-ready summary of every pipeline stage's latest report."""
    raw = {key: _load_json(path) for key, path in REPORT_PATHS.items()}

    stages = [
        _stage_summary("Bronze Ingestion", raw["bronze_manifest"]),
        _stage_summary("Silver Transformation", raw["silver_manifest"]),
        _stage_summary("Silver Data Quality", raw["silver_quality"]),
        _stage_summary("Gold Transformation", raw["gold_manifest"]),
        _stage_summary("Gold Data Quality", raw["gold_quality"]),
        _stage_summary("PostgreSQL Warehouse Validation", raw["postgres_validation"], summary_key="summary"),
    ]

    dbt_summary = raw["dbt_test_summary"] or {}
    stages.append({
        "stage": "dbt Tests",
        "available": raw["dbt_test_summary"] is not None,
        "generated_at_utc": dbt_summary.get("generated_at_utc"),
        "passed": dbt_summary.get("pass"),
        "warnings": dbt_summary.get("warn"),
        "failed": dbt_summary.get("fail"),
        "skipped": dbt_summary.get("skipped"),
        "total_checks": dbt_summary.get("total_tests"),
    })

    airflow_summary = raw["airflow_run_summary"] or {}
    stages.append({
        "stage": "Airflow Orchestration",
        "available": raw["airflow_run_summary"] is not None,
        "dag_id": airflow_summary.get("dag_id"),
        "run_id": airflow_summary.get("run_id"),
        "final_status": airflow_summary.get("final_status"),
        "started_at_utc": airflow_summary.get("started_at_utc"),
        "completed_at_utc": airflow_summary.get("completed_at_utc"),
    })

    last_run_timestamps = [
        s.get("generated_at_utc") or s.get("completed_at_utc")
        for s in stages
        if s.get("available") and (s.get("generated_at_utc") or s.get("completed_at_utc"))
    ]
    last_pipeline_run = max(last_run_timestamps) if last_run_timestamps else None

    successful_stage_runs = [
        s.get("generated_at_utc") or s.get("completed_at_utc")
        for s in stages
        if s.get("available") and s.get("failed", 0) in (0, None) and (s.get("generated_at_utc") or s.get("completed_at_utc"))
    ]
    last_successful_run = max(successful_stage_runs) if successful_stage_runs else None

    return {
        "stages": stages,
        "last_pipeline_run": last_pipeline_run,
        "last_successful_pipeline_run": last_successful_run,
    }
