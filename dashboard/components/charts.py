"""Plotly chart builders shared across all pages.

Every function returns ``None`` when there is no data to plot -- pages
check for ``None`` and render an empty-state message via
dashboard.components.layout.render_empty_state instead of crashing.
Every chart carries a title, labeled axes, and formatted hover text.
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from dashboard.config import CHART_COLOR_SEQUENCE

_LAYOUT_DEFAULTS = dict(
    template="plotly_white",
    margin=dict(l=10, r=10, t=50, b=10),
    font=dict(size=13),
    hoverlabel=dict(font_size=12),
    colorway=CHART_COLOR_SEQUENCE,
)


def _has_data(df: pd.DataFrame | None, *required_columns: str) -> bool:
    if df is None or df.empty:
        return False
    return all(col in df.columns for col in required_columns)


def line_chart(
    df: pd.DataFrame, x: str, y: str, title: str, x_label: str, y_label: str,
    y_is_percent: bool = False, y_is_currency: bool = False,
) -> go.Figure | None:
    if not _has_data(df, x, y):
        return None
    fig = px.line(df, x=x, y=y, markers=True, title=title)
    fig.update_traces(hovertemplate=f"%{{x}}<br>{y_label}: %{{y:,.2f}}<extra></extra>")
    fig.update_layout(**_LAYOUT_DEFAULTS, xaxis_title=x_label, yaxis_title=y_label)
    if y_is_percent:
        fig.update_yaxes(ticksuffix="%")
    if y_is_currency:
        fig.update_yaxes(tickprefix="$")
    return fig


def multi_line_chart(df: pd.DataFrame, x: str, y_columns: list[str], labels: dict[str, str], title: str, x_label: str, y_label: str) -> go.Figure | None:
    if df is None or df.empty or x not in df.columns:
        return None
    present = [c for c in y_columns if c in df.columns]
    if not present:
        return None
    fig = go.Figure()
    for column in present:
        fig.add_trace(go.Scatter(x=df[x], y=df[column], mode="lines+markers", name=labels.get(column, column)))
    fig.update_layout(**_LAYOUT_DEFAULTS, title=title, xaxis_title=x_label, yaxis_title=y_label)
    return fig


def horizontal_bar_chart(
    df: pd.DataFrame, category: str, value: str, title: str, x_label: str, y_label: str,
    top_n: int | None = None, value_is_currency: bool = False, value_is_percent: bool = False,
) -> go.Figure | None:
    if not _has_data(df, category, value):
        return None
    plot_df = df.sort_values(value, ascending=True)
    if top_n:
        plot_df = plot_df.tail(top_n)
    fig = px.bar(plot_df, x=value, y=category, orientation="h", title=title)
    hover_fmt = "$%{x:,.0f}" if value_is_currency else ("%{x:,.1f}%" if value_is_percent else "%{x:,.0f}")
    fig.update_traces(hovertemplate=f"%{{y}}<br>{y_label}: {hover_fmt}<extra></extra>")
    fig.update_layout(**_LAYOUT_DEFAULTS, xaxis_title=x_label, yaxis_title=y_label)
    if value_is_currency:
        fig.update_xaxes(tickprefix="$")
    if value_is_percent:
        fig.update_xaxes(ticksuffix="%")
    return fig


def grouped_bar_chart(df: pd.DataFrame, category: str, value: str, group: str, title: str, x_label: str, y_label: str) -> go.Figure | None:
    if not _has_data(df, category, value, group):
        return None
    fig = px.bar(df, x=category, y=value, color=group, barmode="group", title=title)
    fig.update_layout(**_LAYOUT_DEFAULTS, xaxis_title=x_label, yaxis_title=y_label)
    return fig


def distribution_histogram(df: pd.DataFrame, column: str, title: str, x_label: str, nbins: int = 20) -> go.Figure | None:
    if not _has_data(df, column) or df[column].dropna().empty:
        return None
    fig = px.histogram(df.dropna(subset=[column]), x=column, nbins=nbins, title=title)
    fig.update_layout(**_LAYOUT_DEFAULTS, xaxis_title=x_label, yaxis_title="Count")
    return fig


def heatmap(df: pd.DataFrame, x: str, y: str, z: str, title: str, x_label: str, y_label: str) -> go.Figure | None:
    if not _has_data(df, x, y, z):
        return None
    pivot = df.pivot_table(index=y, columns=x, values=z, aggfunc="sum", fill_value=0)
    if pivot.empty:
        return None
    fig = px.imshow(
        pivot, aspect="auto", color_continuous_scale="Blues",
        labels=dict(x=x_label, y=y_label, color="Encounters"),
        title=title,
    )
    fig.update_layout(**{k: v for k, v in _LAYOUT_DEFAULTS.items() if k != "colorway"})
    return fig


def donut_chart(df: pd.DataFrame, names: str, values: str, title: str) -> go.Figure | None:
    """Used sparingly -- composition breakdowns with few categories only (e.g. payer coverage split)."""
    if not _has_data(df, names, values):
        return None
    fig = px.pie(df, names=names, values=values, hole=0.55, title=title)
    fig.update_traces(textinfo="percent+label", hovertemplate="%{label}: %{value:,.0f} (%{percent})<extra></extra>")
    fig.update_layout(**{k: v for k, v in _LAYOUT_DEFAULTS.items()})
    return fig


def scatter_chart(df: pd.DataFrame, x: str, y: str, title: str, x_label: str, y_label: str, hover_name: str | None = None) -> go.Figure | None:
    if not _has_data(df, x, y):
        return None
    fig = px.scatter(df, x=x, y=y, hover_name=hover_name, title=title)
    fig.update_layout(**_LAYOUT_DEFAULTS, xaxis_title=x_label, yaxis_title=y_label)
    return fig
