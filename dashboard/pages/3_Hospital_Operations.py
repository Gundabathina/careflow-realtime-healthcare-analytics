"""Page 3: Hospital Operations -- encounter volume, duration, and utilization."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import streamlit as st

from dashboard.components.charts import heatmap, horizontal_bar_chart, line_chart
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
from dashboard.formatting import format_duration_minutes, format_number, format_percent
from dashboard.queries import (
    get_avg_duration_by_organization,
    get_emergency_utilization_trend,
    get_encounter_volume_by_month,
    get_encounter_volume_by_organization,
    get_inpatient_utilization_trend,
    get_operations_kpis,
    get_organization_comparison_table,
    get_organization_encounter_class_heatmap,
)

configure_page("Hospital Operations")
render_header("Hospital Operations", "Encounter volume, duration, and utilization across organizations.")

filters = render_sidebar_filters()

try:
    kpis = get_operations_kpis(filters)

    render_kpi_row(
        [
            KPI("Total Encounters", kpis.get("total_encounters"), format_number),
            KPI("Unique Patients", kpis.get("unique_patients"), format_number),
            KPI("Average Encounter Duration", kpis.get("avg_duration_minutes"), format_duration_minutes),
            KPI("Median Encounter Duration", kpis.get("median_duration_minutes"), format_duration_minutes),
        ]
    )
    render_kpi_row(
        [
            KPI("Emergency Encounter %", kpis.get("emergency_pct"), lambda v: format_percent(v, already_pct=True)),
            KPI("Inpatient Encounter %", kpis.get("inpatient_pct"), lambda v: format_percent(v, already_pct=True)),
            KPI("Providers Active", kpis.get("providers_active"), format_number),
        ]
    )

    st.divider()

    volume_by_month = get_encounter_volume_by_month(filters)
    volume_by_org = get_encounter_volume_by_organization(filters)
    duration_by_org = get_avg_duration_by_organization(filters)
    emergency_trend = get_emergency_utilization_trend(filters)
    inpatient_trend = get_inpatient_utilization_trend(filters)
    heatmap_df = get_organization_encounter_class_heatmap(filters)

    col1, col2 = st.columns(2)
    with col1:
        render_section_title("Encounter Volume by Month")
        fig = line_chart(volume_by_month, "year_month", "encounter_count", "Encounter Volume by Month", "Month", "Encounters")
        st.plotly_chart(fig, use_container_width=True) if fig else render_empty_state()
    with col2:
        render_section_title("Encounter Volume by Organization")
        fig = horizontal_bar_chart(volume_by_org, "organization_name", "encounter_count", "Top 15 Organizations by Volume", "Encounters", "Organization")
        st.plotly_chart(fig, use_container_width=True) if fig else render_empty_state()

    col1, col2 = st.columns(2)
    with col1:
        render_section_title("Average Encounter Duration by Organization")
        fig = horizontal_bar_chart(duration_by_org, "organization_name", "avg_duration_minutes", "Top 15 by Average Duration", "Minutes", "Organization")
        st.plotly_chart(fig, use_container_width=True) if fig else render_empty_state()
    with col2:
        render_section_title("Organization x Encounter Class")
        fig = heatmap(heatmap_df, "encounter_class", "organization_name", "encounter_count", "Encounter Volume Heatmap", "Encounter Class", "Organization")
        st.plotly_chart(fig, use_container_width=True) if fig else render_empty_state()

    col1, col2 = st.columns(2)
    with col1:
        render_section_title("Emergency Utilization Trend")
        fig = line_chart(emergency_trend, "year_month", "emergency_pct", "Emergency Encounter % by Month", "Month", "Emergency %")
        st.plotly_chart(fig, use_container_width=True) if fig else render_empty_state()
    with col2:
        render_section_title("Inpatient Utilization Trend")
        fig = line_chart(inpatient_trend, "year_month", "inpatient_pct", "Inpatient Encounter % by Month", "Month", "Inpatient %")
        st.plotly_chart(fig, use_container_width=True) if fig else render_empty_state()

    render_section_title("Organization Comparison")
    comparison = get_organization_comparison_table(filters)
    if comparison is not None and not comparison.empty:
        st.dataframe(comparison, use_container_width=True, hide_index=True)
        st.download_button(
            "Download organization comparison (CSV)", comparison.to_csv(index=False).encode("utf-8"),
            "organization_comparison.csv", "text/csv",
        )
    else:
        render_empty_state()

except DashboardQueryError as exc:
    render_error_state(str(exc))

render_synthetic_data_notice()
