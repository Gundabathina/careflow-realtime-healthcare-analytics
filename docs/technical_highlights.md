# Technical Highlights

A scannable list of the specific engineering decisions and problems
this project involved -- for a hiring manager skimming quickly. For
the full explanation behind any item, follow its link into
[`interview_guide.md`](interview_guide.md) or [`interview_notes.md`](interview_notes.md).

## Bugs found and fixed (with real root causes)

1. **Imaging-study composite-grain bug** -- a fact table that looked
   like it had a simple single-column primary key didn't actually have
   a unique grain at that key. Fixed by identifying the true composite
   natural key and enforcing uniqueness at three independent layers
   (Gold Python code, a dbt singular test, and the PostgreSQL warehouse
   validator) so the same class of bug can't silently regress later.
   [`interview_guide.md#13`](interview_guide.md#13-imaging-study-grain-issue-and-how-it-was-fixed)
2. **Foreign-key violation on repeated force-reloads** -- the
   PostgreSQL warehouse loader's `--force`/`--truncate-and-load` path
   could trip FK constraint violations on a second consecutive run.
   Root-caused to reload ordering, then redesigned as a single
   whole-batch transaction with dependency-ordered clear (marts, then
   facts, then dimensions) and reload (dimensions, then facts, then
   marts) phases -- verified by running the force path back-to-back
   multiple times. [`interview_guide.md#14`](interview_guide.md#14-foreign-key-force-reload-problem-and-how-it-was-fixed)
3. **Docker/Airflow service-readiness race** -- a startup-ordering
   issue between dependent Docker Compose services, fixed with proper
   healthcheck-gated dependencies. [`interview_guide.md#15`](interview_guide.md#15-dockerairflow-readiness-issue-and-how-it-was-fixed)

## Correctness guarantees, not just tests

- **Independent dual-implementation reconciliation** -- 7/14/30-day
  patient readmissions and monthly financial totals are computed twice:
  once in Python during Gold modeling, once in SQL during dbt's
  intermediate layer. Three dedicated dbt singular tests
  (`reconcile_readmission_counts_with_python_gold`,
  `reconcile_monthly_encounter_totals`,
  `reconcile_financial_totals_with_tolerance`) fail the build if the
  two computations disagree beyond an explicit numeric tolerance --
  catching a bug in *either* implementation, not just one.
- **172 warehouse-vs-Gold validation checks** compare live PostgreSQL
  state back against Gold's own Parquet output after every load --
  schema existence, row counts, primary keys, foreign-key orphans,
  date-key resolution, currency reconciliation, KPI value matching.
- **229 Silver + 119 Gold data-quality checks**, run and reported at
  every build, including honestly-surfaced failures (not filtered out
  before display).

## Idempotency and incremental processing, engineered deliberately

- Every pipeline stage (Silver, Gold, PostgreSQL load) can be safely
  re-run; each uses checksum or dependency-signature comparison to
  skip unchanged data rather than reprocessing blindly.
- Every stage also has an explicit `--force`/`--truncate-and-load`
  override for a full reprocess, and both paths are covered by tests
  that run the pipeline twice and assert identical results.
- Airflow DAGs mirror this: `careflow_daily_analytics` never
  regenerates synthetic data and completes as a fast no-op when
  nothing has changed; `careflow_end_to_end` exposes every force flag
  as an explicit run parameter rather than hard-coding behavior.

## Security and PII discipline, enforced at 3 independent layers

1. **Gold transformation** -- restricted columns (SSN, passport,
   driver's license, name, street address, precise lat/long) are never
   written into any public-facing table.
2. **dbt** -- `dbt/macros/pii_guard.sql` checks every public model
   against the restricted-token list as an automated test.
3. **Dashboard** -- `dashboard.database.assert_no_restricted_columns`
   independently re-checks every query result before it can render,
   so a mistake anywhere upstream still can't reach a page.

Every SQL query in the dashboard and warehouse loader uses
parameterized queries (`%s` placeholders via psycopg) -- never
string-interpolated filter/user input. Every driver exception is
sanitized before logging (`careflow.warehouse.postgres_client._sanitize_error`
and equivalents in `scripts/run_dbt.py`,
`airflow/plugins/careflow_operators.py`) so credentials can't leak into
logs or error messages.

## Environment isolation, done deliberately rather than avoided

Three tools (dbt, Airflow, Streamlit) have Python-version or dependency
constraints that conflict with the main project's Python 3.14 /
`pyarrow>=25.0` pins. Rather than compromising the main environment or
skipping the incompatible tools, each runs in its own isolated
virtualenv (`.venv-dbt`, `.venv-airflow`, `.venv-dashboard`), verified
independently by its own test run -- see
[`architecture/technology_stack.md`](architecture/technology_stack.md).

## Test coverage, by layer

804 tests across 22 files -- ingestion, transformation (Silver/Gold),
warehouse loading, dbt project structure, Airflow DAG structure and
callbacks, dashboard queries/security, and repository-quality checks
(README links, no committed secrets, no fabricated artifacts). See
[`project_metrics.md`](project_metrics.md) for the exact breakdown and
regeneration commands.

## See also

- [`project_summary.md`](project_summary.md) -- one-page overview
- [`interview_guide.md`](interview_guide.md) -- full depth on every item above
- [`architecture/architecture.md`](architecture/architecture.md) -- system diagram
