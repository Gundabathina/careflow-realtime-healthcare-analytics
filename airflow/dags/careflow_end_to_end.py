"""careflow_end_to_end -- full CareFlow pipeline, manual/parameterized run.

Coordinates the existing scripts (Bronze/Silver/Gold, PostgreSQL warehouse,
dbt) end to end; it never reimplements their business logic, only calls
them as subprocesses via CareFlowCommandOperator (see
airflow/plugins/careflow_operators.py). No schedule -- triggered manually
or via scripts/trigger_careflow_dag.py, with dag_run.conf parameters
controlling optional stages and force-reload flags.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins"))

from airflow import DAG
from airflow.models.param import Param
from airflow.operators.empty import EmptyOperator
from airflow.operators.python import BranchPythonOperator, PythonOperator
from airflow.utils.trigger_rule import TriggerRule

from careflow_callbacks import (
    dag_failure_callback,
    dag_success_callback,
    task_failure_callback,
    task_retry_callback,
    task_success_callback,
)
from careflow_operators import (
    CareFlowCommandOperator,
    build_run_summary,
    get_project_root,
    raise_if_run_failed,
    run_environment_check,
    validate_dag_run_params,
    write_run_summary,
)

DAG_ID = "careflow_end_to_end"

DEFAULT_ARGS = {
    "owner": "careflow-data-engineering",
    "retries": 1,
    "retry_delay": timedelta(minutes=2),
    "execution_timeout": timedelta(minutes=15),
    "on_failure_callback": task_failure_callback,
    "on_retry_callback": task_retry_callback,
    "on_success_callback": task_success_callback,
}

PARAMS = {
    "generate_data": Param(False, type="boolean", description="Regenerate synthetic Synthea data before profiling"),
    "population": Param(None, type=["null", "integer"], minimum=1, maximum=100000, description="Synthea population size (only used when generate_data=true)"),
    "force_bronze": Param(False, type="boolean", description="Accepted for symmetry; ingest_bronze.py always fully reprocesses (no incremental mode)"),
    "force_silver": Param(False, type="boolean", description="Pass --force to build_silver_layer.py"),
    "force_gold": Param(False, type="boolean", description="Pass --force to build_gold_layer.py"),
    "force_warehouse": Param(False, type="boolean", description="Pass --force to load_postgres_warehouse.py"),
    "run_dbt_snapshot": Param(False, type="boolean", description="Run dbt snapshot"),
    "run_dbt_docs": Param(False, type="boolean", description="Run dbt docs generate"),
    "fail_fast": Param(False, type="boolean", description="Reserved for future fail-fast wiring; currently informational only"),
}


def _params(context) -> dict:
    return validate_dag_run_params(dict(context["params"]))


def _environment_check(**context):
    return run_environment_check(_params(context))


def _decide_generate_data(**context):
    return "setup_synthea" if _params(context)["generate_data"] else "skip_generate_data"


def _decide_dbt_snapshot(**context):
    return "dbt_snapshot" if _params(context)["run_dbt_snapshot"] else "skip_dbt_snapshot"


def _decide_dbt_docs(**context):
    return "dbt_docs" if _params(context)["run_dbt_docs"] else "skip_dbt_docs"


def _generate_synthetic_data_extra_args(**context) -> list[str]:
    population = _params(context)["population"]
    return ["--population", str(population)] if population else []


def _final_reconciliation(**context):
    dag_run = context["dag_run"]
    task_instances = [
        {
            "task_id": ti.task_id,
            "state": ti.state,
            "duration": ti.duration,
            "try_number": ti.try_number,
        }
        for ti in dag_run.get_task_instances()
        if ti.task_id != "final_reconciliation"
    ]
    summary = build_run_summary(
        dag_id=DAG_ID,
        run_id=dag_run.run_id,
        started_at=str(dag_run.start_date),
        completed_at=str(datetime.utcnow()),
        task_instances=task_instances,
        conf=dict(context["params"]),
    )
    reports_dir = get_project_root() / "reports" / "airflow"
    written = write_run_summary(summary, reports_dir)
    raise_if_run_failed(summary)  # keeps the DAG run's own state accurate -- see docstring
    return {
        "final_status": summary["final_status"],
        "failed_tasks": summary["failed_tasks"],
        "report_paths": {k: str(v) for k, v in written.items()},
    }


with DAG(
    dag_id=DAG_ID,
    description="Full CareFlow pipeline: raw data -> Bronze -> Silver -> Gold -> PostgreSQL -> dbt",
    schedule=None,
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    tags=["careflow", "healthcare", "data-engineering", "end-to-end"],
    params=PARAMS,
    default_args=DEFAULT_ARGS,
    on_success_callback=dag_success_callback,
    on_failure_callback=dag_failure_callback,
) as dag:

    environment_check = PythonOperator(
        task_id="environment_check",
        python_callable=_environment_check,
    )

    decide_generate_data = BranchPythonOperator(
        task_id="decide_generate_data",
        python_callable=_decide_generate_data,
    )

    setup_synthea = CareFlowCommandOperator(
        task_id="setup_synthea",
        command_key="setup_synthea",
        timeout_seconds=600,
        retries=1,
    )

    generate_synthetic_data = CareFlowCommandOperator(
        task_id="generate_synthetic_data",
        command_key="generate_synthetic_data",
        extra_args=_generate_synthetic_data_extra_args,
        timeout_seconds=7200,  # first run builds Synthea via Gradle; can be slow
        retries=1,
    )

    skip_generate_data = EmptyOperator(task_id="skip_generate_data")

    profile_raw_data = CareFlowCommandOperator(
        task_id="profile_raw_data",
        command_key="profile_raw_data",
        trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS,
    )

    validate_raw_data = CareFlowCommandOperator(
        task_id="validate_raw_data",
        command_key="validate_raw_data",
    )

    ingest_bronze = CareFlowCommandOperator(
        task_id="ingest_bronze",
        command_key="ingest_bronze",
    )

    build_silver = CareFlowCommandOperator(
        task_id="build_silver",
        command_key="build_silver",
        extra_args=lambda **c: ["--force"] if _params(c)["force_silver"] else [],
    )

    build_gold = CareFlowCommandOperator(
        task_id="build_gold",
        command_key="build_gold",
        extra_args=lambda **c: ["--force"] if _params(c)["force_gold"] else [],
    )

    ensure_postgres_ready = CareFlowCommandOperator(
        task_id="ensure_postgres_ready",
        command_key="start_postgres",
        retries=3,
        retry_delay=timedelta(seconds=30),
        execution_timeout=timedelta(minutes=5),
    )

    load_postgres = CareFlowCommandOperator(
        task_id="load_postgres",
        command_key="load_postgres_warehouse",
        extra_args=lambda **c: ["--force"] if _params(c)["force_warehouse"] else [],
    )

    validate_postgres = CareFlowCommandOperator(
        task_id="validate_postgres",
        command_key="validate_postgres_warehouse",
    )

    dbt_seed = CareFlowCommandOperator(
        task_id="dbt_seed",
        command_key="run_dbt_seed",
    )

    decide_dbt_snapshot = BranchPythonOperator(
        task_id="decide_dbt_snapshot",
        python_callable=_decide_dbt_snapshot,
    )

    dbt_snapshot = CareFlowCommandOperator(
        task_id="dbt_snapshot",
        command_key="run_dbt_snapshot",
    )

    skip_dbt_snapshot = EmptyOperator(task_id="skip_dbt_snapshot")

    dbt_build = CareFlowCommandOperator(
        task_id="dbt_build",
        command_key="run_dbt_build",
        retries=1,
        execution_timeout=timedelta(minutes=20),
        trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS,
    )

    decide_dbt_docs = BranchPythonOperator(
        task_id="decide_dbt_docs",
        python_callable=_decide_dbt_docs,
    )

    dbt_docs = CareFlowCommandOperator(
        task_id="dbt_docs",
        command_key="run_dbt_docs",
    )

    skip_dbt_docs = EmptyOperator(task_id="skip_dbt_docs")

    final_reconciliation = PythonOperator(
        task_id="final_reconciliation",
        python_callable=_final_reconciliation,
        trigger_rule=TriggerRule.ALL_DONE,
    )

    # -- dependency graph -----------------------------------------------
    environment_check >> decide_generate_data
    decide_generate_data >> setup_synthea >> generate_synthetic_data >> profile_raw_data
    decide_generate_data >> skip_generate_data >> profile_raw_data

    profile_raw_data >> validate_raw_data >> ingest_bronze >> build_silver >> build_gold
    build_gold >> ensure_postgres_ready >> load_postgres >> validate_postgres
    validate_postgres >> dbt_seed >> decide_dbt_snapshot

    decide_dbt_snapshot >> dbt_snapshot >> dbt_build
    decide_dbt_snapshot >> skip_dbt_snapshot >> dbt_build

    dbt_build >> decide_dbt_docs
    decide_dbt_docs >> dbt_docs >> final_reconciliation
    decide_dbt_docs >> skip_dbt_docs >> final_reconciliation
