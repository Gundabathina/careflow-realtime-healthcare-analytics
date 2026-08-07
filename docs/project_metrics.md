# Project Metrics (Verified)

Every number below is read directly from a report or manifest already
produced by the pipeline -- none are estimated or invented. Regenerate
any of them yourself with the commands noted per section. Snapshot
taken 2026-08-07; re-run the pipeline and these commands to refresh.

## Data volume

| Metric | Value | Source |
|---|---|---|
| Raw Synthea source files | 18 CSVs | `data/raw/synthea/csv/` |
| Raw source rows (sum across all 18 files) | 99,896 | counted directly from the raw CSVs |
| Bronze files ingested | 18 / 18 (0 blocked, 0 skipped) | `data/bronze/bronze_manifest.json` |
| Silver datasets | 18 | `data/silver/silver_manifest.json` |
| Gold tables | 22 (8 dimensions, 8 facts, 6 marts) | `data/gold/gold_manifest.json` |
| PostgreSQL warehouse tables loaded | 22 | `reports/warehouse/postgres_table_counts.csv` |

### Gold layer row counts by table

| Table | Kind | Rows |
|---|---|---|
| `dim_patient` | dimension | 58 |
| `dim_provider` | dimension | 161 |
| `dim_organization` | dimension | 161 |
| `dim_payer` | dimension | 10 |
| `dim_date` | dimension | 30,270 |
| `dim_condition` | dimension | 160 |
| `dim_procedure` | dimension | 195 |
| `dim_medication` | dimension | 106 |
| `fact_encounter` | fact | 3,180 |
| `fact_condition` | fact | 1,725 |
| `fact_procedure` | fact | 7,601 |
| `fact_medication` | fact | 2,104 |
| `fact_observation` | fact | 28,089 |
| `fact_claim` | fact | 5,287 |
| `fact_immunization` | fact | 845 |
| `fact_imaging_study` | fact | 258 |
| `mart_patient_360` | mart | 58 |
| `mart_readmission` | mart | 207 |
| `mart_hospital_operations` | mart | 3,094 |
| `mart_financial_performance` | mart | 2,192 |
| `mart_provider_utilization` | mart | 2,149 |
| `mart_monthly_kpis` | mart | 441 |

## dbt analytics layer

| Metric | Value | Source |
|---|---|---|
| dbt models | 36 (14 staging + 8 intermediate + 14 marts) | `reports/dbt/dbt_model_inventory.csv` |
| dbt seeds | 4 | `reports/dbt/dbt_model_inventory.csv` |
| dbt snapshots | 3 | `reports/dbt/dbt_model_inventory.csv` |
| dbt tests (generic + singular) | 133 (121 generic, 12 singular) | `reports/dbt/dbt_test_summary.json` |
| dbt test result | 133 passed, 0 warned, 0 failed, 0 skipped | `reports/dbt/dbt_test_summary.json` |

Reproduce: `PYTHONPATH=src python3 scripts/run_dbt.py build` (see
`docs/dbt_analytics_guide.md`).

## PostgreSQL warehouse validation

| Metric | Value | Source |
|---|---|---|
| Validation checks | 172 total | `reports/warehouse/postgres_validation_report.json` |
| Result | 172 passed, 0 warnings, 0 failed, 0 skipped | `reports/warehouse/postgres_validation_report.json` |

Reproduce: `PYTHONPATH=src python3 scripts/validate_postgres_warehouse.py`.

## Orchestration

| Metric | Value | Source |
|---|---|---|
| Airflow DAGs | 2 (`careflow_end_to_end`, `careflow_daily_analytics`) | `airflow/dags/` |
| Tasks in `careflow_end_to_end` | 22 | `airflow/dags/careflow_end_to_end.py` |
| Tasks in `careflow_daily_analytics` | 11 | `airflow/dags/careflow_daily_analytics.py` |

## Dashboards

| Metric | Value | Source |
|---|---|---|
| Streamlit pages | 7 + landing page | `dashboard/pages/` |
| Power BI pages specified | 7 | `powerbi/page_build_guide.md` |
| Power BI DAX measures documented | 39 | `powerbi/dax_measures.md` |
| Power BI CSV exports | 7 | `data/exports/powerbi/` |

## Testing

| Metric | Value | Source |
|---|---|---|
| Test files | 22 | `tests/` |
| Total tests (full suite) | 804 passed, 2 skipped (see note) | `PYTHONPATH=src python3 -m pytest -q tests/` |

**Note on the 2 skipped tests:** both are skipped intentionally, not
failing -- `test_airflow_dags.py` and `test_dashboard_components.py`
require `apache-airflow`/`streamlit` respectively, which live in
isolated virtual environments (`.venv-airflow`, `.venv-dashboard`) kept
separate from the project's main Python 3.14 environment (neither
package supports/is compatible with it or its pinned dependencies --
see `docs/airflow_orchestration_guide.md` and
`docs/dashboard_guide.md`). Both run for real, with full pass results,
under their respective isolated environments.

## How to regenerate this page

```bash
# Bronze/Silver/Gold manifests
cat data/bronze/bronze_manifest.json | python3 -c "import json,sys; print(json.load(sys.stdin)['summary'])"
cat data/silver/silver_manifest.json | python3 -c "import json,sys; print(json.load(sys.stdin)['summary'])"
cat data/gold/gold_manifest.json | python3 -c "import json,sys; print(json.load(sys.stdin)['summary'])"

# dbt
cat reports/dbt/dbt_test_summary.json

# PostgreSQL validation
cat reports/warehouse/postgres_validation_report.json

# Full test suite
PYTHONPATH=src python3 -m pytest -q tests/
```
