"""KPI card rendering. All number formatting is delegated to
dashboard.formatting so the same logic is unit-tested without streamlit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import streamlit as st


@dataclass
class KPI:
    label: str
    value: Any
    formatter: Callable[[Any], str]
    help_text: str | None = None
    delta: str | None = None


def render_kpi_row(kpis: list[KPI], columns_per_row: int = 5) -> None:
    """Renders KPI cards in a responsive row of st.metric widgets."""
    if not kpis:
        return
    cols = st.columns(min(columns_per_row, len(kpis)))
    for i, kpi in enumerate(kpis):
        with cols[i % len(cols)]:
            display_value = kpi.formatter(kpi.value) if kpi.value is not None else "N/A"
            st.metric(label=kpi.label, value=display_value, delta=kpi.delta, help=kpi.help_text)
