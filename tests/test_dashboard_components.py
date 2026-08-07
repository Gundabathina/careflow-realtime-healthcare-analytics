"""Tests for dashboard/components/* and the seven dashboard pages.

Requires streamlit + plotly (installed in the isolated .venv-dashboard,
not the project's main Python -- see docs/dashboard_guide.md), so this
whole file is skipped via pytest.importorskip when run under an
interpreter without them. No live Streamlit server or PostgreSQL is
required: streamlit widgets execute fine in "bare mode" outside
`streamlit run` (they only warn, never raise), and every database call
is monkeypatched to avoid a live connection.

    PYTHONPATH=src .venv-dashboard/bin/python -m pytest -q tests/test_dashboard_components.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("streamlit")
pytest.importorskip("plotly")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import streamlit as st  # noqa: E402

from dashboard import queries  # noqa: E402
from dashboard.components import charts, filters, kpi_cards, layout  # noqa: E402

PAGES_DIR = PROJECT_ROOT / "dashboard" / "pages"


# -- charts.py: empty-data handling, titles, axis labels --------------------


EMPTY_DF = pd.DataFrame()
VALID_TREND_DF = pd.DataFrame({"year_month": ["2026-01", "2026-02"], "total_encounters": [100, 120]})
VALID_CATEGORY_DF = pd.DataFrame({"encounter_class": ["inpatient", "emergency"], "encounter_count": [10, 20]})
VALID_HEATMAP_DF = pd.DataFrame({
    "organization_name": ["Org A", "Org A", "Org B"],
    "encounter_class": ["inpatient", "emergency", "inpatient"],
    "encounter_count": [5, 3, 7],
})


def test_line_chart_returns_none_for_empty_data():
    assert charts.line_chart(EMPTY_DF, "year_month", "total_encounters", "T", "X", "Y") is None


def test_line_chart_returns_none_for_missing_columns():
    assert charts.line_chart(VALID_TREND_DF, "not_a_column", "total_encounters", "T", "X", "Y") is None


def test_line_chart_returns_figure_with_title_and_axis_labels():
    fig = charts.line_chart(VALID_TREND_DF, "year_month", "total_encounters", "Monthly Encounters", "Month", "Encounters")
    assert fig is not None
    assert fig.layout.title.text == "Monthly Encounters"
    assert fig.layout.xaxis.title.text == "Month"
    assert fig.layout.yaxis.title.text == "Encounters"


def test_line_chart_none_input_does_not_raise():
    assert charts.line_chart(None, "x", "y", "T", "X", "Y") is None


def test_horizontal_bar_chart_returns_none_for_empty_data():
    assert charts.horizontal_bar_chart(EMPTY_DF, "encounter_class", "encounter_count", "T", "X", "Y") is None


def test_horizontal_bar_chart_returns_figure_for_valid_data():
    fig = charts.horizontal_bar_chart(VALID_CATEGORY_DF, "encounter_class", "encounter_count", "Encounters by Class", "Encounters", "Class")
    assert fig is not None
    assert fig.layout.title.text == "Encounters by Class"


def test_horizontal_bar_chart_respects_top_n():
    df = pd.DataFrame({"cat": [f"c{i}" for i in range(20)], "val": list(range(20))})
    fig = charts.horizontal_bar_chart(df, "cat", "val", "T", "X", "Y", top_n=5)
    assert fig is not None
    assert len(fig.data[0].x) == 5


def test_distribution_histogram_returns_none_for_all_null_column():
    df = pd.DataFrame({"days_to_readmission": [None, None]})
    assert charts.distribution_histogram(df, "days_to_readmission", "T", "X") is None


def test_distribution_histogram_returns_figure_for_valid_data():
    df = pd.DataFrame({"days": [1, 2, 3, 4, 5]})
    fig = charts.distribution_histogram(df, "days", "Days to Readmission", "Days")
    assert fig is not None


def test_heatmap_returns_none_for_empty_data():
    assert charts.heatmap(EMPTY_DF, "encounter_class", "organization_name", "encounter_count", "T", "X", "Y") is None


def test_heatmap_returns_figure_for_valid_data():
    fig = charts.heatmap(VALID_HEATMAP_DF, "encounter_class", "organization_name", "encounter_count", "Volume", "Class", "Organization")
    assert fig is not None


def test_donut_chart_returns_none_for_empty_data():
    assert charts.donut_chart(EMPTY_DF, "payer_name", "total_payer_coverage", "T") is None


def test_donut_chart_returns_figure_for_valid_data():
    df = pd.DataFrame({"payer_name": ["Payer A", "Payer B"], "coverage": [100, 200]})
    fig = charts.donut_chart(df, "payer_name", "coverage", "Coverage Breakdown")
    assert fig is not None


def test_multi_line_chart_returns_none_when_no_requested_columns_present():
    assert charts.multi_line_chart(VALID_TREND_DF, "year_month", ["nonexistent"], {}, "T", "X", "Y") is None


def test_multi_line_chart_returns_figure_for_partial_column_match():
    fig = charts.multi_line_chart(VALID_TREND_DF, "year_month", ["total_encounters", "nonexistent"], {}, "T", "X", "Y")
    assert fig is not None


def test_scatter_chart_returns_none_for_missing_columns():
    assert charts.scatter_chart(VALID_TREND_DF, "not_there", "total_encounters", "T", "X", "Y") is None


# -- kpi_cards.py -------------------------------------------------------


def test_kpi_dataclass_defaults():
    kpi = kpi_cards.KPI("Label", 42, str)
    assert kpi.help_text is None
    assert kpi.delta is None


def test_render_kpi_row_handles_none_value_without_raising():
    kpi_cards.render_kpi_row([kpi_cards.KPI("Missing", None, lambda v: str(v))])


def test_render_kpi_row_handles_empty_list_without_raising():
    kpi_cards.render_kpi_row([])


def test_render_kpi_row_formats_values_via_provided_formatter():
    calls = []
    kpi_cards.render_kpi_row([kpi_cards.KPI("X", 5, lambda v: calls.append(v) or f"={v}")])
    assert calls == [5]


# -- filters.py: reset behavior ------------------------------------------


def test_reset_filters_restores_defaults():
    st.session_state["filter_organization"] = "Some Org"
    st.session_state["filter_readmission_window"] = 7
    filters._reset_filters()
    assert st.session_state["filter_organization"] == "All"
    assert st.session_state["filter_readmission_window"] == 30


# -- layout.py ------------------------------------------------------------


def test_render_insight_panel_with_empty_list_does_not_raise():
    layout.render_insight_panel([])


def test_render_insight_panel_with_insights_does_not_raise():
    layout.render_insight_panel(["Encounters increased 5% month over month."])


def test_render_empty_state_does_not_raise():
    layout.render_empty_state()
    layout.render_empty_state("Custom message")


def test_render_error_state_does_not_raise():
    layout.render_error_state("Something went wrong (sanitized)")


def test_configure_page_can_be_called_multiple_times_in_test_process():
    layout.configure_page("Page A")
    layout.configure_page("Page B")  # must not raise outside a live script run


# -- dashboard page imports: every page executes without raising -----------


def _mock_all_queries(monkeypatch):
    monkeypatch.setattr(queries, "run_query", lambda sql, params=None: pd.DataFrame())
    monkeypatch.setattr(queries, "run_filter_query", lambda sql, params=None: pd.DataFrame())


def _load_page_module(filename: str):
    spec = importlib.util.spec_from_file_location(Path(filename).stem, PAGES_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PAGE_FILES = [
    "1_Executive_Overview.py",
    "2_Readmission_Analytics.py",
    "3_Hospital_Operations.py",
    "4_Financial_Performance.py",
    "5_Provider_Performance.py",
    "6_Patient_Population.py",
    "7_Data_Quality.py",
]


@pytest.mark.parametrize("filename", PAGE_FILES)
def test_page_imports_and_executes_without_error_on_empty_data(monkeypatch, filename):
    _mock_all_queries(monkeypatch)
    _load_page_module(filename)  # raises on any uncaught exception during module exec


def test_all_seven_pages_exist():
    assert sorted(p.name for p in PAGES_DIR.glob("*.py")) == sorted(PAGE_FILES)


def test_app_entrypoint_imports_and_executes_without_error(monkeypatch):
    monkeypatch.setattr(
        "dashboard.database.check_database_available",
        lambda: (False, "PostgreSQL is not configured (missing environment variables)."),
    )
    spec = importlib.util.spec_from_file_location("dashboard_app", PROJECT_ROOT / "dashboard" / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
