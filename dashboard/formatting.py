"""Pure formatting and data-storytelling calculation helpers.

Deliberately free of streamlit/plotly imports so this module -- and its
tests -- never require either package. Every "insight" here is a direct
calculation over real query results; nothing is a hard-coded or
AI-generated conclusion.
"""

from __future__ import annotations

from typing import Any


def safe_divide(numerator: Any, denominator: Any) -> float | None:
    """None on a zero/None/NaN denominator -- never a ZeroDivisionError, never a fabricated 0.0."""
    try:
        if numerator is None or denominator is None:
            return None
        denominator = float(denominator)
        if denominator == 0:
            return None
        return float(numerator) / denominator
    except (TypeError, ValueError):
        return None


def format_currency(value: Any, decimals: int = 0) -> str:
    if value is None:
        return "N/A"
    try:
        return f"${float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "N/A"


def format_percent(value: Any, decimals: int = 1, already_pct: bool = False) -> str:
    """``value`` is a fraction (0.234) by default; set already_pct=True if it's already 0-100."""
    if value is None:
        return "N/A"
    try:
        pct = float(value) if already_pct else float(value) * 100
        return f"{pct:,.{decimals}f}%"
    except (TypeError, ValueError):
        return "N/A"


def format_number(value: Any, decimals: int = 0) -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "N/A"


def format_duration_minutes(value: Any) -> str:
    if value is None:
        return "N/A"
    try:
        minutes = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if minutes < 60:
        return f"{minutes:,.0f} min"
    hours = minutes / 60
    return f"{hours:,.1f} hrs"


def pct_change(current: Any, previous: Any) -> float | None:
    """Percentage change from ``previous`` to ``current``, as a fraction (0.124 == +12.4%)."""
    ratio = safe_divide(current, previous)
    if ratio is None or previous is None:
        return None
    try:
        if float(previous) == 0:
            return None
    except (TypeError, ValueError):
        return None
    return ratio - 1.0


def describe_change(label: str, current: Any, previous: Any, unit: str = "%", decimals: int = 1) -> str | None:
    """A single data-driven sentence, or None when there isn't enough data to compare.

    Only ever describes what the numbers show -- e.g. "Emergency
    encounters increased 12.4% compared with the previous month." --
    never a clinical or causal claim.
    """
    change = pct_change(current, previous)
    if change is None:
        return None
    direction = "increased" if change > 0 else "decreased" if change < 0 else "held steady versus"
    magnitude = abs(change) * 100
    if direction == "held steady versus":
        return f"{label} held steady compared with the previous period."
    return f"{label} {direction} {magnitude:.{decimals}f}{unit} compared with the previous period."


def top_row_label(df, label_column: str, value_column: str) -> str | None:
    """The label of the row with the highest value_column, or None if df is empty."""
    if df is None or df.empty or label_column not in df.columns or value_column not in df.columns:
        return None
    non_null = df.dropna(subset=[value_column])
    if non_null.empty:
        return None
    top = non_null.loc[non_null[value_column].idxmax()]
    return str(top[label_column])
