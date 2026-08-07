"""Centralized, parameterized analytical queries for the CareFlow dashboard.

Every function here builds SQL with placeholders (``%s``) and passes
user-influenced values as query parameters -- never string-interpolated
into the SQL text. The only thing ever interpolated directly into a
query string is a column/table name, and only from a fixed, hard-coded
allow-list defined in this file (e.g. READMISSION_WINDOW_COLUMNS) --
never a raw value coming from a filter widget.

All aggregation happens in PostgreSQL (GROUP BY, window functions,
filtered joins) -- pages receive already-aggregated, chart-ready rows,
never a raw fact table pulled into pandas in full.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from dashboard.config import MART_SCHEMA
from dashboard.database import run_filter_query, run_query

M = MART_SCHEMA  # short alias used throughout this file's f-strings

# Only these three columns may ever be selected as "the" readmission
# flag -- resolved from a fixed dict, never from a raw filter value.
READMISSION_WINDOW_COLUMNS: dict[int, str] = {
    7: "readmitted_within_7_days",
    14: "readmitted_within_14_days",
    30: "readmitted_within_30_days",
}


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


@dataclass
class Filters:
    """The global sidebar filter selection. Every field is optional --
    an unset field simply omits that WHERE condition."""

    start_date: Any = None
    end_date: Any = None
    year: int | None = None
    month: int | None = None
    organization: str | None = None
    provider: str | None = None
    payer: str | None = None
    encounter_class: str | None = None
    age_group: str | None = None
    gender: str | None = None
    race: str | None = None
    readmission_window: int = 30

    def is_empty(self) -> bool:
        return not any(
            [
                self.start_date, self.end_date, self.year, self.month,
                self.organization, self.provider, self.payer,
                self.encounter_class, self.age_group, self.race,
                self.gender,
            ]
        )


def _cond(sql_fragment: str, value: Any) -> tuple[str, Any] | None:
    """None/""/"All" means "no filter" -- everything else becomes a parameterized condition."""
    if value in (None, "", "All"):
        return None
    return sql_fragment, value


def _where(conditions: list[tuple[str, Any] | None]) -> tuple[str, list]:
    active = [c for c in conditions if c is not None]
    if not active:
        return "", []
    return " WHERE " + " AND ".join(c[0] for c in active), [c[1] for c in active]


def _encounter_conditions(f: Filters, encounter_alias: str = "fe") -> list[tuple[str, Any] | None]:
    """Conditions applicable to any query rooted at fct_encounters (aliased ``encounter_alias``)."""
    return [
        _cond(f"{encounter_alias}.encounter_date >= %s", f.start_date),
        _cond(f"{encounter_alias}.encounter_date <= %s", f.end_date),
        _cond(f"{encounter_alias}.year_month LIKE %s", f"{f.year}-%" if f.year else None),
        _cond(f"{encounter_alias}.year_month = %s", f"{f.year}-{int(f.month):02d}" if (f.year and f.month) else None),
        _cond(f"{encounter_alias}.encounter_class = %s", f.encounter_class),
    ]


def _patient_conditions(f: Filters, patient_alias: str = "p") -> list[tuple[str, Any] | None]:
    return [
        _cond(f"{patient_alias}.age_group = %s", f.age_group),
        _cond(f"{patient_alias}.gender = %s", f.gender),
        _cond(f"{patient_alias}.race = %s", f.race),
    ]


def _empty_df(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


# ---------------------------------------------------------------------------
# Filter option lists (sidebar dropdowns)
# ---------------------------------------------------------------------------


def get_filter_options() -> dict[str, list]:
    """Distinct values for every sidebar filter, straight from the mart tables."""
    organizations = run_filter_query(
        f"SELECT DISTINCT organization_name FROM {M}.dim_organization_reporting "
        "WHERE organization_name IS NOT NULL ORDER BY organization_name"
    )
    providers = run_filter_query(
        f"SELECT DISTINCT provider_name FROM {M}.dim_provider_reporting "
        "WHERE provider_name IS NOT NULL ORDER BY provider_name"
    )
    payers = run_filter_query(
        f"SELECT DISTINCT payer_name FROM {M}.dim_payer_reporting "
        "WHERE payer_name IS NOT NULL ORDER BY payer_name"
    )
    encounter_classes = run_filter_query(
        f"SELECT DISTINCT encounter_class FROM {M}.fct_encounters "
        "WHERE encounter_class IS NOT NULL ORDER BY encounter_class"
    )
    age_groups = run_filter_query(
        f"SELECT DISTINCT age_group FROM {M}.dim_patient_safe "
        "WHERE age_group IS NOT NULL ORDER BY age_group"
    )
    genders = run_filter_query(
        f"SELECT DISTINCT gender FROM {M}.dim_patient_safe WHERE gender IS NOT NULL ORDER BY gender"
    )
    races = run_filter_query(
        f"SELECT DISTINCT race FROM {M}.dim_patient_safe WHERE race IS NOT NULL ORDER BY race"
    )
    years = run_filter_query(
        f"SELECT DISTINCT LEFT(year_month, 4) AS year FROM {M}.fct_encounters "
        "WHERE year_month IS NOT NULL ORDER BY year"
    )
    return {
        "organizations": organizations["organization_name"].tolist() if not organizations.empty else [],
        "providers": providers["provider_name"].tolist() if not providers.empty else [],
        "payers": payers["payer_name"].tolist() if not payers.empty else [],
        "encounter_classes": encounter_classes["encounter_class"].tolist() if not encounter_classes.empty else [],
        "age_groups": age_groups["age_group"].tolist() if not age_groups.empty else [],
        "genders": genders["gender"].tolist() if not genders.empty else [],
        "races": races["race"].tolist() if not races.empty else [],
        "years": years["year"].tolist() if not years.empty else [],
    }


# ---------------------------------------------------------------------------
# Page 1: Executive Overview
# ---------------------------------------------------------------------------


def get_executive_kpis(f: Filters) -> dict[str, Any]:
    conditions = _encounter_conditions(f) + [
        _cond("o.organization_name = %s", f.organization),
        _cond("prov.provider_name = %s", f.provider),
        _cond("pay.payer_name = %s", f.payer),
    ]
    where_sql, params = _where(conditions)
    sql = f"""
        SELECT
            COUNT(DISTINCT fe.patient_key) AS patients_served,
            COUNT(*) AS total_encounters,
            COUNT(*) FILTER (WHERE fe.is_inpatient) AS inpatient_encounters,
            COUNT(*) FILTER (WHERE fe.is_emergency) AS emergency_encounters,
            AVG(fe.encounter_duration_minutes) AS avg_length_of_stay_minutes,
            SUM(fe.total_claim_cost) AS total_claim_cost,
            SUM(fe.payer_coverage) AS total_payer_coverage,
            AVG(fe.patient_responsibility) FILTER (WHERE NOT fe.patient_responsibility_is_negative) AS avg_patient_responsibility
        FROM {M}.fct_encounters fe
        LEFT JOIN {M}.dim_organization_reporting o ON fe.organization_key = o.organization_key
        LEFT JOIN {M}.dim_provider_reporting prov ON fe.provider_key = prov.provider_key
        LEFT JOIN {M}.dim_payer_reporting pay ON fe.payer_key = pay.payer_key
        {where_sql}
    """
    df = run_query(sql, tuple(params))
    if df.empty:
        return {}
    row = df.iloc[0].to_dict()

    readmission_sql = f"""
        SELECT
            COUNT(*) AS qualifying_encounters,
            COUNT(*) FILTER (WHERE readmitted_within_30_days) AS readmissions_30_day
        FROM {M}.fct_readmissions
    """
    readmit_df = run_query(readmission_sql)
    qualifying = int(readmit_df.iloc[0]["qualifying_encounters"]) if not readmit_df.empty else 0
    readmitted = int(readmit_df.iloc[0]["readmissions_30_day"]) if not readmit_df.empty else 0
    row["readmission_rate_30_day"] = (readmitted / qualifying) if qualifying else None

    total_coverage = row.get("total_payer_coverage") or 0
    total_cost = row.get("total_claim_cost") or 0
    row["payer_coverage_ratio"] = (float(total_coverage) / float(total_cost)) if total_cost else None
    return row


def get_encounter_trend(f: Filters) -> pd.DataFrame:
    conditions = [
        _cond("year_month >= %s", f.start_date.strftime("%Y-%m") if f.start_date else None),
        _cond("year_month <= %s", f.end_date.strftime("%Y-%m") if f.end_date else None),
        _cond("LEFT(year_month, 4) = %s", str(f.year) if f.year else None),
    ]
    where_sql, params = _where(conditions)
    sql = f"SELECT year_month, total_encounters, total_patients_served FROM {M}.mart_executive_monthly {where_sql} ORDER BY year_month"
    return run_query(sql, tuple(params))


def get_encounter_class_distribution(f: Filters) -> pd.DataFrame:
    conditions = _encounter_conditions(f) + [
        _cond("o.organization_name = %s", f.organization),
        _cond("pay.payer_name = %s", f.payer),
    ]
    where_sql, params = _where(conditions)
    sql = f"""
        SELECT fe.encounter_class, COUNT(*) AS encounter_count
        FROM {M}.fct_encounters fe
        LEFT JOIN {M}.dim_organization_reporting o ON fe.organization_key = o.organization_key
        LEFT JOIN {M}.dim_payer_reporting pay ON fe.payer_key = pay.payer_key
        {where_sql}
        GROUP BY fe.encounter_class
        ORDER BY encounter_count DESC
    """
    return run_query(sql, tuple(params))


def get_monthly_readmission_trend(f: Filters) -> pd.DataFrame:
    conditions = [
        _cond("year_month >= %s", f.start_date.strftime("%Y-%m") if f.start_date else None),
        _cond("year_month <= %s", f.end_date.strftime("%Y-%m") if f.end_date else None),
        _cond("LEFT(year_month, 4) = %s", str(f.year) if f.year else None),
    ]
    where_sql, params = _where(conditions)
    sql = f"SELECT year_month, readmission_rate_30_day, readmission_count_30_day FROM {M}.mart_executive_monthly {where_sql} ORDER BY year_month"
    return run_query(sql, tuple(params))


def get_monthly_cost_trend(f: Filters) -> pd.DataFrame:
    conditions = _encounter_conditions(f) + [
        _cond("o.organization_name = %s", f.organization),
        _cond("pay.payer_name = %s", f.payer),
    ]
    where_sql, params = _where(conditions)
    sql = f"""
        SELECT fe.year_month,
               SUM(fe.total_claim_cost) AS total_claim_cost,
               SUM(fe.payer_coverage) AS total_payer_coverage
        FROM {M}.fct_encounters fe
        LEFT JOIN {M}.dim_organization_reporting o ON fe.organization_key = o.organization_key
        LEFT JOIN {M}.dim_payer_reporting pay ON fe.payer_key = pay.payer_key
        {where_sql}
        GROUP BY fe.year_month
        ORDER BY fe.year_month
    """
    return run_query(sql, tuple(params))


def get_top_organizations_by_encounters(f: Filters, limit: int = 10) -> pd.DataFrame:
    conditions = _encounter_conditions(f)
    where_sql, params = _where(conditions)
    sql = f"""
        SELECT o.organization_name, COUNT(*) AS encounter_count
        FROM {M}.fct_encounters fe
        JOIN {M}.dim_organization_reporting o ON fe.organization_key = o.organization_key
        {where_sql}
        GROUP BY o.organization_name
        ORDER BY encounter_count DESC
        LIMIT %s
    """
    return run_query(sql, tuple(params) + (limit,))


def get_age_group_distribution(f: Filters) -> pd.DataFrame:
    conditions = [
        _cond("gender = %s", f.gender),
        _cond("race = %s", f.race),
    ]
    where_sql, params = _where(conditions)
    sql = f"""
        SELECT age_group, COUNT(*) AS patient_count
        FROM {M}.dim_patient_safe
        {where_sql}
        GROUP BY age_group
        ORDER BY age_group
    """
    return run_query(sql, tuple(params))


def get_payer_coverage_breakdown(f: Filters) -> pd.DataFrame:
    conditions = _encounter_conditions(f) + [_cond("o.organization_name = %s", f.organization)]
    where_sql, params = _where(conditions)
    sql = f"""
        SELECT pay.payer_name,
               SUM(fe.payer_coverage) AS total_payer_coverage,
               SUM(fe.total_claim_cost) AS total_claim_cost
        FROM {M}.fct_encounters fe
        JOIN {M}.dim_payer_reporting pay ON fe.payer_key = pay.payer_key
        LEFT JOIN {M}.dim_organization_reporting o ON fe.organization_key = o.organization_key
        {where_sql}
        GROUP BY pay.payer_name
        ORDER BY total_payer_coverage DESC
    """
    return run_query(sql, tuple(params))


# ---------------------------------------------------------------------------
# Page 2: Readmission Analytics
# ---------------------------------------------------------------------------


def get_readmission_kpis(f: Filters) -> dict[str, Any]:
    window_col = READMISSION_WINDOW_COLUMNS.get(f.readmission_window, "readmitted_within_30_days")
    sql = f"""
        SELECT
            COUNT(*) AS qualifying_index_encounters,
            COUNT(*) FILTER (WHERE readmitted_within_7_days) AS readmitted_7,
            COUNT(*) FILTER (WHERE readmitted_within_14_days) AS readmitted_14,
            COUNT(*) FILTER (WHERE readmitted_within_30_days) AS readmitted_30,
            AVG(days_to_readmission) FILTER (WHERE {window_col}) AS avg_days_to_readmission
        FROM {M}.fct_readmissions
    """
    df = run_query(sql)
    if df.empty:
        return {}
    row = df.iloc[0].to_dict()
    total = row.get("qualifying_index_encounters") or 0
    row["rate_7_day"] = (row["readmitted_7"] / total) if total else None
    row["rate_14_day"] = (row["readmitted_14"] / total) if total else None
    row["rate_30_day"] = (row["readmitted_30"] / total) if total else None
    return row


def get_readmission_trend(f: Filters) -> pd.DataFrame:
    conditions = [
        _cond("year_month >= %s", f.start_date.strftime("%Y-%m") if f.start_date else None),
        _cond("year_month <= %s", f.end_date.strftime("%Y-%m") if f.end_date else None),
    ]
    where_sql, params = _where(conditions)
    sql = f"SELECT year_month, readmission_rate_30_day FROM {M}.mart_executive_monthly {where_sql} ORDER BY year_month"
    return run_query(sql, tuple(params))


def get_readmissions_by_encounter_class(f: Filters) -> pd.DataFrame:
    window_col = READMISSION_WINDOW_COLUMNS.get(f.readmission_window, "readmitted_within_30_days")
    sql = f"""
        SELECT index_encounter_class,
               COUNT(*) AS qualifying_encounters,
               COUNT(*) FILTER (WHERE {window_col}) AS readmissions,
               ROUND(100.0 * COUNT(*) FILTER (WHERE {window_col}) / NULLIF(COUNT(*), 0), 1) AS readmission_rate_pct
        FROM {M}.fct_readmissions
        GROUP BY index_encounter_class
        ORDER BY readmission_rate_pct DESC NULLS LAST
    """
    return run_query(sql)


def get_readmissions_by_age_group(f: Filters) -> pd.DataFrame:
    window_col = READMISSION_WINDOW_COLUMNS.get(f.readmission_window, "readmitted_within_30_days")
    conditions = [_cond("gender = %s", f.gender)]
    where_sql, params = _where(conditions)
    sql = f"""
        SELECT age_group,
               COUNT(*) AS qualifying_encounters,
               COUNT(*) FILTER (WHERE {window_col}) AS readmissions,
               ROUND(100.0 * COUNT(*) FILTER (WHERE {window_col}) / NULLIF(COUNT(*), 0), 1) AS readmission_rate_pct
        FROM {M}.mart_readmission_analysis
        {where_sql}
        GROUP BY age_group
        ORDER BY age_group
    """
    return run_query(sql, tuple(params))


def get_readmissions_by_gender(f: Filters) -> pd.DataFrame:
    window_col = READMISSION_WINDOW_COLUMNS.get(f.readmission_window, "readmitted_within_30_days")
    conditions = [_cond("age_group = %s", f.age_group)]
    where_sql, params = _where(conditions)
    sql = f"""
        SELECT gender,
               COUNT(*) AS qualifying_encounters,
               COUNT(*) FILTER (WHERE {window_col}) AS readmissions,
               ROUND(100.0 * COUNT(*) FILTER (WHERE {window_col}) / NULLIF(COUNT(*), 0), 1) AS readmission_rate_pct
        FROM {M}.mart_readmission_analysis
        {where_sql}
        GROUP BY gender
        ORDER BY gender
    """
    return run_query(sql, tuple(params))


def get_readmissions_by_organization(f: Filters, limit: int = 10) -> pd.DataFrame:
    window_col = READMISSION_WINDOW_COLUMNS.get(f.readmission_window, "readmitted_within_30_days")
    sql = f"""
        SELECT o.organization_name,
               COUNT(*) AS qualifying_encounters,
               COUNT(*) FILTER (WHERE r.{window_col}) AS readmissions,
               ROUND(100.0 * COUNT(*) FILTER (WHERE r.{window_col}) / NULLIF(COUNT(*), 0), 1) AS readmission_rate_pct
        FROM {M}.fct_readmissions r
        JOIN {M}.fct_encounters fe ON r.index_encounter_key = fe.encounter_key
        JOIN {M}.dim_organization_reporting o ON fe.organization_key = o.organization_key
        GROUP BY o.organization_name
        HAVING COUNT(*) >= 5
        ORDER BY readmission_rate_pct DESC NULLS LAST
        LIMIT %s
    """
    return run_query(sql, (limit,))


def get_days_to_readmission_distribution(f: Filters) -> pd.DataFrame:
    window_col = READMISSION_WINDOW_COLUMNS.get(f.readmission_window, "readmitted_within_30_days")
    sql = f"""
        SELECT days_to_readmission
        FROM {M}.fct_readmissions
        WHERE {window_col} AND days_to_readmission IS NOT NULL
    """
    return run_query(sql)


def get_high_readmission_segments(f: Filters, min_qualifying: int = 5, limit: int = 15) -> pd.DataFrame:
    """Aggregated (never individual-patient) high-readmission segments by age group x gender."""
    window_col = READMISSION_WINDOW_COLUMNS.get(f.readmission_window, "readmitted_within_30_days")
    sql = f"""
        SELECT age_group, gender,
               COUNT(*) AS qualifying_encounters,
               COUNT(*) FILTER (WHERE {window_col}) AS readmissions,
               ROUND(100.0 * COUNT(*) FILTER (WHERE {window_col}) / NULLIF(COUNT(*), 0), 1) AS readmission_rate_pct
        FROM {M}.mart_readmission_analysis
        GROUP BY age_group, gender
        HAVING COUNT(*) >= %s
        ORDER BY readmission_rate_pct DESC NULLS LAST
        LIMIT %s
    """
    return run_query(sql, (min_qualifying, limit))


# ---------------------------------------------------------------------------
# Page 3: Hospital Operations
# ---------------------------------------------------------------------------


def get_operations_kpis(f: Filters) -> dict[str, Any]:
    conditions = _encounter_conditions(f) + [_cond("o.organization_name = %s", f.organization)]
    where_sql, params = _where(conditions)
    sql = f"""
        SELECT
            COUNT(*) AS total_encounters,
            COUNT(DISTINCT fe.patient_key) AS unique_patients,
            AVG(fe.encounter_duration_minutes) AS avg_duration_minutes,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY fe.encounter_duration_minutes) AS median_duration_minutes,
            ROUND(100.0 * COUNT(*) FILTER (WHERE fe.is_emergency) / NULLIF(COUNT(*), 0), 1) AS emergency_pct,
            ROUND(100.0 * COUNT(*) FILTER (WHERE fe.is_inpatient) / NULLIF(COUNT(*), 0), 1) AS inpatient_pct,
            COUNT(DISTINCT fe.provider_key) AS providers_active
        FROM {M}.fct_encounters fe
        LEFT JOIN {M}.dim_organization_reporting o ON fe.organization_key = o.organization_key
        {where_sql}
    """
    df = run_query(sql, tuple(params))
    return df.iloc[0].to_dict() if not df.empty else {}


def get_encounter_volume_by_month(f: Filters) -> pd.DataFrame:
    conditions = _encounter_conditions(f) + [_cond("o.organization_name = %s", f.organization)]
    where_sql, params = _where(conditions)
    sql = f"""
        SELECT fe.year_month, COUNT(*) AS encounter_count
        FROM {M}.fct_encounters fe
        LEFT JOIN {M}.dim_organization_reporting o ON fe.organization_key = o.organization_key
        {where_sql}
        GROUP BY fe.year_month
        ORDER BY fe.year_month
    """
    return run_query(sql, tuple(params))


def get_encounter_volume_by_organization(f: Filters, limit: int = 15) -> pd.DataFrame:
    conditions = _encounter_conditions(f)
    where_sql, params = _where(conditions)
    sql = f"""
        SELECT o.organization_name, COUNT(*) AS encounter_count
        FROM {M}.fct_encounters fe
        JOIN {M}.dim_organization_reporting o ON fe.organization_key = o.organization_key
        {where_sql}
        GROUP BY o.organization_name
        ORDER BY encounter_count DESC
        LIMIT %s
    """
    return run_query(sql, tuple(params) + (limit,))


def get_avg_duration_by_organization(f: Filters, limit: int = 15) -> pd.DataFrame:
    conditions = _encounter_conditions(f)
    where_sql, params = _where(conditions)
    sql = f"""
        SELECT o.organization_name, AVG(fe.encounter_duration_minutes) AS avg_duration_minutes
        FROM {M}.fct_encounters fe
        JOIN {M}.dim_organization_reporting o ON fe.organization_key = o.organization_key
        {where_sql}
        GROUP BY o.organization_name
        ORDER BY avg_duration_minutes DESC
        LIMIT %s
    """
    return run_query(sql, tuple(params) + (limit,))


def get_emergency_utilization_trend(f: Filters) -> pd.DataFrame:
    conditions = _encounter_conditions(f) + [_cond("o.organization_name = %s", f.organization)]
    where_sql, params = _where(conditions)
    sql = f"""
        SELECT fe.year_month,
               ROUND(100.0 * COUNT(*) FILTER (WHERE fe.is_emergency) / NULLIF(COUNT(*), 0), 1) AS emergency_pct
        FROM {M}.fct_encounters fe
        LEFT JOIN {M}.dim_organization_reporting o ON fe.organization_key = o.organization_key
        {where_sql}
        GROUP BY fe.year_month
        ORDER BY fe.year_month
    """
    return run_query(sql, tuple(params))


def get_inpatient_utilization_trend(f: Filters) -> pd.DataFrame:
    conditions = _encounter_conditions(f) + [_cond("o.organization_name = %s", f.organization)]
    where_sql, params = _where(conditions)
    sql = f"""
        SELECT fe.year_month,
               ROUND(100.0 * COUNT(*) FILTER (WHERE fe.is_inpatient) / NULLIF(COUNT(*), 0), 1) AS inpatient_pct
        FROM {M}.fct_encounters fe
        LEFT JOIN {M}.dim_organization_reporting o ON fe.organization_key = o.organization_key
        {where_sql}
        GROUP BY fe.year_month
        ORDER BY fe.year_month
    """
    return run_query(sql, tuple(params))


def get_organization_encounter_class_heatmap(f: Filters) -> pd.DataFrame:
    conditions = _encounter_conditions(f)
    where_sql, params = _where(conditions)
    sql = f"""
        SELECT o.organization_name, fe.encounter_class, COUNT(*) AS encounter_count
        FROM {M}.fct_encounters fe
        JOIN {M}.dim_organization_reporting o ON fe.organization_key = o.organization_key
        {where_sql}
        GROUP BY o.organization_name, fe.encounter_class
    """
    return run_query(sql, tuple(params))


def get_organization_comparison_table(f: Filters) -> pd.DataFrame:
    conditions = _encounter_conditions(f)
    where_sql, params = _where(conditions)
    sql = f"""
        SELECT o.organization_name,
               COUNT(*) AS total_encounters,
               COUNT(DISTINCT fe.patient_key) AS unique_patients,
               ROUND(AVG(fe.encounter_duration_minutes)::numeric, 1) AS avg_duration_minutes,
               COUNT(*) FILTER (WHERE fe.is_emergency) AS emergency_encounters,
               COUNT(*) FILTER (WHERE fe.is_inpatient) AS inpatient_encounters,
               ROUND(SUM(fe.total_claim_cost)::numeric, 2) AS total_claim_cost
        FROM {M}.fct_encounters fe
        JOIN {M}.dim_organization_reporting o ON fe.organization_key = o.organization_key
        {where_sql}
        GROUP BY o.organization_name
        ORDER BY total_encounters DESC
    """
    return run_query(sql, tuple(params))


# ---------------------------------------------------------------------------
# Page 4: Financial Performance
# ---------------------------------------------------------------------------


def get_financial_kpis(f: Filters) -> dict[str, Any]:
    conditions = _encounter_conditions(f) + [
        _cond("o.organization_name = %s", f.organization),
        _cond("pay.payer_name = %s", f.payer),
    ]
    where_sql, params = _where(conditions)
    sql = f"""
        SELECT
            SUM(fe.total_claim_cost) AS total_claim_cost,
            SUM(fe.payer_coverage) AS total_payer_coverage,
            SUM(fe.patient_responsibility) FILTER (WHERE NOT fe.patient_responsibility_is_negative) AS total_patient_responsibility,
            AVG(fe.total_claim_cost) AS avg_cost_per_encounter,
            AVG(fe.patient_responsibility) FILTER (WHERE NOT fe.patient_responsibility_is_negative) AS avg_patient_responsibility
        FROM {M}.fct_encounters fe
        LEFT JOIN {M}.dim_organization_reporting o ON fe.organization_key = o.organization_key
        LEFT JOIN {M}.dim_payer_reporting pay ON fe.payer_key = pay.payer_key
        {where_sql}
    """
    df = run_query(sql, tuple(params))
    if df.empty:
        return {}
    row = df.iloc[0].to_dict()
    cost = row.get("total_claim_cost") or 0
    coverage = row.get("total_payer_coverage") or 0
    row["coverage_ratio"] = (float(coverage) / float(cost)) if cost else None
    return row


def get_monthly_claim_cost(f: Filters) -> pd.DataFrame:
    return get_monthly_cost_trend(f)


def get_payer_coverage_over_time(f: Filters) -> pd.DataFrame:
    conditions = _encounter_conditions(f) + [_cond("pay.payer_name = %s", f.payer)]
    where_sql, params = _where(conditions)
    sql = f"""
        SELECT fe.year_month, SUM(fe.payer_coverage) AS total_payer_coverage
        FROM {M}.fct_encounters fe
        LEFT JOIN {M}.dim_payer_reporting pay ON fe.payer_key = pay.payer_key
        {where_sql}
        GROUP BY fe.year_month
        ORDER BY fe.year_month
    """
    return run_query(sql, tuple(params))


def get_patient_responsibility_trend(f: Filters) -> pd.DataFrame:
    conditions = _encounter_conditions(f) + [_cond("o.organization_name = %s", f.organization)]
    where_sql, params = _where(conditions)
    # NOT patient_responsibility_is_negative is a fixed business rule
    # (excludes known-bad Synthea records flagged in Phase 3A), not a
    # user-controlled filter -- always applied, never parameterized.
    where_sql = f"{where_sql} AND NOT fe.patient_responsibility_is_negative" if where_sql else " WHERE NOT fe.patient_responsibility_is_negative"
    sql = f"""
        SELECT fe.year_month, SUM(fe.patient_responsibility) AS total_patient_responsibility
        FROM {M}.fct_encounters fe
        LEFT JOIN {M}.dim_organization_reporting o ON fe.organization_key = o.organization_key
        {where_sql}
        GROUP BY fe.year_month
        ORDER BY fe.year_month
    """
    return run_query(sql, tuple(params))


def get_cost_by_encounter_class(f: Filters) -> pd.DataFrame:
    conditions = _encounter_conditions(f)
    where_sql, params = _where(conditions)
    sql = f"""
        SELECT fe.encounter_class,
               SUM(fe.total_claim_cost) AS total_claim_cost,
               AVG(fe.total_claim_cost) AS avg_claim_cost
        FROM {M}.fct_encounters fe
        {where_sql}
        GROUP BY fe.encounter_class
        ORDER BY total_claim_cost DESC
    """
    return run_query(sql, tuple(params))


def get_cost_by_organization(f: Filters, limit: int = 15) -> pd.DataFrame:
    conditions = _encounter_conditions(f)
    where_sql, params = _where(conditions)
    sql = f"""
        SELECT o.organization_name, SUM(fe.total_claim_cost) AS total_claim_cost
        FROM {M}.fct_encounters fe
        JOIN {M}.dim_organization_reporting o ON fe.organization_key = o.organization_key
        {where_sql}
        GROUP BY o.organization_name
        ORDER BY total_claim_cost DESC
        LIMIT %s
    """
    return run_query(sql, tuple(params) + (limit,))


def get_coverage_ratio_by_payer(f: Filters) -> pd.DataFrame:
    conditions = _encounter_conditions(f)
    where_sql, params = _where(conditions)
    sql = f"""
        SELECT pay.payer_name,
               SUM(fe.payer_coverage) AS total_payer_coverage,
               SUM(fe.total_claim_cost) AS total_claim_cost,
               ROUND(100.0 * SUM(fe.payer_coverage) / NULLIF(SUM(fe.total_claim_cost), 0), 1) AS coverage_ratio_pct
        FROM {M}.fct_encounters fe
        JOIN {M}.dim_payer_reporting pay ON fe.payer_key = pay.payer_key
        {where_sql}
        GROUP BY pay.payer_name
        ORDER BY coverage_ratio_pct DESC NULLS LAST
    """
    return run_query(sql, tuple(params))


def get_top_payers_by_coverage(f: Filters, limit: int = 10) -> pd.DataFrame:
    conditions = _encounter_conditions(f)
    where_sql, params = _where(conditions)
    sql = f"""
        SELECT pay.payer_name, SUM(fe.payer_coverage) AS total_payer_coverage
        FROM {M}.fct_encounters fe
        JOIN {M}.dim_payer_reporting pay ON fe.payer_key = pay.payer_key
        {where_sql}
        GROUP BY pay.payer_name
        ORDER BY total_payer_coverage DESC
        LIMIT %s
    """
    return run_query(sql, tuple(params) + (limit,))


# ---------------------------------------------------------------------------
# Page 5: Provider Performance
# ---------------------------------------------------------------------------


def get_provider_kpis(f: Filters) -> dict[str, Any]:
    conditions = [_cond("year_month >= %s", f.start_date.strftime("%Y-%m") if f.start_date else None)]
    where_sql, params = _where(conditions)
    sql = f"""
        SELECT
            COUNT(DISTINCT provider_key) AS active_providers,
            SUM(encounter_count) AS total_provider_encounters,
            AVG(encounter_count) AS avg_encounters_per_provider,
            AVG(unique_patients) AS avg_patients_per_provider,
            AVG(average_encounter_duration_minutes) AS avg_encounter_duration
        FROM {M}.fct_provider_activity
        {where_sql}
    """
    df = run_query(sql, tuple(params))
    return df.iloc[0].to_dict() if not df.empty else {}


def get_top_providers_by_encounters(f: Filters, limit: int = 10) -> pd.DataFrame:
    sql = f"""
        SELECT provider_name, SUM(encounter_count) AS total_encounters
        FROM {M}.fct_provider_activity
        GROUP BY provider_name
        ORDER BY total_encounters DESC
        LIMIT %s
    """
    return run_query(sql, (limit,))


def get_top_providers_by_patients(f: Filters, limit: int = 10) -> pd.DataFrame:
    sql = f"""
        SELECT provider_name, SUM(unique_patients) AS total_unique_patients
        FROM {M}.fct_provider_activity
        GROUP BY provider_name
        ORDER BY total_unique_patients DESC
        LIMIT %s
    """
    return run_query(sql, (limit,))


def get_provider_utilization_over_time(f: Filters) -> pd.DataFrame:
    conditions = [_cond("provider_name = %s", f.provider)]
    where_sql, params = _where(conditions)
    sql = f"""
        SELECT year_month, SUM(encounter_count) AS total_encounters
        FROM {M}.fct_provider_activity
        {where_sql}
        GROUP BY year_month
        ORDER BY year_month
    """
    return run_query(sql, tuple(params))


def get_provider_speciality_distribution(f: Filters) -> pd.DataFrame:
    sql = f"""
        SELECT speciality, COUNT(DISTINCT provider_key) AS provider_count
        FROM {M}.dim_provider_reporting
        WHERE speciality IS NOT NULL
        GROUP BY speciality
        ORDER BY provider_count DESC
    """
    return run_query(sql)


def get_avg_duration_by_provider(f: Filters, limit: int = 15) -> pd.DataFrame:
    sql = f"""
        SELECT provider_name, AVG(average_encounter_duration_minutes) AS avg_duration_minutes
        FROM {M}.fct_provider_activity
        GROUP BY provider_name
        ORDER BY avg_duration_minutes DESC
        LIMIT %s
    """
    return run_query(sql, (limit,))


def get_cost_by_provider(f: Filters, limit: int = 15) -> pd.DataFrame:
    sql = f"""
        SELECT provider_name, SUM(total_claim_cost) AS total_claim_cost
        FROM {M}.fct_provider_activity
        GROUP BY provider_name
        ORDER BY total_claim_cost DESC
        LIMIT %s
    """
    return run_query(sql, (limit,))


def get_provider_ranking_table(f: Filters) -> pd.DataFrame:
    sql = f"""
        SELECT provider_name, speciality,
               SUM(encounter_count) AS total_encounters,
               SUM(unique_patients) AS total_unique_patients,
               ROUND(AVG(average_encounter_duration_minutes)::numeric, 1) AS avg_encounter_duration_minutes,
               ROUND(SUM(total_claim_cost)::numeric, 2) AS total_claim_cost
        FROM {M}.fct_provider_activity
        GROUP BY provider_name, speciality
        ORDER BY total_encounters DESC
    """
    return run_query(sql)


# ---------------------------------------------------------------------------
# Page 6: Patient Population
# ---------------------------------------------------------------------------


def get_patient_population_kpis(f: Filters) -> dict[str, Any]:
    conditions = [
        _cond("age_group = %s", f.age_group),
        _cond("gender = %s", f.gender),
        _cond("race = %s", f.race),
    ]
    where_sql, params = _where(conditions)
    sql = f"""
        SELECT age_group, COUNT(*) AS patient_count,
               AVG(distinct_encounter_count) AS avg_encounters_per_patient,
               AVG(condition_count) AS avg_conditions_per_patient,
               AVG(medication_count) AS avg_medications_per_patient
        FROM {M}.mart_patient_population
        {where_sql}
        GROUP BY age_group
    """
    df = run_query(sql, tuple(params))
    if df.empty:
        return {}

    from dashboard.config import AGE_GROUP_MIDPOINTS

    total_patients = int(df["patient_count"].sum())
    weighted_age = sum(
        AGE_GROUP_MIDPOINTS.get(row["age_group"], 0) * row["patient_count"] for _, row in df.iterrows()
    )
    avg_age = (weighted_age / total_patients) if total_patients else None

    # dim_patient_safe/mart_patient_population carry no is_deceased column
    # (excluded from the public mart alongside other non-essential
    # fields) -- deceased status is therefore not available at this
    # layer, so this KPI is reported as unavailable rather than guessed at.
    return {
        "patient_count": total_patients,
        "avg_age_estimated": avg_age,
        "deceased_patient_count": None,
        "avg_encounters_per_patient": float((df["avg_encounters_per_patient"] * df["patient_count"]).sum() / total_patients) if total_patients else None,
        "avg_conditions_per_patient": float((df["avg_conditions_per_patient"] * df["patient_count"]).sum() / total_patients) if total_patients else None,
        "avg_medications_per_patient": float((df["avg_medications_per_patient"] * df["patient_count"]).sum() / total_patients) if total_patients else None,
    }


def get_gender_distribution(f: Filters) -> pd.DataFrame:
    conditions = [_cond("age_group = %s", f.age_group), _cond("race = %s", f.race)]
    where_sql, params = _where(conditions)
    sql = f"SELECT gender, COUNT(*) AS patient_count FROM {M}.mart_patient_population {where_sql} GROUP BY gender ORDER BY patient_count DESC"
    return run_query(sql, tuple(params))


def get_race_distribution(f: Filters) -> pd.DataFrame:
    conditions = [_cond("age_group = %s", f.age_group), _cond("gender = %s", f.gender)]
    where_sql, params = _where(conditions)
    sql = f"SELECT race, COUNT(*) AS patient_count FROM {M}.mart_patient_population {where_sql} GROUP BY race ORDER BY patient_count DESC"
    return run_query(sql, tuple(params))


def get_ethnicity_distribution(f: Filters) -> pd.DataFrame:
    conditions = [_cond("age_group = %s", f.age_group), _cond("gender = %s", f.gender)]
    where_sql, params = _where(conditions)
    sql = f"SELECT ethnicity, COUNT(*) AS patient_count FROM {M}.mart_patient_population {where_sql} GROUP BY ethnicity ORDER BY patient_count DESC"
    return run_query(sql, tuple(params))


def get_geographic_distribution(f: Filters, limit: int = 20) -> pd.DataFrame:
    sql = f"""
        SELECT state, county, COUNT(*) AS patient_count
        FROM {M}.mart_patient_population
        WHERE state IS NOT NULL
        GROUP BY state, county
        ORDER BY patient_count DESC
        LIMIT %s
    """
    return run_query(sql, (limit,))


def get_encounters_per_patient_distribution(f: Filters) -> pd.DataFrame:
    sql = f"SELECT distinct_encounter_count FROM {M}.mart_patient_population WHERE distinct_encounter_count IS NOT NULL"
    return run_query(sql)


def get_conditions_per_patient_distribution(f: Filters) -> pd.DataFrame:
    sql = f"SELECT condition_count FROM {M}.mart_patient_population WHERE condition_count IS NOT NULL"
    return run_query(sql)


def get_medications_per_patient_distribution(f: Filters) -> pd.DataFrame:
    sql = f"SELECT medication_count FROM {M}.mart_patient_population WHERE medication_count IS NOT NULL"
    return run_query(sql)


# ---------------------------------------------------------------------------
# Page 7: Data Quality (reads JSON/CSV pipeline reports, not SQL)
# ---------------------------------------------------------------------------


def get_data_quality_summary() -> dict[str, Any]:
    from dashboard.reports import load_pipeline_reports

    return load_pipeline_reports()
