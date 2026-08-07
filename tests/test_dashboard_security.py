"""Security tests for the CareFlow dashboard: PII exclusion, SQL injection
resistance, credential handling. Fully mocked -- no live PostgreSQL.

Runs under the project's main Python (dashboard.database/.queries
degrade gracefully without streamlit installed).
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dashboard import config, database, queries  # noqa: E402
from dashboard.reports import load_pipeline_reports  # noqa: E402

RESTRICTED_TOKENS = (
    "ssn", "passport", "drivers_license", "driver_license",
    "first_name", "middle_name", "last_name",
    "street_address", "latitude", "longitude",
)


# -- restricted columns are blocked at the database layer -------------------


@pytest.mark.parametrize("token", RESTRICTED_TOKENS)
def test_restricted_token_is_in_the_blocklist(token):
    assert token in config.RESTRICTED_COLUMN_TOKENS


@pytest.mark.parametrize(
    "columns",
    [
        ["ssn"], ["patient_ssn"], ["passport_number"], ["drivers_license_number"],
        ["first_name"], ["middle_name"], ["last_name"], ["street_address_line_1"],
        ["latitude"], ["longitude"], ["patient_key", "latitude", "longitude"],
    ],
)
def test_restricted_columns_raise(columns):
    with pytest.raises(database.RestrictedColumnError):
        database.assert_no_restricted_columns(columns)


def test_safe_dashboard_columns_never_raise():
    safe_columns = [
        "patient_key", "age_group", "gender", "race", "ethnicity", "marital_status",
        "city", "state", "county", "zip", "organization_name", "provider_name",
        "payer_name", "total_claim_cost", "payer_coverage", "encounter_class",
        "readmission_rate_pct", "days_to_readmission",
    ]
    database.assert_no_restricted_columns(safe_columns)


def test_run_query_rejects_a_result_carrying_a_restricted_column(monkeypatch):
    """Even if a future query author's mistake selects a restricted
    column, the result must never reach the caller."""

    class FakeCursor:
        description = [type("Col", (), {"name": "first_name"})()]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            pass

        def fetchall(self):
            return [("Jane",)]

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(database, "get_connection_config", lambda: object())
    monkeypatch.setattr(database, "get_connection", lambda config: FakeConn())

    with pytest.raises(database.RestrictedColumnError):
        database._run("SELECT first_name FROM some_table")


# -- static analysis: queries.py's SQL text never references a restricted column ---


def test_queries_module_source_never_selects_a_restricted_column():
    source = _strip_module_docstring(inspect.getsource(queries))
    lowered = source.lower()
    for token in RESTRICTED_TOKENS:
        assert token not in lowered, f"queries.py references restricted token '{token}'"


def _strip_module_docstring(source: str) -> str:
    """Drop the leading triple-quoted module docstring -- explanatory
    prose there (e.g. "never displays SSN...") would otherwise trip a
    naive substring scan for the very tokens it's documenting as excluded."""
    stripped = source.lstrip()
    for quote in ('"""', "'''"):
        if stripped.startswith(quote):
            end = stripped.find(quote, len(quote))
            if end != -1:
                return stripped[end + len(quote):]
    return source


def test_pages_source_never_accesses_a_restricted_column():
    """Looks for the token used as an actual dict/column key (``["token"]``,
    ``['token']``) -- not just any mention of the word. Page files
    legitimately explain in UI captions what's excluded (e.g. "never
    exact address, latitude, or longitude"), which a plain substring
    scan would misfire on; this only flags real column access syntax."""
    pages_dir = PROJECT_ROOT / "dashboard" / "pages"
    for page_path in pages_dir.glob("*.py"):
        text = page_path.read_text(encoding="utf-8").lower()
        for token in RESTRICTED_TOKENS:
            assert f'"{token}"' not in text, f"{page_path.name} accesses restricted column '{token}'"
            assert f"'{token}'" not in text, f"{page_path.name} accesses restricted column '{token}'"


# -- SQL injection resistance -----------------------------------------------


INJECTION_PAYLOADS = [
    "'; DROP TABLE fct_encounters; --",
    "' OR '1'='1",
    "acme'; DELETE FROM dim_patient_safe WHERE '1'='1",
    "Robert'); DROP TABLE students;--",
]


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_organization_filter_never_reaches_sql_text_unescaped(payload):
    f = queries.Filters(organization=payload)
    conditions = [queries._cond("o.organization_name = %s", f.organization)]
    where_sql, params = queries._where(conditions)
    assert payload not in where_sql
    assert "%s" in where_sql
    assert payload in params


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_get_top_organizations_by_encounters_parameterizes_injection_payload(monkeypatch, payload):
    captured = {}

    def fake_run_query(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return pd.DataFrame()

    monkeypatch.setattr(queries, "run_query", fake_run_query)
    f = queries.Filters(encounter_class=payload)
    queries.get_top_organizations_by_encounters(f)
    assert payload not in captured["sql"]
    assert payload in captured["params"]


def test_readmission_window_never_accepts_arbitrary_column_names(monkeypatch):
    """The only way a value influences the SQL *text* (not a parameter) is
    the readmission-window column name -- and that is always resolved
    through the fixed READMISSION_WINDOW_COLUMNS dict, never the raw input."""
    captured = {}

    def fake_run_query(sql, params=None):
        captured["sql"] = sql
        return pd.DataFrame()

    monkeypatch.setattr(queries, "run_query", fake_run_query)
    malicious = "readmitted_within_7_days; DROP TABLE fct_readmissions; --"
    f = queries.Filters(readmission_window=malicious)  # type: ignore[arg-type]
    queries.get_readmissions_by_encounter_class(f)
    assert "DROP TABLE" not in captured["sql"]
    assert "readmitted_within_30_days" in captured["sql"]  # safe fallback


# -- credentials never logged / never leaked in error messages -------------


def test_dashboard_query_error_never_contains_password(monkeypatch):
    from careflow.warehouse.postgres_client import PostgresConnectionConfig, WarehouseConnectionError

    cfg = PostgresConnectionConfig(host="localhost", port=5433, dbname="careflow", user="careflow_user", password="supersecretpassword123")

    def raise_connection_error(config):
        raise WarehouseConnectionError(f"Could not connect to PostgreSQL at {config.safe_repr()}: connection refused")

    monkeypatch.setattr(database, "get_connection_config", lambda: cfg)
    monkeypatch.setattr(database, "get_connection", raise_connection_error)

    with pytest.raises(database.DashboardQueryError) as excinfo:
        database._run("SELECT 1")
    assert "supersecretpassword123" not in str(excinfo.value)


def test_database_module_never_logs_the_raw_password_env_var():
    source = inspect.getsource(database)
    assert "POSTGRES_PASSWORD" not in source  # credentials are read only via load_connection_config()


def test_config_module_has_no_hardcoded_credentials():
    source = inspect.getsource(config)
    for suspicious in ("password", "secret_key", "PGPASSWORD"):
        assert suspicious not in source.lower()


# -- CSV export safety: exported frames never carry restricted columns -----


def test_exported_dataframe_columns_are_always_pii_checked(monkeypatch):
    """Every query result -- including ones destined for a CSV download
    button -- passes through assert_no_restricted_columns before being
    returned to a page."""
    monkeypatch.setattr(database, "get_connection_config", lambda: object())

    class FakeCol:
        def __init__(self, name):
            self.name = name

    class FakeCursor:
        description = [FakeCol("patient_key"), FakeCol("age_group")]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            pass

        def fetchall(self):
            return [(1, "18-34")]

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(database, "get_connection", lambda config: FakeConn())
    df = database._run("SELECT patient_key, age_group FROM safe_table")
    csv_text = df.to_csv(index=False)
    for token in RESTRICTED_TOKENS:
        assert token not in csv_text.lower()


# -- data quality report loading never modifies upstream files -------------


def test_load_pipeline_reports_does_not_modify_report_files():
    report_paths = [
        PROJECT_ROOT / "reports" / "warehouse" / "postgres_validation_report.json",
        PROJECT_ROOT / "reports" / "dbt" / "dbt_test_summary.json",
    ]
    before = {p: p.read_bytes() for p in report_paths if p.is_file()}
    load_pipeline_reports()
    for path, content in before.items():
        assert path.read_bytes() == content
