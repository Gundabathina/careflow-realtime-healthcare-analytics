# Apache Airflow Orchestration Guide (Phase 4A)

Airflow coordinates the existing CareFlow pipeline end to end -- raw
data profiling/validation, Bronze, Silver, Gold, the PostgreSQL
warehouse, and dbt. It never reimplements any of that logic: every task
calls an existing script or dbt subcommand as a subprocess (see
`airflow/plugins/careflow_operators.py`'s `COMMAND_REGISTRY`). This
phase is orchestration only -- no Kafka, dashboards, machine learning,
or CI/CD.

## Two isolated environments, two different jobs

**Docker Compose runs Airflow itself** (webserver, scheduler, its own
metadata database). This is the preferred approach per the phase's own
guidance, and it's a clean fit here: Airflow's officially published
image (`apache/airflow:2.9.3-python3.11`) was already cached locally,
its Python 3.11 is fully compatible with dbt-core (unlike the project's
main Python 3.14), and containerizing it means the host's global Python
is never touched.

**`.venv-airflow` exists only so DAG-structure unit tests can run.**
`apache-airflow` is a large package that pulls in a specific, pinned set
of dependencies -- it cannot be installed into the project's main
Python 3.14 environment (`import airflow` would need to work in
whatever interpreter runs `pytest tests/test_airflow_dags.py`, and
installing it into the main environment is explicitly out of bounds).
So a second, narrowly-scoped Python 3.11 venv holds `apache-airflow==2.9.3`
purely for local test execution:

```bash
/opt/homebrew/bin/python3.11 -m venv .venv-airflow
.venv-airflow/bin/pip install "apache-airflow==2.9.3" \
    --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-2.9.3/constraints-3.11.txt" \
    pytest pyyaml
```

`.venv-airflow` never runs a scheduler or webserver -- that's Docker
Compose's job. It exists next to `.venv-dbt` (Phase 3C's isolated dbt
environment) without touching either it or the main `.venv`/Python 3.14.

## Why dbt runs differently inside the Airflow container

`.venv-dbt` is a **host-built** virtualenv (macOS binaries, via
Homebrew's Python 3.11). A venv like that cannot run inside a Linux
container -- the interpreter and compiled extensions don't match. So
inside the Airflow container, dbt (and the project's own runtime
dependencies: pandas, pyarrow, pyyaml, psycopg) are installed straight
into Airflow's own Python 3.11 environment instead, via
`_PIP_ADDITIONAL_REQUIREMENTS` (see `airflow/requirements.txt` and the
`x-airflow-common` block in `docker-compose.yml`). `scripts/run_dbt.py`
picks whichever is available: `CAREFLOW_DBT_BIN` (set inside the
container) overrides everything; otherwise it falls back to
`.venv-dbt/bin/dbt` (host default, unchanged from Phase 3C), then to
whatever `dbt` is on `PATH`.

`_PIP_ADDITIONAL_REQUIREMENTS` re-installs on every container start --
fine for local development, not for production. A production deployment
should replace it with a custom Dockerfile that `pip install`s
`airflow/requirements.txt` into a real image layer instead.

## Starting Airflow

Everything Airflow-related in `docker-compose.yml` sits behind the
`airflow` Compose profile, so the existing `docker compose up -d postgres`
workflow is completely unaffected -- profiles are additive.

```bash
cp .env.example .env   # if you haven't already; then fill in real values
PYTHONPATH=src python3 scripts/start_airflow.py
```

This starts `careflow-postgres` (if not already running), runs
`airflow-init` (metadata DB migration + admin user), then brings up
`airflow-scheduler` and `airflow-webserver`, waiting for the webserver's
`/health` endpoint. Equivalently, by hand:

```bash
docker compose up -d postgres
docker compose --profile airflow up airflow-init
docker compose --profile airflow up -d airflow-scheduler airflow-webserver
```

UI: <http://localhost:8081> by default (`AIRFLOW_WEBSERVER_PORT` in `.env`;
`AIRFLOW_ADMIN_USERNAME`/`AIRFLOW_ADMIN_PASSWORD` from `.env` to log in).
8080 is Airflow's own image default, but -- same story as PostgreSQL's
5432/5433 in Phase 3B -- it's a common port for another local project's
Airflow to already be holding; set `AIRFLOW_WEBSERVER_PORT` to any free
port and `docker-compose.yml` maps it via `${AIRFLOW_WEBSERVER_PORT:-8080}:8080`.

### Required `.env` values

Beyond the Phase 3B/3C PostgreSQL variables, Airflow needs its own
metadata database credentials and two generated security keys -- see
`.env.example` for the full list (`AIRFLOW_POSTGRES_*`,
`AIRFLOW_FERNET_KEY`, `AIRFLOW_WEBSERVER_SECRET_KEY`,
`AIRFLOW_ADMIN_*`). Generate real values for anything beyond local
development:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Never commit `.env`; never log these values (see **Security** below).

### Airflow's metadata database is a separate PostgreSQL instance

`airflow-postgres` (container `careflow-airflow-metadata-postgres`) is
a distinct container, database, and credential set from
`careflow-postgres` -- Airflow's own scheduling state never lives in
the CareFlow analytics warehouse. It has no host port mapping at all
(internal Docker networking only); nothing outside the Compose network
needs to reach it. `careflow-postgres` keeps its existing host port
mapping (`${POSTGRES_PORT:-5432}`, `5433` locally) unchanged.

Inside the Airflow containers, `POSTGRES_HOST=postgres` and
`POSTGRES_PORT=5432` (the container-internal service name/port, not the
host-mapped `5433`) -- DAG tasks reach the CareFlow warehouse over the
Compose network directly.

## The two DAGs

### `careflow_end_to_end`

No schedule -- manual or parameterized full-pipeline runs. Parameters
(validated both by Airflow's own `Param` schema and again, defensively,
in `careflow_operators.validate_dag_run_params`, since these values go
on to influence a subprocess argument list):

| Parameter | Type | Default | Effect |
|---|---|---|---|
| `generate_data` | bool | `false` | Run `setup_synthea.py` + `generate_synthea_data.py` first |
| `population` | int or null | `null` | `--population N` to `generate_synthea_data.py` (1-100000) |
| `force_bronze` | bool | `false` | Accepted for symmetry; `ingest_bronze.py` has no incremental mode, always fully reprocesses |
| `force_silver` | bool | `false` | `--force` to `build_silver_layer.py` |
| `force_gold` | bool | `false` | `--force` to `build_gold_layer.py` |
| `force_warehouse` | bool | `false` | `--force` to `load_postgres_warehouse.py` (the fixed, transactional force-reload from Phase 3B) |
| `run_dbt_snapshot` | bool | `false` | Run `dbt snapshot` |
| `run_dbt_docs` | bool | `false` | Run `dbt docs generate` |
| `fail_fast` | bool | `false` | Reserved; informational only in this phase |

Optional stages (`generate_data`, `dbt_snapshot`, `dbt_docs`) are
`BranchPythonOperator` decisions feeding either the real task chain or
an `EmptyOperator`, both joining back into the main chain via
`trigger_rule=NONE_FAILED_MIN_ONE_SUCCESS` -- so disabling an optional
stage never orphans downstream tasks.

```
environment_check
  -> decide_generate_data -> {setup_synthea -> generate_synthetic_data | skip_generate_data}
  -> profile_raw_data -> validate_raw_data -> ingest_bronze -> build_silver -> build_gold
  -> ensure_postgres_ready -> load_postgres -> validate_postgres
  -> dbt_seed -> decide_dbt_snapshot -> {dbt_snapshot | skip_dbt_snapshot}
  -> dbt_build -> decide_dbt_docs -> {dbt_docs | skip_dbt_docs}
  -> final_reconciliation
```

### `careflow_daily_analytics`

Schedule `0 2 * * *` (daily at 02:00), `catchup=False`. No parameters --
never regenerates synthetic data, and relies entirely on each stage's
own existing incremental behavior: Silver/Gold/warehouse loads run
*without* `--force`, so their checksum-based skip logic (unchanged from
Phases 3A-3C) applies exactly as it does on the host. Bronze always
fully reprocesses every gated-clear CSV -- that's `ingest_bronze.py`'s
own design, not something the DAG works around.

```
environment_check -> profile_raw_data -> validate_raw_data -> ingest_bronze_incremental
  -> build_silver_incremental -> build_gold_incremental -> ensure_postgres_ready
  -> load_postgres_incremental -> validate_postgres -> dbt_build -> final_summary
```

## CareFlowCommandOperator

Every real task (other than the branch/summary Python tasks) is a
`CareFlowCommandOperator` (`airflow/plugins/careflow_operators.py`).
It only ever runs a command from `COMMAND_REGISTRY` -- a fixed dict
mapping a `command_key` to an argument list -- and only the extra flags
declared in `ALLOWED_EXTRA_ARGS`/`ALLOWED_INT_ARGS` for that specific
command may be appended (`--force` where applicable; `--population N`
within `[1, 100000]` for data generation). `extra_args` can be a plain
list (validated at DAG-parse time) or a callable resolved and validated
at task-execution time (used for flags that depend on that run's
`dag_run.conf`, e.g. `force_gold`).

`subprocess.run` is always called with an argument list, never
`shell=True`. Captured stdout/stderr are logged (sanitized) and the
task pushes a compact XCom return value: `command_key`, `returncode`,
`duration_seconds`, and a redacted, ~15-line `stdout_tail` -- never the
full output, never a DataFrame, never a report's contents. A non-zero
exit code raises `AirflowException`, failing the task (and, via Airflow's
normal dependency semantics, every downstream data-dependent task).

## Bugs found and fixed via live integration testing

- **`ensure_postgres_ready` inside the Airflow container**: `scripts/start_postgres.py`
  originally always shelled out to `docker compose up -d postgres` /
  `docker inspect` -- fine on the host, but the Airflow container
  deliberately has no Docker socket mounted (mounting one is a real
  privilege-escalation surface, avoided on purpose). Inside the
  container this isn't actually the task's job anyway: `careflow-postgres`
  is already started, separately, by `scripts/start_airflow.py` before
  Airflow itself comes up -- the task only needs to confirm it's
  *reachable*. Fixed by falling back to a connectivity-only check
  (`careflow.warehouse.postgres_client.check_connectivity`) whenever the
  Docker daemon isn't reachable from wherever the script is running,
  leaving host behavior (`docker compose up -d postgres` + health-check
  wait) completely unchanged.
- **A failed upstream task didn't fail the DAG run**: `final_reconciliation`/
  `final_summary` use `trigger_rule=ALL_DONE` so they always run and
  always write a report -- but Airflow computes a DAG run's own overall
  state from its *leaf* tasks' states, not "did anything fail anywhere."
  A leaf that uses `ALL_DONE` and then succeeds made the whole run show
  as `success` even after a real upstream task failure (caught live: an
  accidental scheduled run of `careflow_daily_analytics`, triggered by
  unpausing it, hit the bug above before the fix landed, failed
  `ensure_postgres_ready`, and the DAG run still reported `success`).
  Fixed with `careflow_operators.raise_if_run_failed`: after writing the
  report, the summary task re-raises if any task failed, so the leaf's
  own state -- and therefore the DAG run's state -- reflects reality.
  Regression-tested in both `test_airflow_scripts.py` (the helper
  itself) and `test_airflow_dags.py` (both DAGs' summary callables
  actually call it).

## Idempotency

Nothing about running these DAGs changes how each underlying script
behaves. A plain (non-force) run of `careflow_daily_analytics` hits the
same checksum-based skip paths that already exist in Silver, Gold, and
the PostgreSQL loader. `force_warehouse=true` on `careflow_end_to_end`
calls `load_postgres_warehouse.py --force`, which uses the whole-batch
transactional clear-then-reload fixed in Phase 3B (clear marts, then
facts, then dimensions; reload dimensions, then facts, then marts; one
transaction, full rollback on any failure) -- **the same fix, not a
new one**; the DAG adds no additional logic here and cannot reintroduce
the old per-table foreign-key-violation bug. `max_active_runs=1` on
both DAGs prevents two concurrent runs from ever racing each other
against the same warehouse.

## Callbacks

`airflow/plugins/careflow_callbacks.py` provides
`task_failure_callback`, `task_retry_callback`, `task_success_callback`,
`dag_success_callback`, and `dag_failure_callback`, wired in via
`default_args` (task-level) and the `DAG(...)` constructor (DAG-level).
Every callback logs DAG id, task id, run id, execution timestamp,
exception type, try number, and the task's log URL when available --
and passes any exception message through the same secret-redaction
(`sanitize_text`) used by the command operator before logging it, so a
subprocess error that happened to echo a connection string never leaks
a password into the Airflow logs.

## Reports

`final_reconciliation` (end-to-end) / `final_summary` (daily) always run
(`trigger_rule=ALL_DONE` -- the one documented exception to "don't hide
failures with `all_done`": it's the run's own status report, and an
upstream failure still marks the DAG run itself as failed regardless of
this task's own outcome). Both call
`careflow_operators.build_run_summary` + `write_run_summary`, producing:

- `reports/airflow/airflow_run_summary.json` -- DAG id, run id, start/end
  timestamps, a sanitized copy of `dag_run.conf` (known keys only), per-task
  state/duration/try-number, and `final_status`.
- `reports/airflow/airflow_task_summary.csv` -- one row per task.
- `reports/airflow/airflow_failure_summary.json` -- written only when at
  least one task failed.

Task-instance summaries are structurally limited to `task_id`/`state`/
`duration`/`try_number` -- there is no field a patient record, a
DataFrame, or free-text log content could end up in.

## Security

- `AIRFLOW_FERNET_KEY`, `AIRFLOW_WEBSERVER_SECRET_KEY`, and every
  `POSTGRES_PASSWORD`/`AIRFLOW_POSTGRES_PASSWORD` are environment
  variables only -- never hard-coded, never written into
  `airflow/config/airflow.cfg.example` (a reference file only; the live
  config comes entirely from `AIRFLOW__SECTION__KEY` env vars in
  `docker-compose.yml`).
- `careflow_operators.sanitize_text` (password= patterns, embedded DSN
  credentials, and the literal configured `POSTGRES_PASSWORD` value)
  scrubs everything logged or pushed to XCom by the command operator and
  every callback.
- The CareFlow data used throughout this project is Synthea-generated
  synthetic data, but it is still handled as if it carried real PHI
  throughout this pipeline (no PII/PHI fields in logs, XCom, or run
  summaries) -- as a matter of project discipline, so the same code
  paths would already be safe against a real data source.
- `.env` is never committed (see `.gitignore`); `.env.example` ships
  only placeholder values, clearly documented as change-before-anything-
  beyond-local-development.

## Airflow connections

DAG tasks reach PostgreSQL the same way every other CareFlow script
does -- `POSTGRES_HOST`/`PORT`/`DB`/`USER`/`PASSWORD` environment
variables, read by `careflow.warehouse.postgres_client.load_connection_config()`
-- not an `airflow.db` Connection object, since these are the same
scripts used on the host and shouldn't need two different credential
mechanisms. If a native Airflow Connection is ever wanted instead (e.g.
for a future operator that talks to PostgreSQL directly via Airflow's
own hooks), initialize it from an environment variable rather than the
UI or committed code:

```bash
export AIRFLOW_CONN_CAREFLOW_POSTGRES="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}"
```

## Triggering and checking runs

```bash
PYTHONPATH=src python3 scripts/trigger_careflow_dag.py \
    --dag-id careflow_end_to_end   # generate_data=false, all force flags=false by default

PYTHONPATH=src python3 scripts/trigger_careflow_dag.py \
    --generate-data --population 200 --force-gold --run-dbt-docs

PYTHONPATH=src python3 scripts/check_pipeline_status.py --dag-id careflow_end_to_end
```

Both scripts validate every flag (booleans, a bounded `--population`)
before building a JSON `--conf` and shelling out to
`docker compose exec ... airflow dags trigger`/`list-runs` -- an
argument list, never a raw shell string.

## Tests

```bash
PYTHONPATH=src .venv-airflow/bin/python -m pytest -q \
    tests/test_airflow_dags.py tests/test_airflow_callbacks.py tests/test_airflow_scripts.py
```

None of these tests start a scheduler, Docker, Java, or PostgreSQL.
`test_airflow_dags.py` needs `apache-airflow` importable (hence
`.venv-airflow`); `test_airflow_callbacks.py` and
`test_airflow_scripts.py` degrade gracefully and also run under the
project's main Python, since `careflow_operators.py`/
`careflow_callbacks.py` only import Airflow's `BaseOperator`/
`AirflowException` behind a `try/except ImportError` fallback.

## What's next

Phase 4A stops here: orchestration only. Dashboards, machine learning,
and CI/CD are explicitly out of scope for this phase.
