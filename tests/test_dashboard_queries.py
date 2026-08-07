"""Tests for dashboard/queries.py, dashboard/database.py, dashboard/formatting.py.

Mocked throughout -- no live PostgreSQL, no Streamlit server required.
dashboard.database degrades gracefully without streamlit installed, so
this file runs under the project's main Python (as required by the
exact pytest command in docs/dashboard_guide.md) as well as under
.venv-dashboard.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dashboard import database, formatting, queries  # noqa: E402


# -- Filters / WHERE-clause building: always parameterized -----------------


def test_cond_returns_none_for_empty_values():
    assert queries._cond("x = %s", None) is None
    assert queries._cond("x = %s", "") is None
    assert queries._cond("x = %s", "All") is None


def test_cond_returns_condition_for_real_values():
    result = queries._cond("x = %s", "acme")
    assert result == ("x = %s", "acme")


def test_where_builds_parameterized_sql_never_inline_values():
    sql, params = queries._where([("a = %s", 1), ("b = %s", "hello")])
    assert sql == " WHERE a = %s AND b = %s"
    assert params == [1, "hello"]
    # the actual value must never appear inline in the SQL text
    assert "hello" not in sql
    assert "1" not in sql


def test_where_with_no_conditions_is_empty():
    sql, params = queries._where([None, None])
    assert sql == ""
    assert params == []


def test_encounter_conditions_use_placeholders_not_raw_sql_injection():
    f = queries.Filters(encounter_class="inpatient'; DROP TABLE fct_encounters; --")
    conditions = queries._encounter_conditions(f)
    where_sql, params = queries._where(conditions)
    assert "DROP TABLE" not in where_sql
    assert "%s" in where_sql
    assert "inpatient'; DROP TABLE fct_encounters; --" in params


def test_filters_is_empty():
    assert queries.Filters().is_empty()
    assert not queries.Filters(organization="Acme").is_empty()


# -- Readmission window allow-list: never a raw column name from input -----


def test_readmission_window_columns_is_a_fixed_allowlist():
    assert queries.READMISSION_WINDOW_COLUMNS == {
        7: "readmitted_within_7_days", 14: "readmitted_within_14_days", 30: "readmitted_within_30_days",
    }


def test_readmission_kpis_falls_back_to_30_day_for_unknown_window(monkeypatch):
    captured_sql = []

    def fake_run_query(sql, params=None):
        captured_sql.append(sql)
        return pd.DataFrame([{"qualifying_index_encounters": 10, "readmitted_7": 1, "readmitted_14": 2, "readmitted_30": 3, "avg_days_to_readmission": 5.0}])

    monkeypatch.setattr(queries, "run_query", fake_run_query)
    f = queries.Filters(readmission_window=999)  # not in the allow-list
    queries.get_readmission_kpis(f)
    assert "readmitted_within_30_days" in captured_sql[0]
    assert "999" not in captured_sql[0]


# -- database.py: restricted-column defense-in-depth ------------------------


def test_assert_no_restricted_columns_passes_for_safe_columns():
    database.assert_no_restricted_columns(["patient_key", "age_group", "gender", "total_claim_cost"])


@pytest.mark.parametrize("column", ["ssn", "patient_ssn", "passport_number", "first_name", "last_name", "latitude", "longitude", "street_address"])
def test_assert_no_restricted_columns_blocks_pii_columns(column):
    with pytest.raises(database.RestrictedColumnError):
        database.assert_no_restricted_columns(["patient_key", column])


def test_run_query_wraps_driver_errors_in_dashboard_query_error(monkeypatch):
    class FakeConfig:
        def safe_repr(self):
            return "postgresql://user:***@host/db"

    monkeypatch.setattr(database, "get_connection_config", lambda: FakeConfig())

    def fake_get_connection(config):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(database, "get_connection", fake_get_connection)
    with pytest.raises(database.DashboardQueryError):
        database._run("SELECT 1")


def test_run_query_raises_dashboard_error_on_missing_credentials(monkeypatch):
    def raise_missing():
        raise database.MissingCredentialsError("POSTGRES_PASSWORD missing")

    monkeypatch.setattr(database, "get_connection_config", raise_missing)
    with pytest.raises(database.DashboardQueryError):
        database._run("SELECT 1")


# -- formatting.py: KPI math, percentages, currency, zero-denominator ------


def test_safe_divide_normal_case():
    assert formatting.safe_divide(50, 200) == 0.25


def test_safe_divide_zero_denominator_returns_none():
    assert formatting.safe_divide(50, 0) is None


def test_safe_divide_none_inputs_return_none():
    assert formatting.safe_divide(None, 10) is None
    assert formatting.safe_divide(10, None) is None


def test_format_currency():
    assert formatting.format_currency(1234.5) == "$1,234"
    assert formatting.format_currency(1234.567, decimals=2) == "$1,234.57"
    assert formatting.format_currency(None) == "N/A"


def test_format_percent_from_fraction():
    assert formatting.format_percent(0.1234) == "12.3%"


def test_format_percent_already_percent():
    assert formatting.format_percent(12.3, already_pct=True) == "12.3%"


def test_format_percent_none_is_na():
    assert formatting.format_percent(None) == "N/A"


def test_format_number():
    assert formatting.format_number(1234) == "1,234"
    assert formatting.format_number(None) == "N/A"


def test_format_duration_minutes_under_an_hour():
    assert formatting.format_duration_minutes(45) == "45 min"


def test_format_duration_minutes_over_an_hour():
    assert formatting.format_duration_minutes(150) == "2.5 hrs"


def test_pct_change_computes_correctly():
    assert formatting.pct_change(110, 100) == pytest.approx(0.10)
    assert formatting.pct_change(90, 100) == pytest.approx(-0.10)


def test_pct_change_zero_previous_returns_none():
    assert formatting.pct_change(10, 0) is None


def test_describe_change_only_when_comparison_exists():
    assert formatting.describe_change("Encounters", 110, 100) == "Encounters increased 10.0% compared with the previous period."
    assert formatting.describe_change("Encounters", None, None) is None
    assert formatting.describe_change("Encounters", 10, 0) is None


def test_describe_change_decrease():
    text = formatting.describe_change("Cost", 90, 100)
    assert "decreased" in text


def test_top_row_label_empty_dataframe_returns_none():
    assert formatting.top_row_label(pd.DataFrame(), "name", "value") is None


def test_top_row_label_returns_highest_value_row():
    df = pd.DataFrame({"name": ["a", "b", "c"], "value": [1, 5, 3]})
    assert formatting.top_row_label(df, "name", "value") == "b"


# -- empty dataset handling --------------------------------------------


def test_queries_handle_empty_kpi_result_gracefully(monkeypatch):
    monkeypatch.setattr(queries, "run_query", lambda sql, params=None: pd.DataFrame())
    assert queries.get_executive_kpis(queries.Filters()) == {}
    assert queries.get_readmission_kpis(queries.Filters()) == {}
    assert queries.get_operations_kpis(queries.Filters()) == {}
    assert queries.get_financial_kpis(queries.Filters()) == {}


def test_get_patient_population_kpis_empty_returns_empty_dict(monkeypatch):
    monkeypatch.setattr(queries, "run_query", lambda sql, params=None: pd.DataFrame())
    assert queries.get_patient_population_kpis(queries.Filters()) == {}


# -- filter option building never crashes on empty results -----------------


def test_get_filter_options_handles_empty_tables(monkeypatch):
    monkeypatch.setattr(queries, "run_filter_query", lambda sql, params=None: pd.DataFrame())
    options = queries.get_filter_options()
    assert options["organizations"] == []
    assert options["age_groups"] == []


# -- date filters are passed as parameters, not string-formatted into SQL --


def test_encounter_trend_uses_year_month_string_params_not_raw_dates(monkeypatch):
    captured = {}

    def fake_run_query(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return pd.DataFrame()

    monkeypatch.setattr(queries, "run_query", fake_run_query)
    f = queries.Filters(start_date=date(2026, 1, 1), end_date=date(2026, 3, 31))
    queries.get_encounter_trend(f)
    assert "2026-01" in captured["params"]
    assert "2026-03" in captured["params"]
    assert "2026-01" not in captured["sql"]  # never inlined into the SQL text
