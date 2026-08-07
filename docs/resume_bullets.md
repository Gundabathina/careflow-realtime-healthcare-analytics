# Resume Bullets

Two role-targeted variants, drawing only on verified metrics from
`docs/project_metrics.md`. Pick the set matching the role you're
applying for, or blend individual bullets.

## Healthcare Data Analyst version

- Built an end-to-end healthcare analytics platform processing ~100K
  synthetic clinical records (Synthea-generated) through a
  Bronze/Silver/Gold pipeline into a dimensional PostgreSQL warehouse
  (8 dimensions, 8 facts, 6 marts).
- Designed and delivered a 7-page interactive Streamlit dashboard
  (Executive, Readmission, Operations, Financial, Provider, Patient
  Population, Data Quality) with 30+ KPIs and data-driven, non-fabricated
  insight generation (e.g. automated month-over-month change detection).
- Defined and implemented a 7/14/30-day patient readmission methodology,
  computed independently in both Python and SQL (dbt), with automated
  reconciliation testing to guarantee the two implementations agree.
- Built a governed dbt analytics layer (36 models, 133 automated tests)
  enforcing PII exclusion and business-rule validation on every public
  reporting table.
- Authored a complete Power BI implementation package -- data model, 39
  DAX measures, and a page-by-page build guide -- covering the same 7
  analytical domains as the live dashboard.

## Healthcare Data Engineer version

- Architected and built a 6-layer healthcare data platform (Synthea ->
  Bronze -> Silver -> Gold -> PostgreSQL -> dbt) with checksum-based
  incremental processing at every layer, verified idempotent under
  repeated `--force` reprocessing.
- Diagnosed and fixed a transactional foreign-key violation bug in a
  PostgreSQL warehouse loader's force-reload path by redesigning it as
  a single whole-batch transaction with dependency-ordered clear/reload
  phases; verified the fix with back-to-back production-pattern reruns.
- Orchestrated the full pipeline with 2 Apache Airflow DAGs (22-task
  parameterized end-to-end run, 11-task scheduled incremental run)
  behind a custom operator restricted to an explicit command allow-list,
  with secret-redacting callbacks and PII-safe run summaries.
- Built and enforced a 3-layer PII-exclusion strategy (Gold
  transformation, dbt model tests, and independent dashboard-layer
  column checks) across a dimensional warehouse and its downstream
  reporting layer.
- Achieved 804 passing automated tests across 22 test files spanning
  ingestion, transformation, warehouse loading, dbt, orchestration, and
  dashboard security -- with isolated, version-pinned environments for
  three components (dbt, Airflow, Streamlit) whose dependency
  constraints were incompatible with the main project environment.
