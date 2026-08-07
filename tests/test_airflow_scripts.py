"""Tests for the Phase 4A orchestration scripts and the shared
airflow/plugins/careflow_operators.py engine behind them.

Everything here is mocked: no running Airflow scheduler, Docker, Java,
or PostgreSQL is required or contacted. careflow_operators.py degrades
gracefully without apache-airflow installed (BaseOperator/AirflowException
stubs), so -- unlike test_airflow_dags.py -- this file also runs under
the project's main Python. The required command still targets
.venv-airflow for consistency with the other Airflow test files.
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "airflow" / "plugins"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import careflow_operators as co  # noqa: E402


# -- command registry / allow-list -------------------------------------------


EXPECTED_COMMANDS = {
    "setup_synthea", "generate_synthetic_data", "profile_raw_data", "validate_raw_data",
    "ingest_bronze", "build_silver", "build_gold", "start_postgres", "load_postgres_warehouse",
    "validate_postgres_warehouse", "run_dbt_seed", "run_dbt_snapshot", "run_dbt_build", "run_dbt_docs",
}


def test_command_registry_has_exactly_the_expected_commands():
    assert set(co.COMMAND_REGISTRY.keys()) == EXPECTED_COMMANDS


def test_command_registry_entries_are_argument_lists_not_strings():
    for command_key, argv in co.COMMAND_REGISTRY.items():
        assert isinstance(argv, list), f"{command_key} must be an argument list"
        assert all(isinstance(part, str) for part in argv)


def test_validate_extra_args_blocks_unapproved_flags():
    with pytest.raises(ValueError):
        co.validate_extra_args("build_gold", ["--rm", "-rf", "/"])


def test_validate_extra_args_blocks_unapproved_command():
    with pytest.raises(ValueError):
        co.validate_extra_args("drop_database", [])


def test_validate_extra_args_allows_force_only_where_declared():
    assert co.validate_extra_args("build_gold", ["--force"]) == ["--force"]
    with pytest.raises(ValueError):
        co.validate_extra_args("ingest_bronze", ["--force"])  # ingest_bronze.py has no --force flag


def test_validate_extra_args_validates_population_bounds():
    assert co.validate_extra_args("generate_synthetic_data", ["--population", "500"]) == ["--population", "500"]
    with pytest.raises(ValueError):
        co.validate_extra_args("generate_synthetic_data", ["--population", "0"])
    with pytest.raises(ValueError):
        co.validate_extra_args("generate_synthetic_data", ["--population", "999999999"])
    with pytest.raises(ValueError):
        co.validate_extra_args("generate_synthetic_data", ["--population", "not-a-number"])


def test_command_operator_rejects_unapproved_command_key_at_construction():
    with pytest.raises(ValueError):
        co.CareFlowCommandOperator(task_id="t", command_key="rm_rf_everything")


# -- CareFlowCommandOperator.execute(): subprocess safety, XCom compactness -


class FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_execute_calls_subprocess_run_with_an_argument_list(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return FakeCompletedProcess(returncode=0, stdout="ok")

    monkeypatch.setattr(co.subprocess, "run", fake_run)
    op = co.CareFlowCommandOperator(task_id="t", command_key="build_gold", extra_args=["--force"])
    op.execute({})

    assert isinstance(captured["argv"], list)
    assert captured["argv"][-1] == "--force"
    assert "shell" not in captured["kwargs"] or captured["kwargs"]["shell"] is False


def test_execute_never_uses_shell_true():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(co))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for keyword in getattr(node, "keywords", []):
                if keyword.arg == "shell":
                    assert not (isinstance(keyword.value, ast.Constant) and keyword.value.value is True)


def test_execute_raises_on_nonzero_return_code(monkeypatch):
    monkeypatch.setattr(co.subprocess, "run", lambda *a, **k: FakeCompletedProcess(returncode=1, stderr="boom"))
    op = co.CareFlowCommandOperator(task_id="t", command_key="build_gold")
    with pytest.raises(co.AirflowException):
        op.execute({})


def test_execute_xcom_payload_is_compact(monkeypatch):
    huge_stdout = "\n".join(f"line {i}" for i in range(5000))
    monkeypatch.setattr(co.subprocess, "run", lambda *a, **k: FakeCompletedProcess(returncode=0, stdout=huge_stdout))
    op = co.CareFlowCommandOperator(task_id="t", command_key="build_gold")
    result = op.execute({})

    assert set(result.keys()) == {"command_key", "returncode", "duration_seconds", "stdout_tail"}
    assert len(result["stdout_tail"]) < len(huge_stdout)
    assert result["stdout_tail"].count("\n") <= 16  # at most ~15 lines + truncation marker


def test_execute_redacts_password_from_captured_output(monkeypatch):
    monkeypatch.setattr(
        co.subprocess, "run",
        lambda *a, **k: FakeCompletedProcess(returncode=0, stdout="connecting with password=hunter2secret now"),
    )
    op = co.CareFlowCommandOperator(task_id="t", command_key="build_gold")
    result = op.execute({})
    assert "hunter2secret" not in result["stdout_tail"]
    assert "password=***" in result["stdout_tail"]


def test_execute_extra_args_callable_is_resolved_and_validated_at_execute_time(monkeypatch):
    monkeypatch.setattr(co.subprocess, "run", lambda argv, **k: (_ for _ in ()).throw(AssertionError(f"unexpected argv {argv}")) if "--force" not in argv else FakeCompletedProcess(0))
    op = co.CareFlowCommandOperator(task_id="t", command_key="build_gold", extra_args=lambda **c: ["--force"])
    op.execute({})  # must not raise -- callable resolved to a validated ["--force"]


def test_execute_extra_args_callable_result_is_still_validated(monkeypatch):
    op = co.CareFlowCommandOperator(task_id="t", command_key="build_gold", extra_args=lambda **c: ["--not-allowed"])
    with pytest.raises(ValueError):
        op.execute({})


def test_execute_times_out_and_raises_airflow_exception(monkeypatch):
    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr(co.subprocess, "run", fake_run)
    op = co.CareFlowCommandOperator(task_id="t", command_key="build_gold", timeout_seconds=1)
    with pytest.raises(co.AirflowException):
        op.execute({})


def test_execute_does_not_touch_upstream_data_directories(monkeypatch, tmp_path):
    """With subprocess mocked out, executing the operator must never
    itself read or write anything under data/ -- only the (mocked)
    subprocess would."""
    monkeypatch.setattr(co.subprocess, "run", lambda *a, **k: FakeCompletedProcess(returncode=0))
    for layer in ("raw", "bronze", "silver", "gold"):
        layer_dir = PROJECT_ROOT / "data" / layer
        if layer_dir.is_dir():
            sample = next(layer_dir.rglob("*"), None)
            if sample and sample.is_file():
                before = sample.read_bytes()
                op = co.CareFlowCommandOperator(task_id="t", command_key="build_gold")
                op.execute({})
                assert sample.read_bytes() == before


# -- environment check --------------------------------------------------


def test_run_environment_check_passes_with_all_checks_ok(monkeypatch):
    monkeypatch.setattr(co, "_command_available", lambda *a, **k: True)
    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.setenv("POSTGRES_PORT", "5433")
    monkeypatch.setenv("POSTGRES_DB", "careflow")
    monkeypatch.setenv("POSTGRES_USER", "careflow_user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "x")
    result = co.run_environment_check({"generate_data": False})
    assert result["checks_failed"] == 0
    assert result["checks_passed"] > 0


def test_run_environment_check_raises_on_missing_postgres_env_vars(monkeypatch):
    monkeypatch.setattr(co, "_command_available", lambda *a, **k: True)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    with pytest.raises(co.AirflowException):
        co.run_environment_check({"generate_data": False})


def test_run_environment_check_requires_java_and_git_only_when_generating_data(monkeypatch):
    calls = []

    def fake_command_available(argv, timeout=10):
        calls.append(argv[0])
        return True

    monkeypatch.setattr(co, "_command_available", fake_command_available)
    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.setenv("POSTGRES_PORT", "5433")
    monkeypatch.setenv("POSTGRES_DB", "careflow")
    monkeypatch.setenv("POSTGRES_USER", "careflow_user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "x")

    co.run_environment_check({"generate_data": False})
    assert "java" not in calls
    assert "git" not in calls

    calls.clear()
    co.run_environment_check({"generate_data": True})
    assert "java" in calls
    assert "git" in calls


def test_environment_check_never_logs_the_password_value():
    import inspect

    source = inspect.getsource(co.run_environment_check)
    assert "POSTGRES_PASSWORD]" not in source  # never indexes/prints the value directly
    assert "os.environ.get(\"POSTGRES_PASSWORD\")" not in source or "missing_env" in source


# -- DAG-run parameter validation --------------------------------------------


def test_validate_dag_run_params_defaults():
    result = co.validate_dag_run_params({})
    assert all(result[name] is False for name in co.BOOLEAN_PARAMS)
    assert result["population"] is None


def test_validate_dag_run_params_rejects_non_boolean():
    with pytest.raises(ValueError):
        co.validate_dag_run_params({"generate_data": "yes"})


def test_validate_dag_run_params_rejects_out_of_range_population():
    with pytest.raises(ValueError):
        co.validate_dag_run_params({"population": 0})
    with pytest.raises(ValueError):
        co.validate_dag_run_params({"population": 10_000_000})


def test_validate_dag_run_params_rejects_bool_disguised_as_population():
    with pytest.raises(ValueError):
        co.validate_dag_run_params({"population": True})


# -- run summary (success + failure paths) -----------------------------


def test_build_run_summary_success_path():
    task_instances = [
        {"task_id": "a", "state": "success", "duration": 1.2, "try_number": 1},
        {"task_id": "b", "state": "success", "duration": 0.5, "try_number": 1},
    ]
    summary = co.build_run_summary("careflow_daily_analytics", "run-1", "t0", "t1", task_instances)
    assert summary["final_status"] == "success"
    assert summary["failed_tasks"] == []


def test_build_run_summary_failure_path():
    task_instances = [
        {"task_id": "a", "state": "success", "duration": 1.2, "try_number": 1},
        {"task_id": "b", "state": "failed", "duration": 0.5, "try_number": 2},
    ]
    summary = co.build_run_summary("careflow_end_to_end", "run-2", "t0", "t1", task_instances)
    assert summary["final_status"] == "failed"
    assert summary["failed_tasks"] == ["b"]


def test_build_run_summary_sanitizes_conf_to_known_keys_only():
    summary = co.build_run_summary(
        "careflow_end_to_end", "run-3", "t0", "t1", [],
        conf={"generate_data": True, "population": 50, "unexpected_key": "whatever", "patient_ssn": "111-22-3333"},
    )
    assert summary["conf"] == {"generate_data": True, "population": 50}
    assert "unexpected_key" not in summary["conf"]
    assert "patient_ssn" not in json.dumps(summary)


def test_write_run_summary_writes_run_and_task_csv_on_success(tmp_path):
    summary = co.build_run_summary(
        "careflow_daily_analytics", "run-4", "t0", "t1",
        [{"task_id": "a", "state": "success", "duration": 1.0, "try_number": 1}],
    )
    written = co.write_run_summary(summary, tmp_path)
    assert "run_summary" in written and written["run_summary"].is_file()
    assert "task_summary" in written and written["task_summary"].is_file()
    assert "failure_summary" not in written
    assert not (tmp_path / "airflow_failure_summary.json").exists()

    with written["task_summary"].open() as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["task_id"] == "a"


def test_write_run_summary_writes_failure_summary_on_failure(tmp_path):
    summary = co.build_run_summary(
        "careflow_end_to_end", "run-5", "t0", "t1",
        [{"task_id": "a", "state": "failed", "duration": 1.0, "try_number": 1}],
    )
    written = co.write_run_summary(summary, tmp_path)
    assert "failure_summary" in written
    payload = json.loads(written["failure_summary"].read_text())
    assert payload["failed_tasks"] == ["a"]
    assert payload["final_status"] == "failed"


# -- raise_if_run_failed: keeps the DAG run's own leaf-based state accurate -


def test_raise_if_run_failed_raises_when_a_task_failed():
    summary = co.build_run_summary(
        "careflow_end_to_end", "run-7", "t0", "t1",
        [{"task_id": "ensure_postgres_ready", "state": "failed", "duration": 1.0, "try_number": 4}],
    )
    with pytest.raises(co.AirflowException):
        co.raise_if_run_failed(summary)


def test_raise_if_run_failed_does_not_raise_on_success():
    summary = co.build_run_summary(
        "careflow_end_to_end", "run-8", "t0", "t1",
        [{"task_id": "dbt_build", "state": "success", "duration": 1.0, "try_number": 1}],
    )
    co.raise_if_run_failed(summary)  # must not raise


def test_run_summary_never_contains_pii_fields():
    """Task instance dicts are structurally limited to task_id/state/
    duration/try_number -- there is no field a patient record could
    ever end up in."""
    summary = co.build_run_summary(
        "careflow_end_to_end", "run-6", "t0", "t1",
        [{"task_id": "load_postgres", "state": "success", "duration": 2.0, "try_number": 1}],
    )
    dumped = json.dumps(summary)
    for forbidden in ("ssn", "patient_id", "first_name", "last_name", "date_of_birth"):
        assert forbidden not in dumped.lower()


# -- executable resolution overrides -----------------------------------


def test_get_python_executable_respects_override(monkeypatch):
    monkeypatch.setenv("CAREFLOW_PYTHON_BIN", "/usr/bin/python3.11")
    assert co.get_python_executable() == "/usr/bin/python3.11"


def test_get_dbt_executable_respects_override(monkeypatch):
    monkeypatch.setenv("CAREFLOW_DBT_BIN", "dbt")
    assert co.get_dbt_executable() == "dbt"


def test_get_project_root_respects_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CAREFLOW_PROJECT_ROOT", str(tmp_path))
    assert co.get_project_root() == tmp_path


# -- scripts/trigger_careflow_dag.py -----------------------------------


import trigger_careflow_dag as trigger_mod  # noqa: E402


def test_trigger_script_builds_a_validated_conf():
    args = trigger_mod.parse_args(["--generate-data", "--population", "250"])
    conf = trigger_mod.build_conf(args)
    assert conf["generate_data"] is True
    assert conf["population"] == 250


def test_trigger_script_rejects_out_of_range_population():
    args = trigger_mod.parse_args(["--population", "999999999"])
    with pytest.raises(ValueError):
        trigger_mod.build_conf(args)


def test_trigger_script_restricts_dag_id_choices():
    with pytest.raises(SystemExit):
        trigger_mod.parse_args(["--dag-id", "some_other_dag"])


def test_trigger_script_uses_subprocess_argument_list(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return FakeCompletedProcess(returncode=0, stdout="Triggered")

    monkeypatch.setattr(trigger_mod.subprocess, "run", fake_run)
    exit_code = trigger_mod.main(["--generate-data"])
    assert exit_code == 0
    assert isinstance(captured["argv"], list)
    assert captured["argv"][:2] == ["docker", "compose"]
    assert "--conf" in captured["argv"]
    conf_index = captured["argv"].index("--conf") + 1
    conf = json.loads(captured["argv"][conf_index])
    assert conf["generate_data"] is True


def test_trigger_script_returns_nonzero_on_docker_failure(monkeypatch):
    monkeypatch.setattr(
        trigger_mod.subprocess, "run",
        lambda *a, **k: FakeCompletedProcess(returncode=1, stderr="no such service"),
    )
    assert trigger_mod.main([]) == 1


def test_trigger_script_returns_nonzero_on_invalid_params():
    assert trigger_mod.main(["--population", "-5"]) == 1


# -- scripts/check_pipeline_status.py ------------------------------------


import check_pipeline_status as status_mod  # noqa: E402


def test_check_status_uses_subprocess_argument_list(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return FakeCompletedProcess(
            returncode=0,
            stdout=json.dumps([{"run_id": "run-1", "state": "success"}]),
        )

    monkeypatch.setattr(status_mod.subprocess, "run", fake_run)
    exit_code = status_mod.main([])
    assert exit_code == 0
    assert isinstance(captured["argv"], list)
    assert captured["argv"][:2] == ["docker", "compose"]


def test_check_status_returns_nonzero_when_latest_run_failed(monkeypatch):
    monkeypatch.setattr(
        status_mod.subprocess, "run",
        lambda *a, **k: FakeCompletedProcess(returncode=0, stdout=json.dumps([{"run_id": "run-2", "state": "failed"}])),
    )
    assert status_mod.main([]) == 1


def test_check_status_returns_nonzero_when_no_runs_found(monkeypatch):
    monkeypatch.setattr(status_mod.subprocess, "run", lambda *a, **k: FakeCompletedProcess(returncode=0, stdout="[]"))
    assert status_mod.main([]) == 1


def test_check_status_returns_nonzero_on_docker_error(monkeypatch):
    monkeypatch.setattr(status_mod.subprocess, "run", lambda *a, **k: FakeCompletedProcess(returncode=1, stderr="not running"))
    assert status_mod.main([]) == 1


# -- scripts/start_airflow.py ---------------------------------------------


import start_airflow as start_mod  # noqa: E402


def test_start_airflow_returns_nonzero_when_docker_unavailable(monkeypatch):
    monkeypatch.setattr(start_mod, "_docker_available", lambda: False)
    assert start_mod.main([]) == 1


def test_start_airflow_uses_subprocess_argument_lists(monkeypatch):
    monkeypatch.setattr(start_mod, "_docker_available", lambda: True)
    captured_calls = []

    def fake_run(argv, cwd):
        captured_calls.append(argv)
        return FakeCompletedProcess(returncode=1, stderr="simulated failure")  # stop after the first call

    monkeypatch.setattr(start_mod, "_run", fake_run)
    start_mod.main([])
    assert captured_calls
    for call in captured_calls:
        assert isinstance(call, list)
    assert captured_calls[0][:3] == ["docker", "compose", "up"]


def test_start_airflow_returns_nonzero_when_postgres_start_fails(monkeypatch):
    monkeypatch.setattr(start_mod, "_docker_available", lambda: True)
    monkeypatch.setattr(start_mod, "_run", lambda *a, **k: FakeCompletedProcess(returncode=1, stderr="boom"))
    assert start_mod.main([]) == 1


def test_start_airflow_skip_careflow_postgres_flag(monkeypatch):
    monkeypatch.setattr(start_mod, "_docker_available", lambda: True)
    calls = []

    def fake_run(argv, cwd):
        calls.append(argv)
        return FakeCompletedProcess(returncode=1, stderr="stop after init")

    monkeypatch.setattr(start_mod, "_run", fake_run)
    start_mod.main(["--skip-careflow-postgres"])
    assert all(argv[:4] != ["docker", "compose", "up", "-d"] or "postgres" not in argv for argv in calls)
