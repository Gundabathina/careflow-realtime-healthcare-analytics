"""Shared operators and task logic for the CareFlow Airflow DAGs.

Everything here is a plain, dependency-light Python module (only the
standard library plus Airflow's BaseOperator/exceptions) so it can be
imported both by the DAG files running inside Airflow and, without a
running Airflow, by tests/test_airflow_dags.py and
tests/test_airflow_scripts.py.

Design rules enforced throughout this file:
  - subprocess is always called with an argument list, never a string,
    and never with shell=True.
  - Only commands in COMMAND_REGISTRY may be executed, and only the
    extra arguments declared allowed for that command may be appended --
    DAG-run parameters are validated against these allow-lists before
    ever reaching a command line.
  - Secrets (POSTGRES_PASSWORD, anything matching a password/DSN
    pattern) are stripped from anything logged or pushed to XCom.
  - XCom payloads are compact summaries (status, counts, paths, a short
    stdout tail) -- never full stdout, DataFrames, or report contents.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

try:
    from airflow.exceptions import AirflowException
    from airflow.models import BaseOperator
except ImportError:  # pragma: no cover - only exercised when airflow isn't installed
    AirflowException = RuntimeError

    class BaseOperator:  # type: ignore[no-redef]
        """Minimal stand-in so this module stays importable without Airflow installed."""

        def __init__(self, *, task_id: str, **kwargs: Any) -> None:
            import logging

            self.task_id = task_id
            self.log = logging.getLogger(f"careflow.operators.{task_id}")
            for key, value in kwargs.items():
                setattr(self, key, value)


# ---------------------------------------------------------------------------
# Project root resolution
# ---------------------------------------------------------------------------


def get_project_root() -> Path:
    """The CareFlow repository root.

    Inside the Airflow containers this is the bind-mounted repo, pointed
    at by CAREFLOW_PROJECT_ROOT. On a host machine running DAG-parsing
    tests directly, it's derived from this file's location
    (airflow/plugins/careflow_operators.py -> repo root).
    """
    override = os.environ.get("CAREFLOW_PROJECT_ROOT")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2]


def get_python_executable() -> str:
    """The Python interpreter used to run scripts/*.py.

    Overridable via CAREFLOW_PYTHON_BIN (used inside the Airflow
    container, where the project's runtime dependencies are installed
    into Airflow's own Python). Defaults to the interpreter currently
    running Airflow itself.
    """
    return os.environ.get("CAREFLOW_PYTHON_BIN") or __import__("sys").executable


def get_dbt_executable() -> str:
    """The dbt binary used by scripts/run_dbt.py's own resolution logic.

    Overridable via CAREFLOW_DBT_BIN (used inside the Airflow container,
    where dbt is installed into Airflow's own Python environment rather
    than the host-only .venv-dbt virtualenv -- a venv built from
    host-compiled binaries cannot run inside a Linux container). Falls
    back to the host's .venv-dbt, then to whatever `dbt` is on PATH.
    """
    override = os.environ.get("CAREFLOW_DBT_BIN")
    if override:
        return override
    venv_dbt = get_project_root() / ".venv-dbt" / "bin" / "dbt"
    if venv_dbt.is_file():
        return str(venv_dbt)
    found = shutil.which("dbt")
    return found or str(venv_dbt)


# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------

_PASSWORD_PATTERN = re.compile(r"password[=:]\S+", re.IGNORECASE)
_DSN_PATTERN = re.compile(r"://[^:@/\s]+:[^@/\s]+@")


def sanitize_text(text: str) -> str:
    """Strip anything resembling a password or embedded DSN, plus the
    literal configured POSTGRES_PASSWORD value if it's set, before the
    text is logged or pushed to XCom."""
    if not text:
        return text
    text = _PASSWORD_PATTERN.sub("password=***", text)
    text = _DSN_PATTERN.sub("://***:***@", text)
    real_password = os.environ.get("POSTGRES_PASSWORD")
    if real_password:
        text = text.replace(real_password, "***")
    return text


def _tail_lines(text: str, max_lines: int = 40) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join(["... (truncated) ..."] + lines[-max_lines:])


# ---------------------------------------------------------------------------
# Approved command registry -- the only commands CareFlowCommandOperator
# will ever execute, and the only extra flags each one may be given.
# ---------------------------------------------------------------------------


def _script(name: str) -> list[str]:
    return [get_python_executable(), str(Path("scripts") / name)]


def _dbt(subcommand: str) -> list[str]:
    # scripts/run_dbt.py resolves the dbt binary itself (via
    # get_dbt_executable's same override chain); it always calls dbt with
    # --project-dir/--profiles-dir, never a shell string.
    return [get_python_executable(), str(Path("scripts") / "run_dbt.py"), subcommand]


COMMAND_REGISTRY: dict[str, list[str]] = {
    "setup_synthea": _script("setup_synthea.py"),
    "generate_synthetic_data": _script("generate_synthea_data.py"),
    "profile_raw_data": _script("profile_synthea_data.py"),
    "validate_raw_data": _script("validate_synthea_data.py"),
    "ingest_bronze": _script("ingest_bronze.py"),
    "build_silver": _script("build_silver_layer.py"),
    "build_gold": _script("build_gold_layer.py"),
    "start_postgres": _script("start_postgres.py"),
    "load_postgres_warehouse": _script("load_postgres_warehouse.py"),
    "validate_postgres_warehouse": _script("validate_postgres_warehouse.py"),
    "run_dbt_seed": _dbt("seed"),
    "run_dbt_snapshot": _dbt("snapshot"),
    "run_dbt_build": _dbt("build"),
    "run_dbt_docs": _dbt("docs-generate"),
}

# ingest_bronze.py has no --force flag: bronze ingestion always
# reprocesses every gated-clear CSV (see src/careflow/bronze/ingest.py).
# force_bronze is still accepted as a DAG parameter for interface
# symmetry with the other layers, but is intentionally a no-op here.
ALLOWED_EXTRA_ARGS: dict[str, set[str]] = {
    "build_silver": {"--force"},
    "build_gold": {"--force"},
    "load_postgres_warehouse": {"--force"},
}

# Commands accepting a validated integer flag (name -> flag string).
ALLOWED_INT_ARGS: dict[str, str] = {
    "generate_synthetic_data": "--population",
}

DEFAULT_TIMEOUT_SECONDS = 900

MIN_POPULATION = 1
MAX_POPULATION = 100_000


def validate_extra_args(command_key: str, extra_args: list[str] | None) -> list[str]:
    """Reject anything not on that command's explicit allow-list.

    This is the single choke point that keeps DAG-run parameters from
    ever reaching a shell/subprocess argument list unvalidated. Boolean
    flags (e.g. ``--force``) must appear verbatim in ALLOWED_EXTRA_ARGS;
    the one integer-valued flag (``--population``) must be paired with a
    value that parses as an int within [MIN_POPULATION, MAX_POPULATION].
    """
    if command_key not in COMMAND_REGISTRY:
        raise ValueError(f"'{command_key}' is not an approved CareFlow command")
    if not extra_args:
        return []
    allowed_flags = ALLOWED_EXTRA_ARGS.get(command_key, set())
    int_flag = ALLOWED_INT_ARGS.get(command_key)

    validated: list[str] = []
    i = 0
    while i < len(extra_args):
        arg = extra_args[i]
        if arg in allowed_flags:
            validated.append(arg)
            i += 1
            continue
        if int_flag is not None and arg == int_flag:
            if i + 1 >= len(extra_args):
                raise ValueError(f"'{arg}' requires a value")
            value = extra_args[i + 1]
            if not re.fullmatch(r"-?\d+", value):
                raise ValueError(f"'{arg}' value must be an integer, got {value!r}")
            int_value = int(value)
            if not (MIN_POPULATION <= int_value <= MAX_POPULATION):
                raise ValueError(f"'{arg}' value must be between {MIN_POPULATION} and {MAX_POPULATION}")
            validated.extend([arg, str(int_value)])
            i += 2
            continue
        raise ValueError(f"'{arg}' is not an allowed argument for command '{command_key}'")
    return validated


# ---------------------------------------------------------------------------
# CareFlowCommandOperator
# ---------------------------------------------------------------------------


class CareFlowCommandOperator(BaseOperator):
    """Runs one approved CareFlow script/dbt subcommand as a subprocess.

    Only ``command_key`` values present in COMMAND_REGISTRY are ever
    executed; ``extra_args`` are validated against that command's
    explicit allow-list before being appended. Never uses shell=True.
    Pushes a compact, secret-redacted summary as this task's XCom
    return value; raises AirflowException (failing the task) on a
    non-zero exit code.

    ``extra_args`` may be a plain list (validated immediately, at DAG
    parse time) or a callable ``(**context) -> list[str]`` (resolved and
    validated at task-execution time) -- needed when the arguments
    depend on that specific run's dag_run.conf, which doesn't exist yet
    at parse time.
    """

    template_fields = ()

    def __init__(
        self,
        *,
        command_key: str,
        extra_args: list[str] | Any = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        if command_key not in COMMAND_REGISTRY:
            raise ValueError(f"'{command_key}' is not an approved CareFlow command")
        self.command_key = command_key
        self.timeout_seconds = timeout_seconds
        if callable(extra_args):
            self._extra_args_callable = extra_args
            self.extra_args: list[str] = []
        else:
            self._extra_args_callable = None
            self.extra_args = validate_extra_args(command_key, extra_args)

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        project_root = get_project_root()
        if self._extra_args_callable is not None:
            resolved = self._extra_args_callable(**context)
            extra_args = validate_extra_args(self.command_key, resolved)
        else:
            extra_args = self.extra_args
        argv = [*COMMAND_REGISTRY[self.command_key], *extra_args]

        env = os.environ.copy()
        env["PYTHONPATH"] = str(project_root / "src")
        env.setdefault("DBT_PROFILES_DIR", str(project_root))

        self.log.info("Running approved command '%s': %s", self.command_key, " ".join(argv))
        started = time.monotonic()
        try:
            result = subprocess.run(
                argv,
                cwd=str(project_root),
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise AirflowException(
                f"Command '{self.command_key}' timed out after {self.timeout_seconds}s"
            ) from exc
        duration = time.monotonic() - started

        stdout = sanitize_text(result.stdout or "")
        stderr = sanitize_text(result.stderr or "")
        if stdout.strip():
            self.log.info("[%s stdout]\n%s", self.command_key, _tail_lines(stdout))
        if stderr.strip():
            self.log.info("[%s stderr]\n%s", self.command_key, _tail_lines(stderr))

        summary = {
            "command_key": self.command_key,
            "returncode": result.returncode,
            "duration_seconds": round(duration, 3),
            "stdout_tail": _tail_lines(stdout, max_lines=15),
        }

        if result.returncode != 0:
            raise AirflowException(
                f"Command '{self.command_key}' failed with exit code {result.returncode}"
            )

        self.log.info("Command '%s' completed successfully in %.2fs", self.command_key, duration)
        return summary


# ---------------------------------------------------------------------------
# DAG-run parameter validation (defensive, in addition to Airflow Param types)
# ---------------------------------------------------------------------------

BOOLEAN_PARAMS = (
    "generate_data", "force_bronze", "force_silver", "force_gold",
    "force_warehouse", "run_dbt_snapshot", "run_dbt_docs", "fail_fast",
)


def validate_dag_run_params(params: dict[str, Any]) -> dict[str, Any]:
    """Re-validate every end-to-end DAG parameter defensively.

    Airflow's own Param(type=...) definitions already constrain what a
    UI/API trigger can submit, but this is re-checked here too: a
    parameter reaching this function is about to influence which
    subprocess arguments get built, so it is never trusted blindly.
    """
    validated: dict[str, Any] = {}
    for name in BOOLEAN_PARAMS:
        value = params.get(name, False)
        if not isinstance(value, bool):
            raise ValueError(f"Parameter '{name}' must be a boolean, got {type(value).__name__}")
        validated[name] = value

    population = params.get("population")
    if population is None:
        validated["population"] = None
    else:
        if isinstance(population, bool) or not isinstance(population, int):
            raise ValueError("Parameter 'population' must be an integer")
        if not (MIN_POPULATION <= population <= MAX_POPULATION):
            raise ValueError(f"Parameter 'population' must be between {MIN_POPULATION} and {MAX_POPULATION}")
        validated["population"] = population

    return validated


# ---------------------------------------------------------------------------
# Environment check
# ---------------------------------------------------------------------------

REQUIRED_SCRIPTS = (
    "profile_synthea_data.py", "validate_synthea_data.py", "ingest_bronze.py",
    "build_silver_layer.py", "build_gold_layer.py", "start_postgres.py",
    "load_postgres_warehouse.py", "validate_postgres_warehouse.py", "run_dbt.py",
    "generate_synthea_data.py", "setup_synthea.py",
)

REQUIRED_POSTGRES_ENV_VARS = ("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")


def _check(ok: bool, message: str, results: list[dict[str, Any]]) -> None:
    results.append({"check": message, "ok": ok})


def _command_available(argv: list[str], timeout: int = 10) -> bool:
    try:
        subprocess.run(argv, capture_output=True, timeout=timeout)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def run_environment_check(params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate the environment before any pipeline stage runs.

    Never logs credential values -- only whether each required
    POSTGRES_* variable is *present*.
    """
    params = validate_dag_run_params(params or {})
    project_root = get_project_root()
    results: list[dict[str, Any]] = []

    _check(project_root.is_dir(), f"repository root exists ({project_root})", results)

    for script_name in REQUIRED_SCRIPTS:
        _check((project_root / "scripts" / script_name).is_file(), f"scripts/{script_name} exists", results)

    _check(_command_available([get_python_executable(), "--version"]), "python executable works", results)

    if params.get("generate_data"):
        _check(_command_available(["java", "-version"]), "java available (required: generate_data=true)", results)
        _check(_command_available(["git", "--version"]), "git available (required: generate_data=true)", results)

    docker_ok = _command_available(["docker", "info"], timeout=15)
    _check(docker_ok, "docker daemon reachable", results)
    if docker_ok:
        health = subprocess.run(
            ["docker", "inspect", "--format={{.State.Health.Status}}", "careflow-postgres"],
            capture_output=True, text=True, timeout=15,
        )
        status = health.stdout.strip() if health.returncode == 0 else "not_running"
        _check(status in ("healthy", "starting", "not_running"), f"careflow-postgres container status: {status or 'unknown'}", results)

    _check((project_root / ".env").is_file(), ".env exists", results)

    missing_env = [name for name in REQUIRED_POSTGRES_ENV_VARS if not os.environ.get(name)]
    _check(not missing_env, f"required PostgreSQL env vars present (missing: {missing_env or 'none'})", results)

    dbt_bin = get_dbt_executable()
    dbt_bin_exists = Path(dbt_bin).is_file() or shutil.which(dbt_bin) is not None
    _check(dbt_bin_exists, f"dbt executable resolves ({dbt_bin})", results)
    if dbt_bin_exists:
        _check(_command_available([dbt_bin, "--version"], timeout=30), "dbt --version works", results)

    raw_csv_dir = project_root / "data" / "raw" / "synthea" / "csv"
    if not params.get("generate_data"):
        _check(raw_csv_dir.is_dir(), f"raw Synthea CSV directory exists ({raw_csv_dir}) -- required since generate_data=false", results)

    failed = [r for r in results if not r["ok"]]
    passed = [r for r in results if r["ok"]]
    if failed:
        raise AirflowException(
            "Environment check failed:\n" + "\n".join(f"  - {r['check']}" for r in failed)
        )

    return {"checks_passed": len(passed), "checks_failed": len(failed), "checks": results}


# ---------------------------------------------------------------------------
# Run summary / reconciliation report writer (used by both DAGs' final task)
# ---------------------------------------------------------------------------


def _sanitized_conf(conf: dict[str, Any] | None) -> dict[str, Any]:
    """dag_run.conf, restricted to the known boolean/int params -- never
    an unbounded echo of whatever a caller submitted."""
    conf = conf or {}
    out: dict[str, Any] = {}
    for name in BOOLEAN_PARAMS:
        if name in conf:
            out[name] = bool(conf[name])
    if "population" in conf and isinstance(conf["population"], int) and not isinstance(conf["population"], bool):
        out["population"] = conf["population"]
    return out


def build_run_summary(
    dag_id: str,
    run_id: str,
    started_at: str,
    completed_at: str,
    task_instances: list[dict[str, Any]],
    conf: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pure function (no Airflow context needed) building the run-summary dict.

    ``task_instances`` is a list of {"task_id","state","duration","try_number"}
    dicts -- callers extract this from Airflow's TaskInstance objects so
    this function stays independently testable.
    """
    status_counts: dict[str, int] = {}
    for ti in task_instances:
        state = ti.get("state") or "unknown"
        status_counts[state] = status_counts.get(state, 0) + 1
    failed_tasks = [ti["task_id"] for ti in task_instances if ti.get("state") == "failed"]
    final_status = "failed" if failed_tasks else "success"

    return {
        "dag_id": dag_id,
        "run_id": run_id,
        "started_at_utc": started_at,
        "completed_at_utc": completed_at,
        "conf": _sanitized_conf(conf),
        "task_count": len(task_instances),
        "status_counts": status_counts,
        "failed_tasks": failed_tasks,
        "final_status": final_status,
        "tasks": task_instances,
    }


def write_run_summary(summary: dict[str, Any], reports_dir: Path) -> dict[str, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)

    run_summary_path = reports_dir / "airflow_run_summary.json"
    run_summary_path.write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")

    task_summary_path = reports_dir / "airflow_task_summary.csv"
    import csv as csv_module
    with task_summary_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv_module.DictWriter(fh, fieldnames=["task_id", "state", "duration", "try_number"])
        writer.writeheader()
        for ti in summary["tasks"]:
            writer.writerow({k: ti.get(k) for k in ("task_id", "state", "duration", "try_number")})

    written = {"run_summary": run_summary_path, "task_summary": task_summary_path}

    if summary["failed_tasks"]:
        failure_summary_path = reports_dir / "airflow_failure_summary.json"
        failure_payload = {
            "dag_id": summary["dag_id"],
            "run_id": summary["run_id"],
            "failed_tasks": summary["failed_tasks"],
            "final_status": summary["final_status"],
            "generated_at_utc": summary["completed_at_utc"],
        }
        failure_summary_path.write_text(json.dumps(failure_payload, indent=2) + "\n", encoding="utf-8")
        written["failure_summary"] = failure_summary_path

    return written


def raise_if_run_failed(summary: dict[str, Any]) -> None:
    """Fail the calling task when the run it just summarized had any failed task.

    The summary/reconciliation task uses trigger_rule=ALL_DONE so it
    always runs and always writes a report -- but a DAG run's overall
    state in Airflow is computed from its *leaf* tasks' own states, not
    a simple "any task failed" check. Without this, a leaf task that
    uses ALL_DONE and then succeeds would make the whole DAG run report
    as "success" even though a data-dependent task upstream genuinely
    failed. Calling this after write_run_summary() closes that gap: the
    summary task deliberately fails too whenever the run it is
    summarizing was not clean, so the DAG run's own state stays accurate.
    """
    if summary["failed_tasks"]:
        raise AirflowException(
            f"Run failed: {summary['failed_tasks']} did not succeed (see reports/airflow/airflow_failure_summary.json)"
        )
