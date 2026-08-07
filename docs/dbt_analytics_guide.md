# dbt Analytics Engineering Guide (Phase 3C)

dbt sits on top of the existing PostgreSQL warehouse (Phase 3B) to add
governed, tested, documented analytics models. This phase is dbt only —
no Kafka, Airflow, dashboards, machine learning, or cloud deployment.

The PostgreSQL warehouse remains the source of truth. dbt never
duplicates or replaces the Python ingestion pipeline (`scripts/load_postgres_warehouse.py`);
it only reads from `careflow_dim`, `careflow_fact`, and `careflow_mart`
and writes new, separate schemas (`careflow_dbt_staging`,
`careflow_dbt_intermediate`, `careflow_dbt_mart`, plus
`careflow_dbt_seeds`/`careflow_dbt_snapshots`). It never touches
`data/raw`, `data/bronze`, `data/silver`, or Gold Parquet files at all —
dbt only ever talks to PostgreSQL.

## Environment: an isolated dbt toolchain, never the project's Python 3.14

dbt-core has no supported build for Python 3.14 (the project's main
environment). Rather than risk breaking that environment, dbt lives in
its own virtualenv, built once with Homebrew's Python 3.11:

```bash
/opt/homebrew/bin/python3.11 -m venv .venv-dbt
.venv-dbt/bin/pip install "dbt-core==1.8.9" "dbt-postgres==1.8.2"
```

`dbt-core` must be pinned explicitly alongside `dbt-postgres` — resolving
`dbt-postgres` alone can pull in a pre-release `dbt-core` as an unpinned
transitive dependency. `.venv-dbt/` is gitignored and never installed
into the project's main `.venv/`. Every dbt command in this guide and in
`scripts/run_dbt.py` runs through `.venv-dbt/bin/dbt`.

## Connection: environment variables only, local port included

The local PostgreSQL instance runs on port 5433 (5432 is occupied by
another project on this machine). `profiles.yml.example` reads every
connection parameter — including the port — via dbt's `env_var()`, so
the port is never hard-coded anywhere in a committed file:

```yaml
port: "{{ env_var('POSTGRES_PORT') | int }}"
```

Before running any dbt command:

```bash
set -a && source .env && set +a
```

`profiles.yml` (the real file dbt reads) is a copy of `profiles.yml.example`
— it holds only `env_var()` references, no literal credentials — and is
still gitignored, never committed. `DBT_PROFILES_DIR` is not required
day-to-day: every invocation (manual or via `scripts/run_dbt.py`) passes
`--profiles-dir` pointing at the repository root explicitly, so dbt never
falls back to `~/.dbt/`. `DBT_TARGET` selects the profile target
(`dev` by default; `ci` is also defined, using schema
`careflow_dbt_mart_ci` for isolated CI runs).

## Running dbt

Either directly:

```bash
set -a && source .env && set +a
.venv-dbt/bin/dbt debug   --project-dir . --profiles-dir .
.venv-dbt/bin/dbt deps    --project-dir . --profiles-dir .
.venv-dbt/bin/dbt seed    --project-dir . --profiles-dir .
.venv-dbt/bin/dbt snapshot --project-dir . --profiles-dir .
.venv-dbt/bin/dbt build   --project-dir . --profiles-dir .
.venv-dbt/bin/dbt docs generate --project-dir . --profiles-dir .
```

Or through the wrapper, which additionally sanitizes logged output,
never uses `shell=True`, exits non-zero on failure, and refreshes the
`reports/dbt/` quality reports after every invocation:

```bash
set -a && source .env && set +a
PYTHONPATH=src python3 scripts/run_dbt.py debug
PYTHONPATH=src python3 scripts/run_dbt.py deps
PYTHONPATH=src python3 scripts/run_dbt.py seed
PYTHONPATH=src python3 scripts/run_dbt.py snapshot
PYTHONPATH=src python3 scripts/run_dbt.py run
PYTHONPATH=src python3 scripts/run_dbt.py test
PYTHONPATH=src python3 scripts/run_dbt.py build
PYTHONPATH=src python3 scripts/run_dbt.py docs-generate
PYTHONPATH=src python3 scripts/run_dbt.py full-refresh   # build --full-refresh
```

## Model layers

**Staging** (`dbt/models/staging/`, 14 models, views, schema
`careflow_dbt_staging`) — one model per warehouse table, named
`stg_careflow__<table>`. Selects explicit columns from `source()` only
(never `SELECT *`, never `ref()`), preserves natural and surrogate keys,
and applies no business logic. `stg_careflow__readmissions` is the one
exception worth noting: it stages `careflow_mart.mart_readmission` (the
Python Gold layer's own readmission output) specifically so dbt's
independently-computed readmissions can be reconciled against it.

**Intermediate** (`dbt/models/intermediate/`, 8 models, tables, schema
`careflow_dbt_intermediate`) — joins, window functions, and aggregation
live here, always via `ref()`. `int_patient_clinical_activity` in
particular pre-aggregates each source table (conditions, procedures,
medications, observations, immunizations, encounters) into its own CTE
before joining one-to-one into the patient grain — joining several
one-to-many tables directly would multiply row counts together
(a genuine bug hit and fixed during this build; see below).
`int_readmission_events` independently recomputes 7/14/30-day
readmissions from `int_encounters_enriched` using `lead()` window
functions, rather than reusing the Python Gold mart, specifically so it
can be reconciled against it.

**Marts** (`dbt/models/marts/`, 14 models, schema `careflow_dbt_mart`) —
consumer-facing facts, PII-safe dimensions, and pre-aggregated marts.
`fct_encounters` is the one incremental model (see below); everything
else is a table.

## PII: what's safe, what's excluded

No public dbt model may expose SSN, passport, driver's license, first/
middle/last name, street address, or precise latitude/longitude. Safe
demographics are `patient_key`, age group, gender, race, ethnicity,
marital status, city, state, county, and ZIP — this project's Synthea
data is synthetic, so ZIP/city/state carry no real-world privacy risk.
`dim_patient_safe` selects exactly this list (deliberately omitting even
`patient_id`). This is enforced three ways:

1. The Gold layer itself already drops SSN/passport/license/name/address
   before the warehouse is ever loaded (Phase 3A) — latitude/longitude
   are the only residual PII, explicitly excluded from every staging and
   mart column list.
2. `dbt/macros/pii_guard.sql` (`restricted_pii_columns()`,
   `assert_no_restricted_pii_columns()`) queries
   `information_schema.columns` for any restricted-PII-sounding column
   name on a given public model.
3. The singular test `no_restricted_pii_in_public_models.sql` calls that
   macro against all 14 public mart models.

## Incremental materialization

Only `fct_encounters` is incremental — most facts here are small enough
that a full table rebuild is simpler and safer than incremental-model
edge cases. It uses `unique_key='encounter_key'`,
`on_schema_change='sync_all_columns'`, and predicates new rows on
`start_timestamp` (there is no separate load timestamp on Gold facts):

```sql
{% if is_incremental() %}
where start_timestamp > (select coalesce(max(start_timestamp), '1900-01-01'::timestamptz) from {{ this }})
{% endif %}
```

`dbt run --full-refresh` (or `scripts/run_dbt.py full-refresh`) forces a
complete rebuild. Both incremental and full-refresh runs are idempotent
— re-running either with unchanged Gold data produces the same row count.

## Tests

**121 generic tests** (`not_null`, `unique`, `relationships`,
`accepted_values`, `dbt_utils.expression_is_true`,
`dbt_utils.unique_combination_of_columns`) declared in each layer's
`schema.yml`, covering surrogate/natural keys, FK resolution, non-negative
durations, `encounter_class` accepted values, monthly-KPI and imaging
grain uniqueness, and more.

**12 singular tests** (`dbt/tests/*.sql`) — a query returning any row is
a failure:

| # | File | Checks |
|---|------|--------|
| 1 | `no_negative_patient_responsibility.sql` | No unflagged negative `patient_responsibility` |
| 2 | `no_encounter_stop_before_start.sql` | `stop_timestamp >= start_timestamp` |
| 3 | `no_self_readmission.sql` | A readmission's next encounter is never itself |
| 4 | `no_negative_readmission_intervals.sql` | `days_to_readmission >= 0` |
| 5 | `readmission_7_day_implies_14_and_30_day.sql` | 7-day flag implies 14- and 30-day flags |
| 6 | `readmission_14_day_implies_30_day.sql` | 14-day flag implies 30-day flag |
| 7 | `reconcile_monthly_encounter_totals.sql` | dbt vs. `fct_encounters` monthly totals match exactly |
| 8 | `reconcile_financial_totals_with_tolerance.sql` | Total claim cost matches within tolerance |
| 9 | `reconcile_readmission_counts_with_python_gold.sql` | dbt's readmission counts vs. Python Gold's `mart_readmission` |
| 10 | `no_restricted_pii_in_public_models.sql` | No restricted PII column on any of the 14 public marts |
| 11 | `imaging_study_composite_grain_is_unique.sql` | `(study_id, series_uid, instance_uid)` is unique — **`study_id` alone is deliberately never tested as unique**, it isn't |
| 12 | `no_unexpected_orphan_dimension_keys.sql` | Every non-null FK on `fact_encounter` resolves to its dimension |

## Reconciliation

Three of the singular tests above (7, 8, 9) compare dbt's independently
computed figures against the Python Gold layer, using explicit numeric
tolerances declared as dbt `vars` in `dbt_project.yml`:

- `count_reconciliation_tolerance` (default `0`) — encounter/patient/
  readmission counts must match exactly.
- `currency_reconciliation_tolerance` (default `0.01`) — dollar totals
  may differ by at most one cent (floating-point aggregation).

A discrepancy beyond tolerance fails the test — it is never hidden or
silently rounded away. `reports/dbt/dbt_reconciliation_report.json`
records each check's outcome after every `scripts/run_dbt.py` invocation
that produces `run_results.json`.

## Seeds and snapshots

**Seeds** (`dbt/seeds/`, schema `careflow_dbt_seeds`) are small,
hand-maintained config only — accepted encounter classes, readmission
window definitions, age-group sort order, and KPI definitions (mirroring
`kpi_calculator.py`'s KPI set). Never a duplicate of a large dataset.

**Snapshots** (`dbt/snapshots/`, schema `careflow_dbt_snapshots`) track
slowly-changing dimension history for provider specialty/organization,
payer ownership, and organization utilization/revenue — using the
`check` strategy, since none of these warehouse dimensions carries a
reliable `updated_at` column. Facts are never snapshotted.

## Documentation, groups, and exposures

Every model and most columns carry a `description:`. Three `groups` —
`staging`, `intermediate`, `marts` — each declare an owner in
`dbt/models/schema.yml`. Four `exposures` describe planned (**not yet
built**) downstream dashboards — Executive Operations, Readmission,
Financial Performance, Provider Performance — each pointing at the marts
it will consume, so `dbt docs generate`'s lineage graph already shows
where this data is headed.

## Bugs found and fixed during this build

- **Combinatorial join fan-out**: `int_patient_clinical_activity`
  originally joined six one-to-many tables directly in one query; row
  counts multiplied together and the query hung for minutes against a
  real (if small) warehouse. Fixed by pre-aggregating each source table
  into its own CTE before joining, so every join is 1:1 on `patient_key`.
- **Overlapping encounters produce negative "days between"**: Synthea's
  real data has some back-to-back/overlapping encounters where the next
  encounter starts before the previous one's recorded discharge.
  `int_patient_encounter_history` and `int_readmission_events` both null
  out the "days between" calculation rather than emit a negative number
  in that case — the same treatment already established for this
  phenomenon in the Python Gold layer (Phase 3A).
- **`generate_schema_name` group/exposure ordering**: `+group: staging`
  in `dbt_project.yml` failed to parse until a `groups:` block declaring
  `staging`/`intermediate`/`marts` existed — added in
  `dbt/models/schema.yml`, which also became the home for the 4 exposures.

## Reports

`scripts/run_dbt.py` regenerates four files in `reports/dbt/` after every
invocation that leaves a `target/run_results.json` behind:

- `dbt_run_summary.json` — invocation ID, dbt version, target, per-model
  status/duration, and a best-effort live row count per model (a direct
  `SELECT COUNT(*)`, skipped gracefully if PostgreSQL is unreachable).
- `dbt_test_summary.json` — total tests and pass/warn/fail/skipped counts.
- `dbt_reconciliation_report.json` — the 3 reconciliation checks, their
  outcome, and the configured tolerances.
- `dbt_model_inventory.csv` — every model/seed/snapshot: layer, schema,
  materialization, group, tags, and whether it's flagged as containing PII.

## What's next

Phase 3C stops here: dbt on top of the existing warehouse, fully tested
and documented. Orchestration (running the Python pipeline and dbt on a
schedule or via Airflow), dashboards, and machine learning are explicitly
out of scope for this phase.
