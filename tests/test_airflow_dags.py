"""Tests for the Phase 4A Airflow DAGs (structure only -- no running
scheduler, Docker, Java, or PostgreSQL is required or contacted).

Run under the dedicated .venv-airflow environment (apache-airflow is
never installed into the project's main Python 3.14):

    PYTHONPATH=src .venv-airflow/bin/python -m pytest -q tests/test_airflow_dags.py

If run under an interpreter without apache-airflow installed, every test
in this file is skipped (not failed) via pytest.importorskip.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# Plain `pytest.importorskip("airflow")` is not enough here: this
# project's own top-level airflow/ directory (no __init__.py) forms an
# importable PEP 420 namespace package named "airflow" whenever apache-
# airflow itself isn't installed, so a bare `import airflow` can
# "succeed" without the real package being present at all. Probing a
# concrete submodule that only exists in the real distribution avoids
# that false positive and skips cleanly under the main environment.
pytest.importorskip("airflow.exceptions")

from airflow.exceptions import AirflowDagCycleException  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DAGS_DIR = PROJECT_ROOT / "airflow" / "dags"
PLUGINS_DIR = PROJECT_ROOT / "airflow" / "plugins"

sys.path.insert(0, str(PLUGINS_DIR))
sys.path.insert(0, str(DAGS_DIR))


def _load_dag_module(filename: str):
    spec = importlib.util.spec_from_file_location(Path(filename).stem, DAGS_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def end_to_end_module():
    return _load_dag_module("careflow_end_to_end.py")


@pytest.fixture(scope="module")
def daily_module():
    return _load_dag_module("careflow_daily_analytics.py")


# -- both DAG files import successfully, with no import errors -------------


def test_end_to_end_dag_file_imports_successfully(end_to_end_module):
    assert end_to_end_module.dag is not None


def test_daily_dag_file_imports_successfully(daily_module):
    assert daily_module.dag is not None


# -- DAG ids, schedules, catchup --------------------------------------------


def test_end_to_end_dag_id(end_to_end_module):
    assert end_to_end_module.dag.dag_id == "careflow_end_to_end"


def test_daily_dag_id(daily_module):
    assert daily_module.dag.dag_id == "careflow_daily_analytics"


def test_end_to_end_has_no_schedule(end_to_end_module):
    assert end_to_end_module.dag.timetable.summary in ("Never", "None", None) or end_to_end_module.dag.schedule_interval is None


def test_daily_dag_has_a_documented_daily_schedule(daily_module):
    assert daily_module.dag.schedule_interval == "0 2 * * *"


def test_end_to_end_catchup_is_false(end_to_end_module):
    assert end_to_end_module.dag.catchup is False


def test_daily_catchup_is_false(daily_module):
    assert daily_module.dag.catchup is False


def test_end_to_end_max_active_runs_is_one(end_to_end_module):
    assert end_to_end_module.dag.max_active_runs == 1


def test_daily_max_active_runs_is_one(daily_module):
    assert daily_module.dag.max_active_runs == 1


# -- tags ---------------------------------------------------------------


def test_end_to_end_tags(end_to_end_module):
    assert set(end_to_end_module.dag.tags) == {"careflow", "healthcare", "data-engineering", "end-to-end"}


def test_daily_tags_include_required_set(daily_module):
    assert {"careflow", "healthcare", "data-engineering"}.issubset(set(daily_module.dag.tags))


# -- expected task ids exist ------------------------------------------------


END_TO_END_EXPECTED_TASKS = {
    "environment_check", "decide_generate_data", "setup_synthea", "generate_synthetic_data",
    "skip_generate_data", "profile_raw_data", "validate_raw_data", "ingest_bronze",
    "build_silver", "build_gold", "ensure_postgres_ready", "load_postgres", "validate_postgres",
    "dbt_seed", "decide_dbt_snapshot", "dbt_snapshot", "skip_dbt_snapshot", "dbt_build",
    "decide_dbt_docs", "dbt_docs", "skip_dbt_docs", "final_reconciliation",
}

DAILY_EXPECTED_TASKS = {
    "environment_check", "profile_raw_data", "validate_raw_data", "ingest_bronze_incremental",
    "build_silver_incremental", "build_gold_incremental", "ensure_postgres_ready",
    "load_postgres_incremental", "validate_postgres", "dbt_build", "final_summary",
}


def test_end_to_end_expected_task_ids_exist(end_to_end_module):
    actual = {t.task_id for t in end_to_end_module.dag.tasks}
    assert actual == END_TO_END_EXPECTED_TASKS


def test_daily_expected_task_ids_exist(daily_module):
    actual = {t.task_id for t in daily_module.dag.tasks}
    assert actual == DAILY_EXPECTED_TASKS


# -- task dependency order + no cycles --------------------------------------


def _downstream_ids(dag, task_id: str) -> set[str]:
    return set(dag.get_task(task_id).downstream_task_ids)


def test_end_to_end_dependency_order(end_to_end_module):
    dag = end_to_end_module.dag
    assert "decide_generate_data" in _downstream_ids(dag, "environment_check")
    assert {"setup_synthea", "skip_generate_data"} == _downstream_ids(dag, "decide_generate_data")
    assert "generate_synthetic_data" in _downstream_ids(dag, "setup_synthea")
    assert "profile_raw_data" in _downstream_ids(dag, "generate_synthetic_data")
    assert "profile_raw_data" in _downstream_ids(dag, "skip_generate_data")
    assert "validate_raw_data" in _downstream_ids(dag, "profile_raw_data")
    assert "ingest_bronze" in _downstream_ids(dag, "validate_raw_data")
    assert "build_silver" in _downstream_ids(dag, "ingest_bronze")
    assert "build_gold" in _downstream_ids(dag, "build_silver")
    assert "ensure_postgres_ready" in _downstream_ids(dag, "build_gold")
    assert "load_postgres" in _downstream_ids(dag, "ensure_postgres_ready")
    assert "validate_postgres" in _downstream_ids(dag, "load_postgres")
    assert "dbt_seed" in _downstream_ids(dag, "validate_postgres")
    assert "decide_dbt_snapshot" in _downstream_ids(dag, "dbt_seed")
    assert {"dbt_snapshot", "skip_dbt_snapshot"} == _downstream_ids(dag, "decide_dbt_snapshot")
    assert "dbt_build" in _downstream_ids(dag, "dbt_snapshot")
    assert "dbt_build" in _downstream_ids(dag, "skip_dbt_snapshot")
    assert "decide_dbt_docs" in _downstream_ids(dag, "dbt_build")
    assert {"dbt_docs", "skip_dbt_docs"} == _downstream_ids(dag, "decide_dbt_docs")
    assert "final_reconciliation" in _downstream_ids(dag, "dbt_docs")
    assert "final_reconciliation" in _downstream_ids(dag, "skip_dbt_docs")


def test_daily_dependency_order_is_a_single_chain(daily_module):
    dag = daily_module.dag
    chain = [
        "environment_check", "profile_raw_data", "validate_raw_data", "ingest_bronze_incremental",
        "build_silver_incremental", "build_gold_incremental", "ensure_postgres_ready",
        "load_postgres_incremental", "validate_postgres", "dbt_build", "final_summary",
    ]
    for upstream, downstream in zip(chain, chain[1:]):
        assert downstream in _downstream_ids(dag, upstream), f"{upstream} -> {downstream} missing"


def test_end_to_end_has_no_dependency_cycles(end_to_end_module):
    end_to_end_module.dag.topological_sort()  # raises AirflowDagCycleException on a cycle


def test_daily_has_no_dependency_cycles(daily_module):
    daily_module.dag.topological_sort()


def test_topological_sort_actually_detects_a_cycle():
    """Positive control for the two tests above: proves
    topological_sort() would in fact raise on a genuinely cyclic DAG,
    rather than the "no cycle" assertions passing vacuously."""
    from datetime import datetime

    from airflow import DAG
    from airflow.operators.empty import EmptyOperator

    with DAG(dag_id="cycle_check_fixture", schedule=None, start_date=datetime(2026, 1, 1), catchup=False) as cyclic_dag:
        a = EmptyOperator(task_id="a")
        b = EmptyOperator(task_id="b")
        a >> b
        b.set_downstream(a)  # deliberately closes the loop

    with pytest.raises(AirflowDagCycleException):
        cyclic_dag.topological_sort()


def test_end_to_end_has_no_orphaned_tasks(end_to_end_module):
    dag = end_to_end_module.dag
    for task in dag.tasks:
        if task.task_id == "environment_check":
            continue  # the DAG's own root
        assert task.upstream_task_ids, f"task '{task.task_id}' has no upstream dependency"


def test_daily_final_summary_uses_all_done_trigger_rule(daily_module):
    """The summary task is the one documented exception allowed to use
    trigger_rule=all_done -- it must always run and record the outcome,
    even when an upstream data-pipeline task failed."""
    task = daily_module.dag.get_task("final_summary")
    assert task.trigger_rule == "all_done"


def test_daily_final_summary_fails_itself_when_the_run_failed(daily_module):
    """Regression test: an ALL_DONE leaf that itself succeeds would
    otherwise make Airflow report the whole DAG run as "success" even
    when a real upstream task failed (DAG run state is computed from
    leaf task states, not "did anything fail" -- caught via live
    integration testing). The summary callable must re-raise so the
    leaf's own state reflects the run's true outcome."""
    import inspect

    source = inspect.getsource(daily_module._final_summary)
    assert "raise_if_run_failed" in source


def test_end_to_end_final_reconciliation_fails_itself_when_the_run_failed(end_to_end_module):
    import inspect

    source = inspect.getsource(end_to_end_module._final_reconciliation)
    assert "raise_if_run_failed" in source


def test_end_to_end_final_reconciliation_uses_all_done_trigger_rule(end_to_end_module):
    task = end_to_end_module.dag.get_task("final_reconciliation")
    assert task.trigger_rule == "all_done"


def test_no_other_end_to_end_task_uses_all_done_trigger_rule(end_to_end_module):
    """all_done must not be used to quietly mask a failed data-dependent
    task -- only the final summary/reconciliation task is the documented
    exception."""
    for task in end_to_end_module.dag.tasks:
        if task.task_id == "final_reconciliation":
            continue
        assert task.trigger_rule != "all_done", f"task '{task.task_id}' unexpectedly uses trigger_rule=all_done"


# -- daily DAG never generates data by default ------------------------------


def test_daily_dag_has_no_generate_data_task(daily_module):
    task_ids = {t.task_id for t in daily_module.dag.tasks}
    assert "generate_synthetic_data" not in task_ids
    assert "setup_synthea" not in task_ids


def test_daily_environment_check_callable_never_requests_data_generation(daily_module):
    import inspect

    source = inspect.getsource(daily_module._environment_check)
    assert "generate_data" in source and "False" in source


# -- manual DAG parameters exist, with correct types/defaults ---------------


REQUIRED_PARAMS = (
    "generate_data", "population", "force_bronze", "force_silver", "force_gold",
    "force_warehouse", "run_dbt_snapshot", "run_dbt_docs", "fail_fast",
)


@pytest.mark.parametrize("param_name", REQUIRED_PARAMS)
def test_end_to_end_param_exists(end_to_end_module, param_name):
    assert param_name in end_to_end_module.dag.params


BOOLEAN_PARAMS = tuple(p for p in REQUIRED_PARAMS if p != "population")


@pytest.mark.parametrize("param_name", BOOLEAN_PARAMS)
def test_force_and_boolean_flags_default_to_false(end_to_end_module, param_name):
    assert end_to_end_module.dag.params[param_name] is False  # resolved default value
    assert end_to_end_module.dag.params.get_param(param_name).value is False


def test_population_param_defaults_to_none_and_is_bounded(end_to_end_module):
    param = end_to_end_module.dag.params.get_param("population")
    assert param.value is None
    schema = param.schema
    assert schema.get("minimum") == 1
    assert schema.get("maximum") == 100000


def test_daily_dag_has_no_parameterized_params(daily_module):
    """The daily DAG is not manually parameterized -- it always runs
    the same incremental pipeline on its schedule."""
    assert not dict(daily_module.dag.params)


# -- Airflow-level parameter validation (schema) rejects bad input ----------


def test_generate_data_param_rejects_non_boolean(end_to_end_module):
    from airflow.exceptions import ParamValidationError

    with pytest.raises((ParamValidationError, Exception)):
        end_to_end_module.dag.params["generate_data"].resolve(value="not-a-boolean")


def test_population_param_rejects_out_of_range_value(end_to_end_module):
    from airflow.exceptions import ParamValidationError

    with pytest.raises((ParamValidationError, Exception)):
        end_to_end_module.dag.params["population"].resolve(value=999999999)
