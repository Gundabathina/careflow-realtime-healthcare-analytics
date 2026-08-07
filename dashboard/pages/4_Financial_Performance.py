"""Page 4: Financial Performance -- claim cost, coverage, and patient responsibility."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import streamlit as st

from dashboard.components.charts import horizontal_bar_chart, line_chart, multi_line_chart
from dashboard.components.filters import render_sidebar_filters
from dashboard.components.kpi_cards import KPI, render_kpi_row
from dashboard.components.layout import (
    configure_page,
    render_empty_state,
    render_error_state,
    render_header,
    render_section_title,
    render_synthetic_data_notice,
)
from dashboard.database import DashboardQueryError
from dashboard.formatting import format_currency, format_percent
from dashboard.queries import (
    get_cost_by_encounter_class,
    get_cost_by_organization,
    get_coverage_ratio_by_payer,
    get_financial_kpis,
    get_monthly_claim_cost,
    get_patient_responsibility_trend,
    get_payer_coverage_over_time,
    get_top_payers_by_coverage,
)

configure_page("Financial Performance")
render_header("Financial Performance", "Claim cost, payer coverage, and patient responsibility across the health system.")

st.caption(
    "Figures are derived from Synthea-generated synthetic data and do not represent real hospital "
    "financial performance."
)

filters = render_sidebar_filters()

try:
    kpis = get_financial_kpis(filters)

    render_kpi_row(
        [
            KPI("Total Claim Cost", kpis.get("total_claim_cost"), format_currency),
            KPI("Total Payer Coverage", kpis.get("total_payer_coverage"), format_currency),
            KPI("Patient Responsibility", kpis.get("total_patient_responsibility"), format_currency),
        ]
    )
    render_kpi_row(
        [
            KPI("Average Cost per Encounter", kpis.get("avg_cost_per_encounter"), lambda v: format_currency(v, decimals=2)),
            KPI("Coverage Ratio", kpis.get("coverage_ratio"), format_percent),
            KPI("Average Patient Responsibility", kpis.get("avg_patient_responsibility"), lambda v: format_currency(v, decimals=2)),
        ]
    )

    st.divider()

    monthly_cost = get_monthly_claim_cost(filters)
    coverage_over_time = get_payer_coverage_over_time(filters)
    responsibility_trend = get_patient_responsibility_trend(filters)
    cost_by_class = get_cost_by_encounter_class(filters)
    cost_by_org = get_cost_by_organization(filters)
    coverage_by_payer = get_coverage_ratio_by_payer(filters)
    top_payers = get_top_payers_by_coverage(filters)

    col1, col2 = st.columns(2)
    with col1:
        render_section_title("Monthly Claim Cost")
        fig = line_chart(monthly_cost, "year_month", "total_claim_cost", "Total Claim Cost by Month", "Month", "USD", y_is_currency=True)
        if fig:
            st.plotly_chart(fig, width="stretch")
        else:
            render_empty_state()
    with col2:
        render_section_title("Payer Coverage Over Time")
        fig = line_chart(coverage_over_time, "year_month", "total_payer_coverage", "Total Payer Coverage by Month", "Month", "USD", y_is_currency=True)
        if fig:
            st.plotly_chart(fig, width="stretch")
        else:
            render_empty_state()

    col1, col2 = st.columns(2)
    with col1:
        render_section_title("Patient Responsibility Trend")
        fig = line_chart(responsibility_trend, "year_month", "total_patient_responsibility", "Total Patient Responsibility by Month", "Month", "USD", y_is_currency=True)
        if fig:
            st.plotly_chart(fig, width="stretch")
        else:
            render_empty_state()
    with col2:
        render_section_title("Cost by Encounter Class")
        fig = horizontal_bar_chart(cost_by_class, "encounter_class", "total_claim_cost", "Total Claim Cost by Encounter Class", "USD", "Encounter Class", value_is_currency=True)
        if fig:
            st.plotly_chart(fig, width="stretch")
        else:
            render_empty_state()

    col1, col2 = st.columns(2)
    with col1:
        render_section_title("Cost by Organization")
        fig = horizontal_bar_chart(cost_by_org, "organization_name", "total_claim_cost", "Top 15 Organizations by Claim Cost", "USD", "Organization", value_is_currency=True)
        if fig:
            st.plotly_chart(fig, width="stretch")
        else:
            render_empty_state()
    with col2:
        render_section_title("Coverage Ratio by Payer")
        fig = horizontal_bar_chart(coverage_by_payer, "payer_name", "coverage_ratio_pct", "Coverage Ratio by Payer", "Coverage Ratio (%)", "Payer", value_is_percent=True)
        if fig:
            st.plotly_chart(fig, width="stretch")
        else:
            render_empty_state()

    render_section_title("Top Payers by Total Coverage")
    fig = horizontal_bar_chart(top_payers, "payer_name", "total_payer_coverage", "Top 10 Payers", "USD", "Payer", value_is_currency=True)
    if fig:
        st.plotly_chart(fig, width="stretch")
    else:
        render_empty_state()

    with st.expander("Download aggregated data"):
        if monthly_cost is not None and not monthly_cost.empty:
            st.download_button(
                "Download financial summary (CSV)", monthly_cost.to_csv(index=False).encode("utf-8"),
                "financial_summary.csv", "text/csv",
            )

except DashboardQueryError as exc:
    render_error_state(str(exc))

render_synthetic_data_notice()
