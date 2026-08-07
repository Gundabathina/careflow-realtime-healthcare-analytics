# Contributing to CareFlow Analytics

This is a portfolio project, but it's built and tested like a real
production codebase. This guide covers environment setup, conventions,
and how to extend each layer safely.

## Environment setup

- **Main project code** (`src/`, `scripts/`, most of `tests/`): Python
  3.14 (or any 3.x with the pins in `requirements.txt`), `pip install -r
  requirements.txt`.
- **dbt**: isolated `.venv-dbt` (Python 3.11) -- see
  [`docs/dbt_analytics_guide.md`](docs/dbt_analytics_guide.md). Never
  install dbt into the main environment.
- **Airflow**: isolated `.venv-airflow` (Python 3.11, test-only) +
  Docker Compose for the actual scheduler/webserver -- see
  [`docs/airflow_orchestration_guide.md`](docs/airflow_orchestration_guide.md).
- **Streamlit dashboard**: isolated `.venv-dashboard` (Python 3.11) --
  see [`docs/dashboard_guide.md`](docs/dashboard_guide.md). streamlit's
  `pyarrow<25` requirement conflicts with the main project's
  `pyarrow>=25.0` pin; never install streamlit into the main environment.

Copy `.env.example` to `.env` and set real values for anything beyond
local development. Never commit `.env`.

## Opening issues and pull requests

Use the provided templates -- GitHub applies them automatically:
[bug report](.github/ISSUE_TEMPLATE/bug_report.md),
[feature request](.github/ISSUE_TEMPLATE/feature_request.md), and the
[pull request template](.github/PULL_REQUEST_TEMPLATE.md) (test output
and the security/PII checklist are required there, not optional).

## Branch naming

`<type>/<short-description>`, e.g. `feature/provider-utilization-page`,
`fix/imaging-grain-key`, `docs/readme-update`. Types: `feature`, `fix`,
`docs`, `refactor`, `test`, `chore`.

## Testing expectations

- Every change ships with tests. Run the relevant targeted file first,
  then the full suite before considering a change done:
  ```bash
  PYTHONPATH=src python3 -m pytest -q tests/test_<relevant_file>.py
  PYTHONPATH=src python3 -m pytest -q tests/
  ```
- Tests must not require a live PostgreSQL server, running Airflow
  scheduler, or Streamlit server unless explicitly testing integration
  (and even then, mock what you can -- see the existing test files for
  the established pattern of monkeypatched database calls).
- New SQL must be parameterized (`%s` placeholders via psycopg) --
  never string-interpolated user/filter input.

## Code style

- No comments explaining *what* code does (identifiers should already
  make that clear) -- only *why*, when it's genuinely non-obvious (a
  workaround, an invariant, a subtle bug fix).
- Don't add error handling for scenarios that can't happen; validate
  only at real system boundaries.
- Prefer editing existing files/patterns over introducing new
  abstractions -- match the style already established in the layer
  you're touching.

## Commit practices

- One logical change per commit; a clear, present-tense summary line.
- Never commit `.env`, real credentials, or generated caches (`__pycache__`,
  `.pytest_cache`, `target/`, `dbt_packages/`, `logs/`).
- Never use `--no-verify` to skip hooks, or force-push to a shared branch.

## Adding a new transformation (Bronze/Silver/Gold)

1. Add the new field/dataset handling in the relevant `src/careflow/<layer>/`
   module -- follow the existing checksum-based incremental pattern.
2. Update the layer's manifest schema if the new field needs to be tracked.
3. Add a data quality check if the field has integrity constraints.
4. Add/update tests in `tests/test_<layer>_*.py`.
5. Update the layer's guide in `docs/`.

## Adding a new dbt model

1. Follow the existing layer convention: staging (1:1 from a source,
   explicit columns, no PII) -> intermediate (business logic) -> marts
   (consumer-facing).
2. Add `data_tests:` (not the deprecated `tests:` key) for every
   primary/natural key and any business-rule invariant.
3. If the model exposes patient data, run it past the restricted-PII
   token list (`dbt/macros/pii_guard.sql`) before it's public.
4. Document every model/column in the relevant `schema.yml`.
5. Run `PYTHONPATH=src python3 scripts/run_dbt.py build` and confirm
   the new tests pass.

## Adding a new dashboard query

1. Add the query function to `dashboard/queries.py` -- never write raw
   SQL directly inside a page file.
2. Use `%s` placeholders and pass values as parameters, always.
3. If the query touches patient-level data, confirm the result columns
   pass `dashboard.database.assert_no_restricted_columns`.
4. Add a test in `tests/test_dashboard_queries.py` covering the
   parameterization and, if relevant, an empty-result case.
5. Wire it into a page via `dashboard/components/charts.py`, handling
   the `None`/empty-DataFrame case.

## Security rules

- PostgreSQL/Airflow credentials: environment variables only, read via
  `careflow.warehouse.postgres_client.load_connection_config()` -- never
  a second, divergent credential-loading path.
- Never log a raw credential or unsanitized driver exception.
- Never expose SSN, passport, driver's license, patient name, street
  address, or precise latitude/longitude in a public model, dashboard
  page, or export.
- See [`docs/security.md`](docs/security.md) for the full policy.
