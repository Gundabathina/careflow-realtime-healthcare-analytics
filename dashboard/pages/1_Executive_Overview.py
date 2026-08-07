"""Page 1: Executive Overview -- hospital-wide KPIs and trends."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import streamlit as st

from dashboard.components.charts import donut_chart, horizontal_bar_chart, line_chart, multi_line_chart
from dashboard.components.filters import render_sidebar_filters
from dashboard.components.kpi_cards import KPI, render_kpi_row
from dashboard.components.layout import (
    configure_page,
    render_empty_state,
    render_error_state,
    render_header,
    render_insight_panel,
    render_section_title,
    render_synthetic_data_notice,
)
from dashboard.database import DashboardQueryError
from dashboard.formatting import (
    describe_change,
    format_currency,
    format_duration_minutes,
    format_number,
    format_percent,
    top_row_label,
)
from dashboard.queries import (
    get_encounter_class_distribution,
    get_encounter_trend,
    get_executive_kpis,
    get_monthly_cost_trend,
    get_monthly_readmission_trend,
    get_payer_coverage_breakdown,
    get_age_group_distribution,
    get_top_organizations_by_encounters,
)

configure_page("Executive Overview")
render_header(
    "Executive Overview",
    "Hospital-wide operational, clinical, and financial KPIs at a glance.",
)

filters = render_sidebar_filters()

try:
    kpis = get_executive_kpis(filters)

    render_kpi_row(
        [
            KPI("Patients Served", kpis.get("patients_served"), format_number),
            KPI("Total Encounters", kpis.get("total_encounters"), format_number),
            KPI("Inpatient Encounters", kpis.get("inpatient_encounters"), format_number),
            KPI("Emergency Encounters", kpis.get("emergency_encounters"), format_number),
            KPI("30-Day Readmission Rate", kpis.get("readmission_rate_30_day"), format_percent,
                help_text="Qualifying inpatient/emergency encounters readmitted within 30 days, hospital-wide."),
        ]
    )
    render_kpi_row(
        [
            KPI("Average Length of Stay", kpis.get("avg_length_of_stay_minutes"), format_duration_minutes),
            KPI("Total Claim Cost", kpis.get("total_claim_cost"), format_currency),
            KPI("Payer Coverage Ratio", kpis.get("payer_coverage_ratio"), format_percent,
                help_text="Total payer coverage / total claim cost."),
            KPI("Average Patient Responsibility", kpis.get("avg_patient_responsibility"), format_currency),
        ]
    )

    st.divider()

    encounter_trend = get_encounter_trend(filters)
    class_dist = get_encounter_class_distribution(filters)
    readmit_trend = get_monthly_readmission_trend(filters)
    cost_trend = get_monthly_cost_trend(filters)
    top_orgs = get_top_organizations_by_encounters(filters)
    age_dist = get_age_group_distribution(filters)
    payer_breakdown = get_payer_coverage_breakdown(filters)

    col1, col2 = st.columns(2)
    with col1:
        render_section_title("Encounters Over Time")
        fig = line_chart(encounter_trend, "year_month", "total_encounters", "Total Encounters by Month", "Month", "Encounters")
        if fig:
            st.plotly_chart(fig, width="stretch")
        else:
            render_empty_state()
    with col2:
        render_section_title("Encounter Class Distribution")
        fig = horizontal_bar_chart(class_dist, "encounter_class", "encounter_count", "Encounters by Class", "Encounters", "Encounter Class")
        if fig:
            st.plotly_chart(fig, width="stretch")
        else:
            render_empty_state()

    col1, col2 = st.columns(2)
    with col1:
        render_section_title("Monthly Readmission Trend")
        fig = line_chart(readmit_trend, "year_month", "readmission_rate_30_day", "30-Day Readmission Rate by Month", "Month", "Readmission Rate", y_is_percent=False)
        if fig:
            st.plotly_chart(fig, width="stretch")
        else:
            render_empty_state()
    with col2:
        render_section_title("Monthly Healthcare Cost Trend")
        fig = multi_line_chart(
            cost_trend, "year_month", ["total_claim_cost", "total_payer_coverage"],
            {"total_claim_cost": "Total Claim Cost", "total_payer_coverage": "Payer Coverage"},
            "Claim Cost & Payer Coverage by Month", "Month", "USD",
        )
        if fig:
            st.plotly_chart(fig, width="stretch")
        else:
            render_empty_state()

    col1, col2 = st.columns(2)
    with col1:
        render_section_title("Top Organizations by Encounters")
        fig = horizontal_bar_chart(top_orgs, "organization_name", "encounter_count", "Top 10 Organizations", "Encounters", "Organization")
        if fig:
            st.plotly_chart(fig, width="stretch")
        else:
            render_empty_state()
    with col2:
        render_section_title("Patient Age-Group Distribution")
        fig = horizontal_bar_chart(age_dist, "age_group", "patient_count", "Patients by Age Group", "Patients", "Age Group")
        if fig:
            st.plotly_chart(fig, width="stretch")
        else:
            render_empty_state()

    render_section_title("Payer Coverage Breakdown")
    fig = donut_chart(payer_breakdown, "payer_name", "total_payer_coverage", "Total Payer Coverage by Payer")
    if fig:
        st.plotly_chart(fig, width="stretch")
    else:
        render_empty_state()

    # -- Data-driven executive insights (only shown when a comparison exists) --
    insights = []
    if encounter_trend is not None and len(encounter_trend) >= 2:
        curr, prev = encounter_trend.iloc[-1], encounter_trend.iloc[-2]
        text = describe_change("Total encounters", curr["total_encounters"], prev["total_encounters"])
        if text:
            insights.append(text)
    if cost_trend is not None and len(cost_trend) >= 2:
        curr, prev = cost_trend.iloc[-1], cost_trend.iloc[-2]
        text = describe_change("Total claim cost", curr["total_claim_cost"], prev["total_claim_cost"])
        if text:
            insights.append(text)
    if readmit_trend is not None and len(readmit_trend) >= 2:
        curr, prev = readmit_trend.iloc[-1], readmit_trend.iloc[-2]
        text = describe_change("The 30-day readmission rate", curr["readmission_rate_30_day"], prev["readmission_rate_30_day"])
        if text:
            insights.append(text)
    top_org = top_row_label(top_orgs, "organization_name", "encounter_count")
    if top_org:
        insights.append(f"{top_org} has the highest encounter volume among organizations shown.")
    top_class = top_row_label(class_dist, "encounter_class", "encounter_count")
    if top_class:
        insights.append(f"{top_class.title()} is the most common encounter class in the selected period.")

    render_insight_panel(insights)

    with st.expander("Download aggregated data"):
        if encounter_trend is not None and not encounter_trend.empty:
            st.download_button(
                "Download monthly KPIs (CSV)", encounter_trend.to_csv(index=False).encode("utf-8"),
                "executive_monthly_encounters.csv", "text/csv",
            )

except DashboardQueryError as exc:
    render_error_state(str(exc))

render_synthetic_data_notice()
