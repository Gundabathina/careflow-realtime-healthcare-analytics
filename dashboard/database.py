"""PostgreSQL access layer for the CareFlow Analytics dashboard.

Reuses careflow.warehouse.postgres_client for credential loading and
error sanitization -- the same connection logic every other CareFlow
component uses, never a second, divergent way of reading POSTGRES_*
environment variables. Connections are opened per-query and always
closed via a context manager; nothing is held open across Streamlit
reruns.

Streamlit is optional at import time (wrapped in try/except) so this
module -- and queries.py, which depends on it -- stay importable and
testable in an environment without streamlit installed (the project's
main Python does not have streamlit; see docs/dashboard_guide.md for
why it lives in an isolated .venv-dashboard instead).
"""

from __future__ import annotations

from typing import Any, Callable, TypeVar

import pandas as pd

from dashboard.config import (
    FILTER_OPTIONS_CACHE_TTL_SECONDS,
    QUERY_CACHE_TTL_SECONDS,
    RESTRICTED_COLUMN_TOKENS,
)

try:
    import streamlit as st

    _HAS_STREAMLIT = True
except ImportError:  # pragma: no cover - exercised only without streamlit installed
    _HAS_STREAMLIT = False

from careflow.warehouse.postgres_client import (  # noqa: E402
    MissingCredentialsError,
    WarehouseConnectionError,
    get_connection,
    load_connection_config,
)

_F = TypeVar("_F", bound=Callable[..., Any])


def _cache_resource(func: _F) -> _F:
    if _HAS_STREAMLIT:
        return st.cache_resource(func)  # type: ignore[return-value]
    return func


def _cache_data(ttl: int) -> Callable[[_F], _F]:
    def decorator(func: _F) -> _F:
        if _HAS_STREAMLIT:
            return st.cache_data(ttl=ttl, show_spinner=False)(func)  # type: ignore[return-value]
        return func

    return decorator


class DashboardQueryError(Exception):
    """A sanitized, user-facing error -- never includes credentials or raw driver text."""


class RestrictedColumnError(Exception):
    """Raised when a query result would expose a column on the restricted-PII list."""


def assert_no_restricted_columns(columns: list[str]) -> None:
    """Defense-in-depth: block any query result carrying a restricted-PII-looking column.

    The dbt layer already enforces this for every public mart (Phase 3C's
    no_restricted_pii_in_public_models test); this is a second,
    independent check specific to the dashboard, so a future query
    author's mistake fails loudly here rather than silently rendering
    PII in the UI.
    """
    lowered = [c.lower() for c in columns]
    for token in RESTRICTED_COLUMN_TOKENS:
        for column in lowered:
            if token in column:
                raise RestrictedColumnError(f"Query result column '{column}' matches restricted PII token '{token}'")


@_cache_resource
def get_connection_config():
    """Load PostgreSQL connection settings once per Streamlit session (cached resource).

    Raises MissingCredentialsError if required POSTGRES_* env vars are
    absent -- never falls back to a guessable default.
    """
    return load_connection_config()


def _run(sql: str, params: tuple | dict | None = None) -> pd.DataFrame:
    """Execute one parameterized, read-only query and return a DataFrame.

    Always opens and closes its own connection via a context manager.
    Any failure is re-raised as DashboardQueryError with a sanitized
    message -- WarehouseConnectionError's own sanitization already
    strips credentials/DSNs; this adds a stable, generic wrapper so a
    caller never has to know psycopg's exception types.
    """
    try:
        config = get_connection_config()
    except MissingCredentialsError as exc:
        raise DashboardQueryError("PostgreSQL is not configured (missing environment variables).") from exc

    try:
        with get_connection(config) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                columns = [desc.name for desc in cur.description] if cur.description else []
                rows = cur.fetchall()
    except WarehouseConnectionError as exc:
        raise DashboardQueryError(f"Could not reach the CareFlow warehouse: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - never leak a raw driver exception to the UI
        raise DashboardQueryError("The query could not be completed. See server logs for details.") from exc

    assert_no_restricted_columns(columns)
    return pd.DataFrame(rows, columns=columns)


@_cache_data(ttl=QUERY_CACHE_TTL_SECONDS)
def run_query(sql: str, params: tuple | dict | None = None) -> pd.DataFrame:
    """Cached entry point for analytical queries (KPIs, chart data, tables)."""
    return _run(sql, params)


@_cache_data(ttl=FILTER_OPTIONS_CACHE_TTL_SECONDS)
def run_filter_query(sql: str, params: tuple | dict | None = None) -> pd.DataFrame:
    """Separate cache bucket (longer TTL) for sidebar filter option lists."""
    return _run(sql, params)


def check_database_available() -> tuple[bool, str | None]:
    """Return (True, None) if the warehouse is reachable, else (False, sanitized_reason)."""
    try:
        get_connection_config()
    except MissingCredentialsError:
        return False, "PostgreSQL is not configured (missing environment variables)."
    try:
        _run("SELECT 1")
        return True, None
    except DashboardQueryError as exc:
        return False, str(exc)
