# Bronze Layer Ingestion Guide (Phase 2E)

CareFlow Analytics loads validated Synthea CSVs from `data/raw/synthea/csv/`
into a typed Parquet Bronze layer at `data/bronze/`. This phase is
ingestion only — no PostgreSQL, Kafka, orchestration, dashboards, or
modeling.

## What "Bronze" means here

Each source CSV becomes one Parquet file with an explicit, consistent
schema:

- **Dates** (`START`, `STOP`, `BIRTHDATE`, `DEATHDATE`, `DATE`, etc.) are
  parsed and typed as UTC timestamps.
- **Cost fields** (reused directly from `data_quality.cost_fields` in
  config — no duplicated list) are typed as `double`.
- **Everything else stays text**, including `Id` and other identifier
  columns and `ZIP` — consistent with the Phase 2D rule that ZIP codes
  and identifiers must never be corrupted by numeric type inference
  (leading zeros preserved).

Row and column counts are never assumed from filenames; files are
discovered dynamically via `careflow.profiling.file_profiler.discover_csv_files`,
the same mechanism Phase 2C uses.

## The validation gate

Before a file is promoted to Bronze, `run_bronze_ingestion` **reuses
Phase 2D**: it calls `build_relationship_summary` and
`build_data_quality_report` directly (no reimplementation) to get a
fresh read of the data's health, and rewrites `reports/profiling/*.json`
as a side effect so those reports always reflect the state at last
ingestion.

A file is **blocked** (not ingested) if:

- any data quality rule whose `source_file` includes that filename has a
  status in `bronze.gate.block_on_statuses` (default: `["fail"]`), or
- any relationship whose `child_file` is that filename has a status in
  the same set.

`warning` and `skipped` statuses never block by default — only `fail`
does. This is configurable under `bronze` in
[`config/project_config.yaml`](../config/project_config.yaml):

```yaml
bronze:
  chunk_size: 50000
  gate:
    enabled: true
    block_on_statuses: ["fail"]
  date_columns:
    - {file: "encounters.csv", column: "START"}
    # ...
```

Set `gate.enabled: false` to ingest everything regardless of validation
status (useful for inspecting bad data in Bronze itself).

## Running ingestion

```bash
PYTHONPATH=src python3 scripts/ingest_bronze.py
```

This writes:

- `data/bronze/<name>.parquet` for every file that passed the gate
- `data/bronze/bronze_manifest.json` — one entry per discovered file with
  `status` (`ingested` / `blocked` / `skipped`), `row_count`,
  `column_count`, `source_checksum` and `bronze_checksum` (SHA-256),
  `bronze_size_bytes`, the applied `schema`, any `cast_failures` (values
  that failed to parse as the configured type), and the reason when
  blocked or skipped.
- Refreshed `reports/profiling/relationship_summary.json`,
  `data_quality_report.json`, `data_quality_summary.csv`, and
  `failed_record_samples.json`.

`data/raw` is only ever read, never written.

## How ingestion stays memory-conscious

Both the CSV read and the Parquet write are chunked: `ingest_file` builds
one fixed `pyarrow.Schema` from the header up front, then streams each
pandas chunk through `pyarrow.parquet.ParquetWriter.write_table` rather
than materializing the full typed DataFrame before writing. Peak memory
is bounded by one chunk, not the whole file — this matters for the
20MB+ `claims_transactions.csv`-scale files.

## Testing

`tests/test_bronze_ingest.py` uses only temporary CSV fixtures — no
dependency on Java, Synthea, or the generated dataset:

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_bronze_ingest.py
```
