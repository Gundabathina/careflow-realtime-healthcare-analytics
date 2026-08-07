# Gold Layer Guide (Phase 3A)

CareFlow Analytics transforms Silver Parquet datasets into a dimensional
Gold layer: a star schema plus healthcare analytics marts. This phase is
modeling only — no PostgreSQL, dbt, Kafka, Airflow, dashboards, or
machine learning.

Reads only from `data/silver/`. Writes only to `data/gold/` and
`reports/profiling/`. Never modifies `data/raw`, `data/bronze`, or
`data/silver`.

## Three modules

- **`careflow.gold.schema`** — surrogate key generation, the table
  dependency graph, and the expected-column schema for every Gold table.
- **`careflow.gold.gold_builder`** — builds every dimension, fact, and
  mart, plus incremental orchestration, the manifest, and quality checks.
- **`careflow.gold.kpi_calculator`** — twelve reusable KPI calculations,
  each returning numerator/denominator/value/unit/definition/timestamp.

## Surrogate keys

Every surrogate key is `int(sha256(namespace|natural_key)[:15 hex])` —
deterministic, namespace-scoped (so `patient` and `provider` never
collide even with the same natural key value), and **never** a random
value or row number. The same natural key always produces the same
surrogate key across runs, across processes, and even when row order
changes. Facts resolve their foreign keys by independently computing the
same hash from the natural key and checking it against the dimension's
actual key set — no join is needed to know a key's *value*, only to know
whether it's *valid*.

Unresolved or null natural keys get `UNKNOWN_SURROGATE_KEY = -1` plus a
`<column>_is_missing` boolean flag on the row. **Rows are never dropped**
for an unresolved foreign key — only flagged.

## Star schema

**Dimensions**: `dim_patient`, `dim_provider`, `dim_organization`,
`dim_payer`, `dim_date` (a complete daily calendar spanning the min/max
date found across all Silver date columns), `dim_condition`,
`dim_procedure`, `dim_medication` (deduplicated code→description
lookups).

**Facts**: `fact_encounter` (`patient_responsibility = total_claim_cost
- payer_coverage`, flagged — not fabricated — when negative),
`fact_condition`, `fact_procedure`, `fact_medication`,
`fact_observation`, `fact_claim` (uses `claims.csv`'s `providerid` and
`primarypatientinsuranceid`, verified against `dim_provider`/`dim_payer`
before trusting them as real foreign keys — no relationship is invented
for columns without a verified match), `fact_immunization`, and
`fact_imaging_study`.

Conditions, procedures, medications, observations, and immunizations
have no row-level id in the source data, so their event keys are hashes
of a composite natural key (e.g. `patient_id|encounter_id|code|start`).

### The imaging_studies correction

Phase 2F's Silver quality checks found `imaging_studies.Id` is **not**
row-level unique — one DICOM study can span multiple series/instance
rows. `fact_imaging_study` makes this explicit: its grain is
`imaging_study_composite_key()` = `id + series_uid + instance_uid`, not
`id` alone. `tests/test_gold_builder.py` has a dedicated regression test
(`test_imaging_study_composite_key_disambiguates_same_study_id`) that
fails if this ever regresses back to using `id` as the sole key.

**Marts**: `mart_patient_360`, `mart_readmission`,
`mart_hospital_operations`, `mart_financial_performance`,
`mart_provider_utilization`, `mart_monthly_kpis`.

### Readmission logic

A patient's qualifying encounters (`inpatient`/`emergency` by default,
configurable via `gold.readmission.qualifying_encounter_classes`) are
sorted chronologically per patient; each becomes an "index" row whose
"next" is the *immediately following* qualifying encounter for that same
patient (via a groupby shift) — never the same row, so index and next
can never be the same encounter. `days_to_readmission` is
`next.start - index.discharge`; when that would be **negative**
(overlapping/back-to-back encounters in the source data), it's left
**null** rather than emitted as a negative number — "days to
readmission" isn't a meaningful concept when encounters overlap.
Windows are configurable via `gold.readmission.windows_days`.

## Incremental builds

```bash
PYTHONPATH=src python3 scripts/build_gold_layer.py
PYTHONPATH=src python3 scripts/build_gold_layer.py --force
PYTHONPATH=src python3 scripts/build_gold_layer.py --table dim_patient --table fact_encounter
PYTHONPATH=src python3 scripts/build_gold_layer.py --mart mart_readmission
```

Each table's "dependency signature" combines the Bronze→Silver
`source_checksum` of every Silver dataset it transitively depends on
(resolved through the dependency graph in `schema.TABLE_DEPENDENCIES`).
Unchanged signature + existing file + not `--force` → `skipped`.
Otherwise → rebuilt. Because a fact's signature includes its
dimensions' underlying Silver checksums, **changing a dimension's source
data automatically triggers rebuilding every fact and mart that depends
on it** — you don't have to track that by hand.

`--table`/`--mart` restrict the run to specific tables, but their
dependencies are pulled in automatically (built fresh if missing, or
just checked/skipped if already current) so a single-table build always
produces a coherent result.

## Outputs

- `data/gold/<table>.parquet` for every dimension, fact, and mart
- `data/gold/gold_manifest.json` — `run_id`, timestamps, the Silver
  manifest path, and per-table `source_checksum` (the dependency
  signature), `target_path`, `source_rows`, `target_rows`, `status`
  (`processed`/`skipped`/`failed`), `dependencies`, `schema_version`,
  `transformation_version`, `key_strategy`
- `reports/profiling/gold_quality_report.json` and
  `gold_quality_summary.csv` — surrogate/fact key null & uniqueness
  checks, schema-drift checks, foreign-key-missing flags, date-key
  resolution against `dim_date`, row-count reconciliation, negative
  patient-responsibility flagging, readmission self-reference and
  negative-days checks, and KPI numerator/denominator validity
- `reports/profiling/gold_kpi_summary.json` — all twelve KPIs

## Testing

Both test files use only temporary Parquet fixtures — no dependency on
the real Bronze/Silver/Synthea dataset:

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_gold_builder.py tests/test_kpi_calculator.py
```
