# CareFlow Analytics Dashboard Guide (Phase 5A)

An interactive Streamlit dashboard on top of the existing PostgreSQL/dbt
analytics layer -- Executive, Readmission, Operations, Financial,
Provider, Patient Population, and Data Quality views. This phase is
dashboard only -- no machine learning, Kafka, cloud deployment, or CI/CD.

## Data source

Every query reads from `careflow_dbt_mart` (the dbt reporting layer,
Phase 3C) via `dashboard/queries.py`. The dashboard never queries Raw,
Bronze, or Silver data, and never writes to the warehouse. Page 7 (Data
Quality) is the one exception to "SQL only" -- it reads the existing
JSON/CSV pipeline reports already written by every other phase
(`dashboard/reports.py`), never re-running a check itself.

## Why streamlit lives in its own environment

Streamlit currently requires `pyarrow<25`, which would downgrade the
project's pinned `pyarrow>=25.0` (used throughout Bronze/Silver/Gold) if
installed into the main Python environment. Exactly like `.venv-dbt`
(Phase 3C) and `.venv-airflow` (Phase 4A), streamlit and plotly live in
an isolated `.venv-dashboard` instead:

```bash
/opt/homebrew/bin/python3.11 -m venv .venv-dashboard
.venv-dashboard/bin/pip install streamlit plotly "psycopg[binary]>=3.3" pandas pyyaml pytest
```

The project's main Python 3.14 environment, `.venv-dbt`, and
`.venv-airflow` are all untouched.

## Running the dashboard

```bash
set -a && source .env && set +a
PYTHONPATH=src python3 scripts/start_dashboard.py
# or directly:
.venv-dashboard/bin/streamlit run dashboard/app.py
```

URL: <http://localhost:8501> by default (`--port` to override).
`scripts/start_dashboard.py` checks PostgreSQL connectivity first and
resolves the streamlit binary the same way `scripts/run_dbt.py` resolves
dbt's -- `.venv-dashboard/bin/streamlit` by default, overridable via
`CAREFLOW_STREAMLIT_BIN`.

## Architecture

```
dashboard/
  config.py        -- static settings (no secrets), color palette, PII token list
  database.py       -- psycopg connection handling, caching, PII defense-in-depth
  queries.py         -- every parameterized SQL query, grouped by page
  formatting.py       -- currency/percent/number formatting, insight calculations (no streamlit/plotly needed)
  reports.py          -- reads existing Bronze/Silver/Gold/Postgres/dbt/Airflow reports for Page 7
  components/
    layout.py          -- page header, section titles, insight panel, empty/error states
    kpi_cards.py         -- st.metric wrapper driven by formatting.py
    filters.py            -- sidebar filters + Reset Filters button
    charts.py              -- Plotly figure builders (line, bar, histogram, heatmap, donut)
  pages/
    1_Executive_Overview.py
    2_Readmission_Analytics.py
    3_Hospital_Operations.py
    4_Financial_Performance.py
    5_Provider_Performance.py
    6_Patient_Population.py
    7_Data_Quality.py
  app.py               -- landing page (Streamlit auto-discovers pages/ for navigation)
```

`database.py` and `queries.py` import streamlit behind a
`try/except ImportError` and fall back to no-op cache decorators when
it's absent -- so the query layer, and everything in
`tests/test_dashboard_queries.py` / `test_dashboard_security.py`, stays
importable and testable under the project's main Python, with no
streamlit dependency at all.

## Filters

The sidebar (`components/filters.py`) exposes date range, organization,
provider, payer, encounter class, age group, gender, race, and
readmission window (7/14/30 days) -- applied "where supported": e.g.
organization/provider/payer filters apply to encounter-level charts,
while age group/gender/race apply to patient- and readmission-level
charts. Every value is drawn from a dropdown populated by
`get_filter_options()` (real distinct values from the database); nothing
free-typed ever reaches a query. **Reset Filters** restores every widget
to its default via `st.session_state`.

## Query safety

Every function in `queries.py` builds SQL with `%s` placeholders and
passes filter values as query parameters -- never string-interpolated.
The one value that ever appears directly in SQL *text* is the
readmission-window column name, and it is resolved through a fixed
dict (`READMISSION_WINDOW_COLUMNS = {7: ..., 14: ..., 30: ...}`), never
the raw filter input. `database.assert_no_restricted_columns` is a
second, independent check applied to every query result's columns
before it's ever rendered or exported -- defense-in-depth alongside the
dbt layer's own `no_restricted_pii_in_public_models` test (Phase 3C).

## Caching

- `@st.cache_resource` on the PostgreSQL connection config (loaded once
  per session).
- `@st.cache_data(ttl=300)` on analytical query results (5 minutes --
  long enough to avoid re-querying on every widget interaction, short
  enough that a fresh Airflow run shows up without a manual cache clear).
- `@st.cache_data(ttl=600)` on sidebar filter option lists (10 minutes;
  these change far less often).

## Data storytelling

`formatting.describe_change()` produces a sentence like *"Emergency
encounters increased 12.4% compared with the previous month"* only when
a genuine current-vs-previous comparison exists in the query result --
never a hard-coded or fabricated conclusion. `formatting.top_row_label()`
identifies the highest-value row in a result (e.g. the organization with
the most encounters) the same way. Every insight in Page 1's Executive
Insights panel is produced this way; the panel renders nothing at all
when there isn't enough data to compare.

## Empty-data and error handling

Every chart-building function in `components/charts.py` returns `None`
when given an empty DataFrame or missing required columns; pages check
for `None` and call `layout.render_empty_state()` instead of crashing.
`database.DashboardQueryError` wraps any database failure with a
sanitized message (never a raw driver exception, never a credential);
pages catch it and call `layout.render_error_state()`.

## Tests

```bash
PYTHONPATH=src python3 -m pytest -q \
    tests/test_dashboard_queries.py tests/test_dashboard_components.py tests/test_dashboard_security.py
```

`test_dashboard_queries.py` and `test_dashboard_security.py` run under
the main Python (no streamlit/plotly needed). `test_dashboard_components.py`
needs streamlit + plotly and is skipped (not failed) under the main
environment via `pytest.importorskip`; run it for real coverage under
`.venv-dashboard`:

```bash
PYTHONPATH=src .venv-dashboard/bin/python -m pytest -q tests/test_dashboard_components.py
```

None of these tests require a live Streamlit server or PostgreSQL --
every database call is monkeypatched, and streamlit widgets execute
safely in "bare mode" outside `streamlit run` (a harmless warning, never
an exception).

## Security

- PostgreSQL credentials are read only via
  `careflow.warehouse.postgres_client.load_connection_config()` -- the
  same mechanism every other CareFlow component uses. `database.py`
  never references `POSTGRES_PASSWORD` directly (verified by
  `test_database_module_never_logs_the_raw_password_env_var`).
  `.streamlit/config.toml` carries no secrets -- presentation settings only.
- Restricted PII (SSN, passport, driver's license, first/middle/last
  name, street address, latitude/longitude) cannot reach the UI: it's
  already excluded from `careflow_dbt_mart` by the dbt layer (Phase 3C),
  and `database.assert_no_restricted_columns` independently blocks any
  query result that somehow carried one anyway.
- CSV downloads are built from the same PII-checked query results as
  the charts and tables on screen -- there is no separate export path
  that could bypass the check.

## Known limitations

- **Average Age** (Patient Population page) is *estimated* from
  age-group bucket midpoints, not an exact age -- `mart_patient_population`
  intentionally exposes only the `age_group` bucket, never a birth date
  or numeric age, consistent with the dbt layer's PII rules.
- **Deceased Patient Count** is reported as unavailable --
  `dim_patient_safe`/`mart_patient_population` carry no `is_deceased`
  column in the public mart.
- Figures throughout are derived from Synthea-generated synthetic data
  and do not represent real hospital performance (noted on every page).

## Power BI preparation

See `docs/powerbi_dashboard_spec.md` and `data/exports/powerbi/*.csv`
for the next phase's starting point -- this phase does not build a
`.pbix` file.
