# Airflow Workflow

Task graphs for both DAGs, generated directly from
`airflow/dags/careflow_end_to_end.py` and
`airflow/dags/careflow_daily_analytics.py` -- every node and edge below
matches the `>>` dependency wiring in those files.

## `careflow_end_to_end` (manual/parameterized, full pipeline)

```mermaid
flowchart TD
    A[environment_check] --> B{decide_generate_data}
    B -->|generate| C[setup_synthea] --> D[generate_synthetic_data] --> E[profile_raw_data]
    B -->|skip| F[skip_generate_data] --> E
    E --> G[validate_raw_data] --> H[ingest_bronze] --> I[build_silver] --> J[build_gold]
    J --> K[ensure_postgres_ready] --> L[load_postgres] --> M[validate_postgres]
    M --> N[dbt_seed] --> O{decide_dbt_snapshot}
    O -->|run| P[dbt_snapshot] --> Q[dbt_build]
    O -->|skip| R[skip_dbt_snapshot] --> Q
    Q --> S{decide_dbt_docs}
    S -->|run| T[dbt_docs] --> U[final_reconciliation]
    S -->|skip| V[skip_dbt_docs] --> U
```

22 tasks total. `decide_generate_data`, `decide_dbt_snapshot`, and
`decide_dbt_docs` are branch operators driven by DAG run parameters
(`generate_data`, `run_dbt_snapshot`, `run_dbt_docs`) -- every branch
converges back onto the main path via an `EmptyOperator` skip stub, so
the DAG has exactly one linear critical path regardless of which
branches are taken. Trigger with parameters via
`scripts/trigger_careflow_dag.py` (see
[`docs/airflow_orchestration_guide.md`](../airflow_orchestration_guide.md)).

## `careflow_daily_analytics` (scheduled, incremental-only)

```mermaid
flowchart TD
    A[environment_check] --> B[profile_raw_data] --> C[validate_raw_data]
    C --> D[ingest_bronze_incremental] --> E[build_silver_incremental] --> F[build_gold_incremental]
    F --> G[ensure_postgres_ready] --> H[load_postgres_incremental] --> I[validate_postgres]
    I --> J[dbt_build] --> K[final_summary]
```

11 tasks, no branching -- this DAG never regenerates synthetic data and
never touches `setup_synthea`/`generate_synthetic_data`; it only
re-runs the checksum/dependency-signature-gated incremental path, so a
scheduled run that finds nothing changed completes as a fast no-op
rather than reprocessing the whole warehouse.

## Design properties (both DAGs)

- **Idempotent** -- every task can be safely re-run; Bronze/Silver/Gold/
  PostgreSQL stages skip unchanged data via checksums/dependency
  signatures, and `--force`/`--truncate-and-load` variants exist for an
  explicit full reprocess.
- **Retry-aware** -- custom operators (`airflow/plugins/careflow_operators.py`)
  and callbacks (`airflow/plugins/careflow_callbacks.py`) sanitize
  driver exceptions before logging and surface real task failures
  rather than absorbing them into a summary task.
- **PII-safe run summaries** -- `reports/airflow/airflow_run_summary.json`
  and `airflow_task_summary.csv` never contain patient-identifying data.
- **Environment-isolated** -- the scheduler/webserver run in Docker
  (`docker-compose.yml`'s `airflow` profile); every task shells out to
  the same CLI scripts a developer would run by hand, so Airflow never
  reimplements pipeline logic.

## See also

- [`architecture.md`](architecture.md) -- overall system diagram
- [`data_flow.md`](data_flow.md) -- stage-by-stage input/process/output/validation
- [`../airflow_orchestration_guide.md`](../airflow_orchestration_guide.md) -- full setup, DAG parameters, and the two real bugs found/fixed during this phase
