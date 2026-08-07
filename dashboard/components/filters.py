"""Global sidebar filters, shared across all seven pages.

Every widget value is constrained to options fetched from the database
(queries.get_filter_options) or a fixed small set (readmission window) --
nothing here ever becomes a raw string concatenated into SQL; queries.py
always passes these values through parameterized placeholders.
"""

from __future__ import annotations

import streamlit as st

from dashboard.queries import Filters, get_filter_options

_DEFAULTS: dict[str, object] = {
    "filter_start_date": None,
    "filter_end_date": None,
    "filter_organization": "All",
    "filter_provider": "All",
    "filter_payer": "All",
    "filter_encounter_class": "All",
    "filter_age_group": "All",
    "filter_gender": "All",
    "filter_race": "All",
    "filter_readmission_window": 30,
}


def _reset_filters() -> None:
    for key, value in _DEFAULTS.items():
        st.session_state[key] = value


def render_sidebar_filters() -> Filters:
    options = get_filter_options()

    with st.sidebar:
        st.markdown("### Filters")

        st.button("Reset Filters", on_click=_reset_filters, use_container_width=True)

        for key, default in _DEFAULTS.items():
            st.session_state.setdefault(key, default)

        date_range = st.date_input(
            "Date range",
            value=(st.session_state["filter_start_date"], st.session_state["filter_end_date"])
            if st.session_state["filter_start_date"] and st.session_state["filter_end_date"]
            else (),
            help="Filters encounter-level charts and tables by encounter date.",
        )
        start_date, end_date = (None, None)
        if isinstance(date_range, tuple) and len(date_range) == 2:
            start_date, end_date = date_range
            st.session_state["filter_start_date"], st.session_state["filter_end_date"] = start_date, end_date

        organization = st.selectbox("Organization", ["All", *options["organizations"]], key="filter_organization")
        provider = st.selectbox("Provider", ["All", *options["providers"]], key="filter_provider")
        payer = st.selectbox("Payer", ["All", *options["payers"]], key="filter_payer")
        encounter_class = st.selectbox("Encounter class", ["All", *options["encounter_classes"]], key="filter_encounter_class")

        st.markdown("**Patient demographics**")
        age_group = st.selectbox("Age group", ["All", *options["age_groups"]], key="filter_age_group")
        gender = st.selectbox("Gender", ["All", *options["genders"]], key="filter_gender")
        race = st.selectbox("Race", ["All", *options["races"]], key="filter_race")

        readmission_window = st.select_slider(
            "Readmission window (days)", options=[7, 14, 30], key="filter_readmission_window",
        )

        year = None
        if options["years"]:
            year_choice = st.selectbox("Year (trend charts)", ["All", *sorted(options["years"], reverse=True)])
            year = int(year_choice) if year_choice != "All" else None

    return Filters(
        start_date=start_date,
        end_date=end_date,
        year=year,
        month=None,
        organization=None if organization == "All" else organization,
        provider=None if provider == "All" else provider,
        payer=None if payer == "All" else payer,
        encounter_class=None if encounter_class == "All" else encounter_class,
        age_group=None if age_group == "All" else age_group,
        gender=None if gender == "All" else gender,
        race=None if race == "All" else race,
        readmission_window=int(readmission_window),
    )
