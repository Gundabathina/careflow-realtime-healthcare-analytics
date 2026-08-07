# Silver Layer Transformation Guide (Phase 2F)

CareFlow Analytics transforms Bronze Parquet datasets into clean,
standardized, analytics-ready Silver Parquet datasets. This phase is
transformation only — no PostgreSQL, dbt, Kafka, Airflow, dashboards, or
modeling.

Reads only from `data/bronze/`. Writes only to `data/silver/` and
`reports/profiling/`. Never modifies `data/raw` or `data/bronze`.

## Two modules

- **`careflow.transformation.schema_registry`** — a purely declarative
  registry: one `DatasetSchema` per Bronze table describing its expected
  source columns, renamed target columns, primary/foreign key
  candidates, date/identifier/numeric columns, required columns, and
  which named transform (if any) applies. The pipeline is driven by this
  registry, not by trusting pandas' type inference.
- **`careflow.transformation.silver_transformer`** — the engine. Applies
  the registry's rules generically to every dataset, then runs one of
  six dataset-specific derived-column functions (patients, encounters,
  conditions, procedures, medications, observations) looked up by name
  from the registry's `transform_name` field.

## Column naming

Every column is renamed to lowercase snake_case. A small, fixed set of
foreign-key-shaped names (`PATIENT`, `ENCOUNTER`, `ORGANIZATION`,
`PROVIDER`, `PAYER`, `MEMBERID`) is renamed consistently to
`*_id` everywhere they appear, since they mean the same thing in every
Synthea file — this is one deterministic rule applied uniformly, not a
per-table invention. Each dataset's own `Id` column becomes its
semantic primary key (`patient_id`, `encounter_id`); everywhere else a
literal `Id`/`ID` becomes `id`.

## Common rules (all 18 datasets)

1. Column names normalized to snake_case.
2. `source_file` (original raw CSV name) preserved as a column.
3. `ingestion_timestamp_utc` (from the Bronze manifest),
   `transformation_timestamp_utc` (this run's time), and
   `source_checksum` (the raw CSV's SHA-256, from Bronze) added.
4. Exact duplicate rows removed; the count is recorded, never silent.
5. Source and target row counts are both recorded in the manifest.
6. Empty strings become null.
7. Identifier-like columns (keys, codes, `zip`, `ssn`, `fips`, etc.) are
   never coerced to numeric.
8. ZIP codes stay text — leading zeros are never stripped.
9. Date columns are parsed safely (`errors="coerce"`); values that fail
   to parse become null and are counted in `parse_failures`, never
   dropped or fabricated.
10. All timestamps are normalized to UTC.
11. Nulls are never filled in unless a specific rule says so (e.g. a
    derived flag like `is_active`).

## Dataset-specific rules

- **patients** — `Id → patient_id`; birthdate/deathdate parsed; gender
  uppercased, race/ethnicity lowercased; SSN/drivers/passport/ZIP/FIPS
  kept as text; lat/lon/healthcare_expenses/healthcare_coverage/income
  made numeric; deduplicated on `patient_id` (after generic exact-row
  dedup), with `is_duplicate_patient_id` flagging which rows had
  duplicates; derives `is_deceased`, `age_at_reference_date`, and
  `age_group`. The reference date comes from `silver.reference_date` in
  config — **never** `datetime.now()`.
- **encounters** — `Id/PATIENT/ORGANIZATION/PROVIDER/PAYER` renamed to
  `*_id`; start/stop parsed; derives `encounter_duration_minutes`,
  `encounter_date`, `encounter_year`, `encounter_month`,
  `is_inpatient`, `is_emergency`; `encounter_class` lowercased; STOP
  before START is **flagged** (`stop_before_start`), not dropped or
  silently fixed — duration is left null for those rows.
- **conditions** — derives `is_active` (no STOP) and
  `condition_duration_days`.
- **procedures** — derives `procedure_duration_minutes`.
- **medications** — derives `is_active` and `medication_duration_days`.
- **observations** — `numeric_value` is populated only when `value`
  parses as a number; the raw `value` is always retained, and
  non-numeric results (common — many observations are qualitative) are
  not treated as failures.
- **everything else** (allergies, careplans, claims,
  claims_transactions, devices, imaging_studies, immunizations,
  organizations, payer_transitions, payers, providers, supplies) —
  generic rules only: column renaming, name-detected date parsing
  (`start`/`stop`/anything containing `date`), identifier preservation,
  and exact-duplicate removal. Numeric typing here is limited to columns
  Bronze already typed as `double` (Phase 2E's `cost_fields`), reusing
  that decision rather than guessing which other columns are safely
  numeric.

## Incremental builds

```bash
PYTHONPATH=src python3 scripts/build_silver_layer.py
PYTHONPATH=src python3 scripts/build_silver_layer.py --force
PYTHONPATH=src python3 scripts/build_silver_layer.py --dataset patients --dataset encounters
```

Each dataset's Bronze `source_checksum` (from `bronze_manifest.json`) is
compared against the checksum recorded the last time that dataset was
successfully processed. Unchanged → `status: "skipped"`, Silver file
left untouched. Changed, or `--force`, → reprocessed. `--dataset`
restricts the run to specific datasets; anything left out keeps its
previous manifest entry exactly as it was.

## Outputs

- `data/silver/<dataset>.parquet` for every processed/skipped dataset
- `data/silver/silver_manifest.json` — `run_id`, start/complete
  timestamps, the Bronze manifest path, and per-dataset
  `source_checksum`, `target_file`, `source_row_count`,
  `target_row_count`, `duplicate_rows_removed`, `parse_failures`,
  `status` (`processed`/`skipped`/`failed`), `transformation_version`,
  `schema_version`
- `reports/profiling/silver_quality_report.json` and
  `silver_quality_summary.csv` — structural, type, referential, and
  reconciliation checks per dataset (primary key null/uniqueness,
  required/foreign-key nulls, UTC-aware timestamps, numeric/identifier
  typing, ZIP-as-text, no negative costs, encounter STOP≥START,
  cross-dataset birthdate-before-encounter, row-count reconciliation).

## Testing

`tests/test_silver_transformer.py` uses only temporary Parquet fixtures
— no dependency on the real Bronze/Synthea dataset:

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_silver_transformer.py
```
