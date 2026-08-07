"""Page chrome shared by every dashboard page: header, section titles,
insight panels, disclaimers, and empty/error-state placeholders."""

from __future__ import annotations

import streamlit as st

from dashboard.config import APP_SUBTITLE, APP_TITLE, PAGE_ICON


def configure_page(page_title: str) -> None:
    st.set_page_config(
        page_title=f"{page_title} | {APP_TITLE}",
        page_icon=PAGE_ICON,
        layout="wide",
        initial_sidebar_state="expanded",
    )


def render_header(page_title: str, page_description: str) -> None:
    st.markdown(
        f"""
        <div style="padding: 0.25rem 0 1rem 0; border-bottom: 1px solid rgba(128,128,128,0.25); margin-bottom: 1.25rem;">
            <div style="font-size: 0.85rem; letter-spacing: 0.06em; text-transform: uppercase; opacity: 0.65;">
                {APP_TITLE} &middot; {APP_SUBTITLE}
            </div>
            <h1 style="margin: 0.15rem 0 0.15rem 0; font-size: 1.9rem;">{page_title}</h1>
            <div style="opacity: 0.75; font-size: 0.95rem;">{page_description}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_section_title(title: str, subtitle: str | None = None) -> None:
    st.markdown(f"#### {title}")
    if subtitle:
        st.caption(subtitle)


def render_insight_panel(insights: list[str], title: str = "Executive Insights") -> None:
    """Only renders when there is at least one data-backed insight -- never a placeholder."""
    if not insights:
        return
    with st.container(border=True):
        st.markdown(f"**{title}**")
        for insight in insights:
            st.markdown(f"- {insight}")


def render_definition_box(title: str, definition: str) -> None:
    with st.container(border=True):
        st.markdown(f"**{title}**")
        st.caption(definition)


def render_empty_state(message: str = "No data available for the selected filters.") -> None:
    st.info(message)


def render_error_state(message: str) -> None:
    st.error(message)


def render_synthetic_data_notice() -> None:
    st.caption(
        "Data is derived from Synthea-generated synthetic patients and is used here to "
        "demonstrate analytics engineering, not to represent real hospital performance."
    )
