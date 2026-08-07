# Changelog

Format loosely follows [Keep a Changelog](https://keepachangelog.com/).
This project was built and delivered as a single sequence of phases
rather than incremental dated releases; each phase below is a real,
distinct unit of work, verified before moving to the next.

## [v1.0.0] -- 2026-08-07 -- Portfolio milestone

The full platform, end to end: synthetic data generation through
Power BI preparation and recruiter-facing documentation.

### Added

- **Data generation & profiling** -- Synthea integration for synthetic
  patient generation; column profiling, relationship integrity, and
  data quality validation against the raw dataset.
- **Bronze layer** -- typed Parquet ingestion, gated by data quality
  validation, with a manifest recording row counts, schema, and checksums.
- **Silver layer** -- standardization and cleaning, checksum-based
  incremental processing.
- **Gold layer** -- dimensional star schema (8 dimensions, 8 facts, 6
  marts), deterministic surrogate keys, independently computed 7/14/30-day
  readmission logic.
- **PostgreSQL warehouse** -- transactional, incremental, checksum-based
  load; whole-batch transactional force-reload; 172-check validation
  suite comparing the warehouse back to Gold's own outputs.
- **dbt analytics layer** -- 36 models (staging/intermediate/marts), 133
  tests (121 generic + 12 singular), 4 seeds, 3 snapshots, full
  documentation and lineage, reconciliation against the Python Gold layer.
- **Apache Airflow orchestration** -- `careflow_end_to_end` (parameterized
  full pipeline) and `careflow_daily_analytics` (scheduled incremental)
  DAGs; custom operator with a fixed command allow-list; callbacks with
  secret redaction; idempotent by design.
- **Streamlit dashboard** -- 7 interactive pages (Executive, Readmission,
  Hospital Operations, Financial Performance, Provider Performance,
  Patient Population, Data Quality), PII-safe by construction and by
  independent runtime check.
- **Power BI preparation** -- audited CSV exports, a documented star-schema
  data model, 39 production-quality DAX measures, a field-by-field
  page build guide, a professional theme, and a QA checklist -- ready
  for a `.pbix` build in Power BI Desktop.
- **Portfolio polish** -- recruiter-facing README, architecture
  documentation (with Mermaid diagrams), verified project metrics,
  security and data-ethics documentation, an interview guide, resume
  bullets, and a demo script.

### Testing

- 732 automated tests across 22 test files, covering every layer from
  Bronze ingestion through the Power BI export package. See
  [`docs/project_metrics.md`](docs/project_metrics.md) for the full
  breakdown.

### Known limitations (see `docs/project_metrics.md` and `README.md#future-improvements`)

- No CI/CD pipeline configured yet.
- Power BI is prepared but not built (`.pbix` requires Power BI Desktop,
  a Windows GUI application).
- One pre-existing Silver-layer data quality check remains failing
  (shown honestly on the Data Quality dashboard page, not hidden).
