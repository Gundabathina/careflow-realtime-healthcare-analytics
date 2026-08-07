# PostgreSQL Analytics Warehouse Guide (Phase 3B)

CareFlow Analytics loads the Gold star schema into a local, reproducible
PostgreSQL warehouse via Docker Compose. This phase is warehousing only —
no dbt, Kafka, Airflow, dashboards, or machine learning.

Reads only from `data/gold/`. Writes database objects only to
PostgreSQL. Never modifies `data/raw`, `data/bronze`, `data/silver`, or
Gold Parquet files.

## Starting PostgreSQL

```bash
cp .env.example .env   # then edit POSTGRES_PASSWORD
PYTHONPATH=src python3 scripts/start_postgres.py
```

This runs `docker compose up -d postgres` and waits for the container's
health check. `docker-compose.yml` requires `POSTGRES_PASSWORD` to be set
in the environment — Compose refuses to start otherwise, so a real
deployment can never silently fall back to a guessable default. The
`.env.example` password is a **local-development-only** placeholder;
change it for anything beyond that.

If port 5432 is already in use on your machine (common if another
project's Postgres is running), set `POSTGRES_PORT` in `.env` to a free
port — `docker-compose.yml` reads it via `${POSTGRES_PORT:-5432}`.

## Loading the warehouse

```bash
PYTHONPATH=src python3 scripts/load_postgres_warehouse.py
PYTHONPATH=src python3 scripts/load_postgres_warehouse.py --force
PYTHONPATH=src python3 scripts/load_postgres_warehouse.py --table dim_patient --table fact_encounter
PYTHONPATH=src python3 scripts/load_postgres_warehouse.py --schema-only
```

The loader always begins by applying `config/postgres_schema.sql`,
`postgres_indexes.sql`, and `postgres_views.sql` (all idempotent), then
loads tables in dimension → fact → mart order. Each table is:

1. Read from its Gold Parquet file.
2. Prepared: any foreign-key column carrying Gold's `-1` "unresolved"
   sentinel becomes SQL `NULL` (see **Unknown-member strategy** below).
3. Bulk-loaded via `COPY` into a fresh `UNLOGGED` staging table (never
   row-by-row `INSERT`).
4. Row-count validated against the staged data.
5. Swapped into the target table with `DELETE FROM target` +
   `INSERT ... SELECT FROM staging`, inside one transaction that rolls
   back completely on any failure at any step.

A table is **skipped** when its Gold `source_checksum` (from
`gold_manifest.json`) matches the checksum recorded the last time it was
successfully loaded (`careflow_meta.load_manifest`) — `--force` bypasses
this. `--truncate-and-load` is equivalent to `--force` (reload
everything selected regardless of checksum). `--fail-fast` stops the run
immediately after the first table failure instead of continuing with the
rest.

## Unknown-member strategy

Gold's fact tables use `-1` (`UNKNOWN_SURROGATE_KEY`) in-Parquet to mark
a foreign key that didn't resolve to a real dimension row, paired with a
`<column>_is_missing` boolean flag. PostgreSQL foreign keys must be
either a valid reference or `NULL` — so at load time, `-1` becomes `NULL`
for every `*_key` column except the table's own primary key. **The
record is never dropped**; the `*_is_missing` flag (loaded as-is) is the
permanent record of *why* the reference is null. No "Unknown" dimension
row is fabricated — this is the one unknown-member strategy in use, and
it's documented here as required.

Fact-to-fact references (e.g. `fact_condition.encounter_key` back to
`fact_encounter.encounter_key`) and all mart tables intentionally have no
FK constraint: `encounter_key` is never null by construction, so a hard
constraint would abort the whole load on any legitimate orphan instead of
just flagging it. `scripts/validate_postgres_warehouse.py` reports orphan
counts for these instead of enforcing them at the database level.

## Schemas and tables

| Schema | Contents |
| --- | --- |
| `careflow_dim` | 8 dimensions |
| `careflow_fact` | 8 facts (includes `fact_imaging_study`) |
| `careflow_mart` | 6 marts + 6 `vw_*` reporting views |
| `careflow_meta` | `schema_version`, `load_manifest`, `table_registry` |
| `careflow_audit` | `load_run`, `load_error`, `validation_result` |

Every column definition in `config/postgres_schema.sql` is hand-authored
by semantic meaning, not generated from pandas/pyarrow type inference:
`BIGINT` for surrogate keys, `TEXT` for natural ids/codes/ZIP (leading
zeros preserved — `patients.csv`'s `"00000"` stays `"00000"`),
`DATE`/`TIMESTAMPTZ` for dates vs. UTC timestamps, `INTEGER` for counts
and date keys, `NUMERIC(18,2)` for currency, `DOUBLE PRECISION` for
ratios/lat-lon/statistical measures, `BOOLEAN` for flags.

### The imaging_studies correction, carried through to Postgres

Phase 2F found `imaging_studies.Id` is not row-level unique (a DICOM
study can span multiple series/instance rows); Phase 3A's
`fact_imaging_study` keys on a composite hash of
`(id, series_uid, instance_uid)`. The warehouse table's primary key is
`imaging_study_key` (that hash) — **never** `study_id` alone — with a
`UNIQUE (study_id, series_uid, instance_uid)` constraint making the true
grain explicit at the database level too.
`tests/test_schema_manager.py::test_imaging_study_primary_key_is_not_bare_source_id`
is a regression test for this.

## PII strategy and reporting views

Gold's `dim_patient` already excludes SSN, passport, driver's license,
street address, and full patient name (dropped during the Phase 3A
Silver → Gold build). The one precise, patient-level field that **is**
still present is latitude/longitude. All six `careflow_mart.vw_*`
reporting views select from `mart_*`/`dim_*` explicitly by column and
never include `dim_patient.latitude`/`longitude` — only coarse geography
(state, county, zip). `scripts/validate_postgres_warehouse.py` checks
every view's actual columns against a restricted-name list as a
regression guard.

## Validating the warehouse

```bash
PYTHONPATH=src python3 scripts/validate_postgres_warehouse.py
```

Compares live PostgreSQL state against Gold Parquet and
`gold_kpi_summary.json` (Gold is always the source of truth): schemas
and tables exist, columns match Gold, row counts match, primary keys are
non-null and unique, foreign-key and date-key orphans are counted,
currency sums reconcile within tolerance, the readmission mart's counts
match Gold exactly, four representative KPIs are recomputed from
PostgreSQL and compared to Gold's values within tolerance, and every
reporting view both executes and excludes restricted PII columns.

## Output reports

- `reports/warehouse/postgres_load_report.json` /
  `postgres_table_counts.csv` — per-table load status, row counts,
  method, duration
- `reports/warehouse/postgres_validation_report.json` /
  `postgres_orphan_summary.csv` — every validation check and a
  dedicated per-relationship orphan-count breakdown

## Testing

All four test files use mocks or lightweight fakes standing in for
`psycopg` — **none require a running PostgreSQL server**:

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_postgres_client.py tests/test_schema_manager.py tests/test_gold_loader.py tests/test_warehouse_validator.py
```
