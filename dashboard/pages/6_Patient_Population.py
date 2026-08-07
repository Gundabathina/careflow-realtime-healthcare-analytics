"""Page 6: Patient Population -- safe demographic composition only.

Never displays patient names, SSN, passport, driver license, exact
address, latitude, or longitude -- dim_patient_safe/mart_patient_population
never carry those columns in the first place (enforced by the dbt
layer's own PII tests, Phase 3C), and database.py's
assert_no_restricted_columns is a second, independent check on every
query result.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import streamlit as st

from dashboard.components.charts import distribution_histogram, horizontal_bar_chart
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
from dashboard.formatting import format_number
from dashboard.queries import (
    get_age_group_distribution,
    get_conditions_per_patient_distribution,
    get_encounters_per_patient_distribution,
    get_ethnicity_distribution,
    get_gender_distribution,
    get_geographic_distribution,
    get_medications_per_patient_distribution,
    get_patient_population_kpis,
    get_race_distribution,
)

configure_page("Patient Population")
render_header("Patient Population", "Safe, aggregated demographic composition of the patient population.")

filters = render_sidebar_filters()

try:
    kpis = get_patient_population_kpis(filters)

    render_kpi_row(
        [
            KPI("Patient Count", kpis.get("patient_count"), format_number),
            KPI("Average Age (estimated)", kpis.get("avg_age_estimated"), lambda v: f"{float(v):,.1f} yrs",
                help_text="Estimated from age-group midpoints -- exact age/birth date is never exposed at this layer."),
            KPI("Deceased Patient Count", kpis.get("deceased_patient_count"), format_number,
                help_text="Not available in the public patient mart by design."),
        ]
    )
    render_kpi_row(
        [
            KPI("Average Encounters per Patient", kpis.get("avg_encounters_per_patient"), lambda v: format_number(v, decimals=1)),
            KPI("Average Conditions per Patient", kpis.get("avg_conditions_per_patient"), lambda v: format_number(v, decimals=1)),
            KPI("Average Medications per Patient", kpis.get("avg_medications_per_patient"), lambda v: format_number(v, decimals=1)),
        ]
    )

    st.divider()

    age_dist = get_age_group_distribution(filters)
    gender_dist = get_gender_distribution(filters)
    race_dist = get_race_distribution(filters)
    ethnicity_dist = get_ethnicity_distribution(filters)
    geo_dist = get_geographic_distribution(filters)
    encounters_dist = get_encounters_per_patient_distribution(filters)
    conditions_dist = get_conditions_per_patient_distribution(filters)
    medications_dist = get_medications_per_patient_distribution(filters)

    col1, col2 = st.columns(2)
    with col1:
        render_section_title("Age-Group Distribution")
        fig = horizontal_bar_chart(age_dist, "age_group", "patient_count", "Patients by Age Group", "Patients", "Age Group")
        if fig:
            st.plotly_chart(fig, width="stretch")
        else:
            render_empty_state()
    with col2:
        render_section_title("Gender Distribution")
        fig = horizontal_bar_chart(gender_dist, "gender", "patient_count", "Patients by Gender", "Patients", "Gender")
        if fig:
            st.plotly_chart(fig, width="stretch")
        else:
            render_empty_state()

    col1, col2 = st.columns(2)
    with col1:
        render_section_title("Race Distribution")
        fig = horizontal_bar_chart(race_dist, "race", "patient_count", "Patients by Race", "Patients", "Race")
        if fig:
            st.plotly_chart(fig, width="stretch")
        else:
            render_empty_state()
    with col2:
        render_section_title("Ethnicity Distribution")
        fig = horizontal_bar_chart(ethnicity_dist, "ethnicity", "patient_count", "Patients by Ethnicity", "Patients", "Ethnicity")
        if fig:
            st.plotly_chart(fig, width="stretch")
        else:
            render_empty_state()

    render_section_title("Geographic Distribution", "State/county only -- never exact address, latitude, or longitude.")
    if geo_dist is not None and not geo_dist.empty:
        geo_dist_display = geo_dist.copy()
        geo_dist_display["location"] = geo_dist_display["state"].fillna("Unknown") + " / " + geo_dist_display["county"].fillna("Unknown")
        fig = horizontal_bar_chart(geo_dist_display, "location", "patient_count", "Top 20 State/County Combinations", "Patients", "State / County")
        if fig:
            st.plotly_chart(fig, width="stretch")
        else:
            render_empty_state()
    else:
        render_empty_state()

    col1, col2, col3 = st.columns(3)
    with col1:
        render_section_title("Encounters per Patient")
        fig = distribution_histogram(encounters_dist, "distinct_encounter_count", "Distinct Encounters per Patient", "Encounters")
        if fig:
            st.plotly_chart(fig, width="stretch")
        else:
            render_empty_state()
    with col2:
        render_section_title("Conditions per Patient")
        fig = distribution_histogram(conditions_dist, "condition_count", "Conditions per Patient", "Conditions")
        if fig:
            st.plotly_chart(fig, width="stretch")
        else:
            render_empty_state()
    with col3:
        render_section_title("Medications per Patient")
        fig = distribution_histogram(medications_dist, "medication_count", "Medications per Patient", "Medications")
        if fig:
            st.plotly_chart(fig, width="stretch")
        else:
            render_empty_state()

except DashboardQueryError as exc:
    render_error_state(str(exc))

render_synthetic_data_notice()
