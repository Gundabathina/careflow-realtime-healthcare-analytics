"""Page 5: Provider Performance -- encounter volume, utilization, and cost by provider.

Uses neutral language throughout (utilization, encounter volume, patient
volume) -- never labels a provider's performance as "good" or "bad"
based on volume alone.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import streamlit as st

from dashboard.components.charts import horizontal_bar_chart, line_chart
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
from dashboard.formatting import format_currency, format_duration_minutes, format_number
from dashboard.queries import (
    get_avg_duration_by_provider,
    get_cost_by_provider,
    get_provider_kpis,
    get_provider_ranking_table,
    get_provider_speciality_distribution,
    get_provider_utilization_over_time,
    get_top_providers_by_encounters,
    get_top_providers_by_patients,
)

configure_page("Provider Performance")
render_header(
    "Provider Performance",
    "Provider-level encounter volume, patient volume, and utilization. Volume reflects activity, not quality of care.",
)

filters = render_sidebar_filters()

try:
    kpis = get_provider_kpis(filters)

    render_kpi_row(
        [
            KPI("Active Providers", kpis.get("active_providers"), format_number),
            KPI("Total Provider Encounters", kpis.get("total_provider_encounters"), format_number),
            KPI("Average Encounters per Provider", kpis.get("avg_encounters_per_provider"), lambda v: format_number(v, decimals=1)),
            KPI("Average Patients per Provider", kpis.get("avg_patients_per_provider"), lambda v: format_number(v, decimals=1)),
            KPI("Average Encounter Duration", kpis.get("avg_encounter_duration"), format_duration_minutes),
        ]
    )

    st.divider()

    top_by_encounters = get_top_providers_by_encounters(filters)
    top_by_patients = get_top_providers_by_patients(filters)
    utilization_trend = get_provider_utilization_over_time(filters)
    speciality_dist = get_provider_speciality_distribution(filters)
    duration_by_provider = get_avg_duration_by_provider(filters)
    cost_by_provider = get_cost_by_provider(filters)

    col1, col2 = st.columns(2)
    with col1:
        render_section_title("Top Providers by Encounter Volume")
        fig = horizontal_bar_chart(top_by_encounters, "provider_name", "total_encounters", "Top 10 Providers by Encounter Volume", "Encounters", "Provider")
        st.plotly_chart(fig, use_container_width=True) if fig else render_empty_state()
    with col2:
        render_section_title("Top Providers by Patient Volume")
        fig = horizontal_bar_chart(top_by_patients, "provider_name", "total_unique_patients", "Top 10 Providers by Unique Patients", "Patients", "Provider")
        st.plotly_chart(fig, use_container_width=True) if fig else render_empty_state()

    col1, col2 = st.columns(2)
    with col1:
        render_section_title("Provider Utilization Over Time")
        fig = line_chart(utilization_trend, "year_month", "total_encounters", "Encounter Volume by Month", "Month", "Encounters")
        st.plotly_chart(fig, use_container_width=True) if fig else render_empty_state()
    with col2:
        render_section_title("Provider Speciality Distribution")
        fig = horizontal_bar_chart(speciality_dist, "speciality", "provider_count", "Providers by Speciality", "Providers", "Speciality", top_n=15)
        st.plotly_chart(fig, use_container_width=True) if fig else render_empty_state()

    col1, col2 = st.columns(2)
    with col1:
        render_section_title("Average Duration by Provider")
        fig = horizontal_bar_chart(duration_by_provider, "provider_name", "avg_duration_minutes", "Top 15 by Average Encounter Duration", "Minutes", "Provider")
        st.plotly_chart(fig, use_container_width=True) if fig else render_empty_state()
    with col2:
        render_section_title("Total Claim Cost by Provider")
        fig = horizontal_bar_chart(cost_by_provider, "provider_name", "total_claim_cost", "Top 15 Providers by Claim Cost", "USD", "Provider", value_is_currency=True)
        st.plotly_chart(fig, use_container_width=True) if fig else render_empty_state()

    render_section_title("Provider Ranking", "Sortable by any column -- click a header to re-rank.")
    ranking = get_provider_ranking_table(filters)
    if ranking is not None and not ranking.empty:
        st.dataframe(ranking, use_container_width=True, hide_index=True)
        st.download_button(
            "Download provider utilization (CSV)", ranking.to_csv(index=False).encode("utf-8"),
            "provider_utilization.csv", "text/csv",
        )
    else:
        render_empty_state()

except DashboardQueryError as exc:
    render_error_state(str(exc))

render_synthetic_data_notice()
