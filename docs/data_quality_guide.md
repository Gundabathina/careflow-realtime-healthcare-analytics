# Data Quality & Relationship Validation Guide (Phase 2D)

CareFlow Analytics validates structural data quality and referential
integrity across the Synthea CSV files in `data/raw/synthea/csv/`. This
phase covers structural checks only — no clinical validation, no
database, streaming, orchestration, dashboards, or modeling.

## Two engines

- **`careflow.profiling.relationship_profiler`** — validates configured
  foreign-key relationships between files (e.g. `encounters.PATIENT ->
  patients.Id`).
- **`careflow.profiling.data_quality`** — runs structural/analytical rules
  (completeness, uniqueness, temporal ordering, numeric plausibility,
  domain values, identifier format) within and across files.

Both are configuration-driven, skip gracefully when files/columns are
missing, use chunked reads, and never modify `data/raw`.

## Configuration

All settings live under `data_quality` in
[`config/project_config.yaml`](../config/project_config.yaml):

| Key | Purpose |
| --- | --- |
| `chunk_size` | Rows per chunk when streaming CSVs |
| `max_failure_samples` | Max sample failures kept per rule/relationship |
| `max_patient_age_years` | Configurable ceiling for the impossible-age check |
| `thresholds.warning_pct` / `fail_pct` | Failure-percentage thresholds for most rules |
| `relationship_thresholds.warning_match_pct` / `fail_match_pct` | Match-percentage thresholds for relationships |
| `allowed_encounter_classes` | Configurable allowed set for `encounters.ENCOUNTERCLASS` |
| `critical_columns` | (file, column) pairs that must not contain unexpected nulls |
| `cost_fields` | (file, column) pairs checked for non-negative values |
| `uuid_columns` | (file, column) pairs expected to hold UUID-formatted identifiers |
| `zip_columns` | (file, column) pairs treated as identifier text (not numbers) |
| `relationships` | The full list of foreign-key relationships to validate |

Identifier-style rules (not-null, unique, date-parses, required FK
columns, death-before-birth) use zero-tolerance thresholds (any failure
is at least a "warning"); the rest use the configured general thresholds.

Access settings in code via `careflow.profiling.data_quality.load_data_quality_settings()`
and `careflow.profiling.relationship_profiler.load_relationship_configs()`
— both work standalone with sensible Python-level defaults if no config
override is present, which is what the test suite relies on.

## Running validation

```bash
PYTHONPATH=src python3 scripts/validate_synthea_data.py
```

This runs the relationship validator, then the data quality rule engine,
and writes four files to `reports/profiling/`:

- `relationship_summary.json` — one entry per configured relationship
- `data_quality_report.json` — one entry per rule, plus overall summary
- `data_quality_summary.csv` — one row per rule, for spreadsheet review
- `failed_record_samples.json` — consolidated warning/fail samples from
  both engines, limited to identifiers and the relevant failing fields
  (never full patient rows)

## Relationship report fields

Each relationship result includes: `relationship`, `parent_file`,
`parent_key`, `child_file`, `child_key`, `records_evaluated`,
`non_null_foreign_keys`, `matched_references`, `unmatched_references`,
`match_percentage`, `null_foreign_keys`, `duplicate_parent_keys`,
`sample_unmatched_values`, `status` (`pass` / `warning` / `fail` /
`skipped`), and `skipped_reason`.

A relationship is skipped (not failed) when its parent file, child file,
parent column, or child column doesn't exist — this lets the same
23-relationship default list work across Synthea exports with slightly
different schemas.

## Data quality rule fields

Each rule result includes: `rule_id`, `rule_name`, `category`,
`business_reason`, `source_file`, `columns_used`, `records_evaluated`,
`records_failed`, `failure_percentage`, `severity`, `status`,
`threshold`, `sample_failures`, and `skipped_reason`.

Rules fall into two groups:

- **Static rules** — one fixed `rule_id` each, covering identifiers
  (not-null/unique for patients and encounters), date parsing and
  ordering (encounter, procedure, medication, careplan, device
  start/stop; birth/death ordering; birth-before-encounter), numeric
  cost-coverage consistency, required FK column presence, encounter
  class domain values, and impossible ages.
- **Dynamic rules** — generated from the configured `cost_fields`,
  `critical_columns`, `uuid_columns`, and `zip_columns` lists, so adding
  a new column to check for negativity, nullness, UUID format, or
  ZIP-as-text is a config change, not a code change. This is also what
  makes the engine reusable for future Bronze-layer validation.

## Testing

Both test files use only temporary CSV fixtures — no dependency on Java,
Synthea, or the generated dataset:

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_relationship_profiler.py tests/test_data_quality.py
```
