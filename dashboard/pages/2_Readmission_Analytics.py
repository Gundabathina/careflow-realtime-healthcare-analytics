"""Page 2: Readmission Analytics -- 7/14/30-day readmission rates and segments."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import streamlit as st

from dashboard.components.charts import distribution_histogram, horizontal_bar_chart, line_chart
from dashboard.components.filters import render_sidebar_filters
from dashboard.components.kpi_cards import KPI, render_kpi_row
from dashboard.components.layout import (
    configure_page,
    render_definition_box,
    render_empty_state,
    render_error_state,
    render_header,
    render_section_title,
    render_synthetic_data_notice,
)
from dashboard.database import DashboardQueryError
from dashboard.formatting import format_number, format_percent
from dashboard.queries import (
    get_days_to_readmission_distribution,
    get_high_readmission_segments,
    get_readmission_kpis,
    get_readmission_trend,
    get_readmissions_by_age_group,
    get_readmissions_by_encounter_class,
    get_readmissions_by_gender,
    get_readmissions_by_organization,
)

configure_page("Readmission Analytics")
render_header(
    "Readmission Analytics",
    "7/14/30-day readmission rates, trends, and segment-level patterns.",
)

render_definition_box(
    "Readmission Definition",
    "A qualifying readmission occurs when a subsequent inpatient or emergency encounter begins "
    "within 30 days after the previous qualifying encounter ends. The 7- and 14-day windows are the "
    "same event, evaluated at a shorter cutoff.",
)

filters = render_sidebar_filters()

try:
    kpis = get_readmission_kpis(filters)

    render_kpi_row(
        [
            KPI("Qualifying Index Encounters", kpis.get("qualifying_index_encounters"), format_number),
            KPI("7-Day Readmission Rate", kpis.get("rate_7_day"), format_percent),
            KPI("14-Day Readmission Rate", kpis.get("rate_14_day"), format_percent),
            KPI("30-Day Readmission Rate", kpis.get("rate_30_day"), format_percent),
            KPI("Average Days to Readmission", kpis.get("avg_days_to_readmission"), lambda v: f"{float(v):,.1f} days"),
        ]
    )

    st.divider()

    trend = get_readmission_trend(filters)
    by_class = get_readmissions_by_encounter_class(filters)
    by_age = get_readmissions_by_age_group(filters)
    by_gender = get_readmissions_by_gender(filters)
    by_org = get_readmissions_by_organization(filters)
    days_dist = get_days_to_readmission_distribution(filters)

    col1, col2 = st.columns(2)
    with col1:
        render_section_title("Readmission Rate Over Time")
        fig = line_chart(trend, "year_month", "readmission_rate_30_day", "30-Day Readmission Rate by Month", "Month", "Rate")
        st.plotly_chart(fig, width="stretch") if fig else render_empty_state()
    with col2:
        render_section_title("Readmissions by Encounter Class")
        fig = horizontal_bar_chart(by_class, "index_encounter_class", "readmission_rate_pct", "Readmission Rate by Index Encounter Class", "Rate (%)", "Encounter Class", value_is_percent=True)
        st.plotly_chart(fig, width="stretch") if fig else render_empty_state()

    col1, col2 = st.columns(2)
    with col1:
        render_section_title("Readmissions by Age Group")
        fig = horizontal_bar_chart(by_age, "age_group", "readmission_rate_pct", "Readmission Rate by Age Group", "Rate (%)", "Age Group", value_is_percent=True)
        st.plotly_chart(fig, width="stretch") if fig else render_empty_state()
    with col2:
        render_section_title("Readmissions by Gender")
        fig = horizontal_bar_chart(by_gender, "gender", "readmission_rate_pct", "Readmission Rate by Gender", "Rate (%)", "Gender", value_is_percent=True)
        st.plotly_chart(fig, width="stretch") if fig else render_empty_state()

    col1, col2 = st.columns(2)
    with col1:
        render_section_title("Readmissions by Organization")
        fig = horizontal_bar_chart(by_org, "organization_name", "readmission_rate_pct", "Readmission Rate by Organization (min. 5 qualifying encounters)", "Rate (%)", "Organization", value_is_percent=True)
        st.plotly_chart(fig, width="stretch") if fig else render_empty_state()
    with col2:
        render_section_title("Distribution of Days to Readmission")
        fig = distribution_histogram(days_dist, "days_to_readmission", "Days to Readmission (within selected window)", "Days")
        st.plotly_chart(fig, width="stretch") if fig else render_empty_state()

    render_section_title(
        "Top Patient Segments by Readmission Rate",
        "Aggregated age-group x gender segments only -- no individual patient identities are shown.",
    )
    segments = get_high_readmission_segments(filters)
    if segments is not None and not segments.empty:
        st.dataframe(segments, width="stretch", hide_index=True)
        st.download_button(
            "Download readmission segment summary (CSV)", segments.to_csv(index=False).encode("utf-8"),
            "readmission_segment_summary.csv", "text/csv",
        )
    else:
        render_empty_state()

except DashboardQueryError as exc:
    render_error_state(str(exc))

render_synthetic_data_notice()
