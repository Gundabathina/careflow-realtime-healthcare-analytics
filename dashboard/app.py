"""CareFlow Analytics -- entry point.

Run with:
    streamlit run dashboard/app.py
or:
    PYTHONPATH=src python3 scripts/start_dashboard.py

Streamlit auto-discovers dashboard/pages/*.py for the sidebar
navigation; this file is only the landing page.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import streamlit as st

from dashboard.components.layout import configure_page, render_header, render_synthetic_data_notice
from dashboard.database import check_database_available

configure_page("Overview")
render_header(
    "Welcome to CareFlow Analytics",
    "A governed, end-to-end healthcare analytics platform: synthetic patient data flows through "
    "Bronze/Silver/Gold transformation, a dbt-tested PostgreSQL warehouse, and Airflow orchestration "
    "into the interactive analytics below.",
)

db_available, db_error = check_database_available()

if not db_available:
    st.error(
        "The CareFlow PostgreSQL warehouse is not reachable right now, so live analytics can't be "
        "shown. Start it and reload this page."
    )
    if db_error:
        st.caption(db_error)
else:
    st.success("Connected to the CareFlow PostgreSQL warehouse (careflow_dbt_mart).")

st.markdown("### What you can explore")

pages = [
    ("1 · Executive Overview", "Hospital-wide KPIs, encounter trends, and data-driven executive insights."),
    ("2 · Readmission Analytics", "7/14/30-day readmission rates, trends, and high-readmission segments."),
    ("3 · Hospital Operations", "Encounter volume, duration, and utilization across organizations."),
    ("4 · Financial Performance", "Claim cost, payer coverage, and patient responsibility trends."),
    ("5 · Provider Performance", "Provider-level encounter volume, utilization, and cost."),
    ("6 · Patient Population", "Safe demographic composition -- age group, gender, race, geography."),
    ("7 · Data Quality", "Bronze/Silver/Gold, PostgreSQL, dbt, and Airflow pipeline health, in one place."),
]

cols = st.columns(2)
for i, (title, description) in enumerate(pages):
    with cols[i % 2]:
        with st.container(border=True):
            st.markdown(f"**{title}**")
            st.caption(description)

st.divider()
render_synthetic_data_notice()
st.caption(
    "Data source: PostgreSQL `careflow_dbt_mart` (dbt reporting layer). "
    "This dashboard never queries Raw, Bronze, or Silver data directly, and never displays "
    "restricted patient identifiers."
)
