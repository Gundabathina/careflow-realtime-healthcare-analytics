# API Reference

CareFlow Analytics has no HTTP/REST API -- it's a batch pipeline plus a
read-only dashboard. "API" here means the two real integration points:
the **CLI scripts** (how you run each stage) and the **public Python
functions** each script calls into (how you'd import this project's
logic from other code). Every signature below is copied from the
current source, not paraphrased.

## CLI scripts (`scripts/`)

All scripts are run as `PYTHONPATH=src python3 scripts/<name>.py [flags]`.

| Script | Key flags | What it does |
|---|---|---|
| `setup_synthea.py` | -- | One-time clone/build of the Synthea generator into `tools/synthea/` |
| `generate_synthea_data.py` | `--population`, `--state`, `--seed`, `--csv`/`--no-csv`, `--fhir`/`--no-fhir`, `--overwrite` | Runs Synthea, writes `data/raw/synthea/csv/*.csv` + generation manifest |
| `profile_synthea_data.py` | -- | Column-level profiling of the raw dataset |
| `validate_synthea_data.py` | -- | Relationship integrity + data quality rule checks on the raw dataset |
| `ingest_bronze.py` | -- | Gated, typed Parquet conversion of raw CSVs -> `data/bronze/` |
| `build_silver_layer.py` | `--force`, `--dataset` (repeatable) | Standardization + checksum-based incremental Silver build |
| `build_gold_layer.py` | `--force`, `--table` (repeatable), `--mart` (repeatable) | Dimensional star-schema Gold build |
| `start_postgres.py` | -- | Brings up the `postgres` Docker Compose service |
| `load_postgres_warehouse.py` | `--force`, `--table` (repeatable), `--schema-only`, `--truncate-and-load`, `--fail-fast` | Loads Gold Parquet into PostgreSQL, transactionally |
| `validate_postgres_warehouse.py` | -- | 172 checks comparing live PostgreSQL state to Gold's own outputs |
| `run_dbt.py` | positional `subcommand`: `debug`, `deps`, `seed`, `snapshot`, `run`, `test`, `build`, `docs-generate`, `full-refresh` | Wraps `dbt <subcommand>` with sanitized output and (for `build`) reconciliation report generation |
| `start_airflow.py` | -- | Brings up Airflow via the `airflow` Docker Compose profile |
| `trigger_careflow_dag.py` | `--dag-id` (`careflow_end_to_end`\|`careflow_daily_analytics`), `--generate-data`, `--population`, `--force-bronze`, `--force-silver`, `--force-gold`, `--force-warehouse`, `--run-dbt-snapshot`, `--run-dbt-docs`, `--fail-fast` | Triggers a DAG run with explicit conformance parameters |
| `start_dashboard.py` | `--port` (default `8501`) | Launches the Streamlit dashboard |
| `check_pipeline_status.py` | -- | Prints a summary of every stage's latest manifest/report |

## Python modules (`src/careflow/`)

Grouped by pipeline layer. Only the public (non-underscore-prefixed)
entry points are listed -- helper functions are intentionally private.

### `careflow.bronze.ingest`

```python
def load_bronze_settings(config: Config | None = None) -> BronzeSettings
def gate_reasons(profile_report: dict, relationship_report: dict, filename: str) -> list[str]
def ingest_file(csv_path: Path, schema: DatasetSchema, output_path: Path, ...) -> dict
def build_bronze_manifest(entries: list[dict], ...) -> dict
def run_bronze_ingestion(config: Config | None = None) -> dict
```

`run_bronze_ingestion()` is the single entry point `scripts/ingest_bronze.py` calls.

### `careflow.transformation.silver_transformer`

```python
def load_silver_settings(config: Config | None = None) -> SilverSettings
def transform_dataset(dataset: str, bronze_dir: Path, settings: SilverSettings) -> tuple[pd.DataFrame, dict]
def run_silver_build(config: Config | None = None, force: bool = False, datasets: list[str] | None = None) -> dict
def build_silver_quality_report(silver_dir: Path, manifest: dict) -> dict
def run_silver_pipeline(config: Config | None = None, force: bool = False, datasets: list[str] | None = None) -> dict
```

`run_silver_pipeline()` runs the build and writes the manifest + quality
report in one call -- what `scripts/build_silver_layer.py` uses.

### `careflow.gold.gold_builder`

```python
def load_gold_settings(config: Config | None = None) -> GoldSettings
def build_dim_patient(silver_dir: Path) -> tuple[pd.DataFrame, int]
def build_fact_encounter(silver_dir: Path, built: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, int]
def build_mart_readmission(built: dict[str, pd.DataFrame], settings: GoldSettings) -> tuple[pd.DataFrame, int]
# ... one build_dim_*/build_fact_*/build_mart_* function per table (22 total)
def run_gold_build(config: Config | None = None, force: bool = False, tables: list[str] | None = None, marts: list[str] | None = None) -> dict
def build_gold_quality_report(gold_dir: Path, manifest: dict) -> dict
def run_gold_pipeline(config: Config | None = None, force: bool = False, tables: list[str] | None = None, marts: list[str] | None = None) -> dict
```

`run_gold_pipeline()` is the entry point `scripts/build_gold_layer.py`
uses; each `build_dim_*`/`build_fact_*`/`build_mart_*` function is
independently callable and returns `(dataframe, row_count)`, which is
how `tests/test_gold_builder.py` exercises each table in isolation.

### `careflow.warehouse.postgres_client`

```python
class PostgresConnectionConfig  # host, port, dbname, user, password
def load_connection_config(env: dict | None = None) -> PostgresConnectionConfig
def validate_identifier(name: str, allowed: set[str] | None = None) -> str
def get_connection(config: PostgresConnectionConfig | None = None) -> Iterator[psycopg.Connection]
def check_connectivity(config: PostgresConnectionConfig | None = None) -> tuple[bool, str | None]
```

`load_connection_config()` is the single credential-loading path used
by every other module (pipeline scripts, the dashboard, Airflow
operators) -- never duplicated. Exceptions
(`MissingCredentialsError`, `UnsafeIdentifierError`,
`WarehouseConnectionError`) are always raised with sanitized messages.

### `careflow.warehouse.gold_loader`

```python
def load_table(conn, schema_name: str, table_name: str, df: pd.DataFrame, ...) -> dict
def run_force_reload(config: Config | None = None, tables: list[str] | None = None) -> dict
def run_gold_load(config: Config | None = None, force: bool = False, tables: list[str] | None = None, schema_only: bool = False, fail_fast: bool = False) -> dict
```

`run_gold_load()` is what `scripts/load_postgres_warehouse.py` calls;
`run_force_reload()` implements the whole-batch transactional
clear-then-reload path used by `--truncate-and-load`.

### `careflow.warehouse.warehouse_validator`

```python
def check_tables_exist(conn) -> list[dict]
def check_row_counts(conn, gold_dir: Path) -> list[dict]
def check_foreign_key_orphans(conn) -> list[dict]
def check_currency_reconciliation(conn, gold_dir: Path, tolerance: float = DEFAULT_CURRENCY_TOLERANCE) -> list[dict]
def check_readmission_counts_match(conn, gold_dir: Path) -> list[dict]
def run_validation(config: Config | None = None) -> dict
```

`run_validation()` runs all 172 checks and is what
`scripts/validate_postgres_warehouse.py` calls.

### `dashboard.database` (dashboard-side "API")

```python
def run_query(sql: str, params: tuple | dict | None = None) -> pd.DataFrame
def run_filter_query(sql: str, params: tuple | dict | None = None) -> pd.DataFrame
def assert_no_restricted_columns(columns: list[str]) -> None
def check_database_available() -> tuple[bool, str | None]
```

Every function in `dashboard/queries.py` (e.g. `get_executive_kpis`,
`get_readmission_kpis`, `get_encounter_volume_by_month`) is built on
top of `run_query`/`run_filter_query` and takes a single `Filters`
object (`dashboard/components/filters.py`) as its filter argument --
see [`dashboard_architecture.md`](architecture/dashboard_architecture.md)
for how these compose into pages.

## See also

- [`architecture/data_flow.md`](architecture/data_flow.md) -- how these functions chain stage-to-stage
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md) -- conventions for adding new functions in this style
