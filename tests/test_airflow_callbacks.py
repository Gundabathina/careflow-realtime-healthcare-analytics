"""Tests for airflow/plugins/careflow_callbacks.py.

These callbacks are plain functions taking an Airflow-style context
dict; they are tested here with lightweight fakes rather than a real
running Airflow, a real TaskInstance (which is DB-backed), or a real
scheduler. careflow_callbacks.py itself only depends on the standard
library plus careflow_operators.sanitize_text (which itself degrades
gracefully without Airflow installed), so this file also runs under the
project's main Python -- but the required command targets
.venv-airflow for consistency with the other Airflow test files.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_DIR = PROJECT_ROOT / "airflow" / "plugins"
sys.path.insert(0, str(PLUGINS_DIR))

import careflow_callbacks as cb  # noqa: E402


class FakeLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def info(self, msg, *args):
        self.records.append(("info", msg % args if args else msg))

    def warning(self, msg, *args):
        self.records.append(("warning", msg % args if args else msg))

    def error(self, msg, *args):
        self.records.append(("error", msg % args if args else msg))


class FakeTaskInstance:
    def __init__(self, task_id: str, try_number: int = 1, log_url: str = "http://airflow.local/log/123"):
        self.task_id = task_id
        self.try_number = try_number
        self.log_url = log_url
        self.log = FakeLogger()


class FakeDagRun:
    def __init__(self, run_id: str):
        self.run_id = run_id


class FakeDag:
    def __init__(self, dag_id: str):
        self.dag_id = dag_id


def make_context(
    dag_id="careflow_end_to_end", task_id="build_gold", run_id="manual__2026-08-06T00:00:00",
    exception=None, try_number=2,
):
    ti = FakeTaskInstance(task_id=task_id, try_number=try_number)
    return {
        "dag": FakeDag(dag_id),
        "task_instance": ti,
        "dag_run": FakeDagRun(run_id),
        "logical_date": "2026-08-06T00:00:00+00:00",
        "exception": exception,
    }, ti


# -- each callback logs the required structured fields ----------------------


def test_task_failure_callback_logs_dag_task_run_and_exception_type():
    context, ti = make_context(exception=RuntimeError("build_gold exited with code 1"))
    cb.task_failure_callback(context)
    assert len(ti.log.records) == 1
    level, message = ti.log.records[0]
    assert level == "error"
    assert "careflow_end_to_end" in message
    assert "build_gold" in message
    assert "manual__2026-08-06T00:00:00" in message
    assert "RuntimeError" in message
    assert "try=2" in message
    assert "http://airflow.local/log/123" in message


def test_task_retry_callback_logs_retry_number():
    context, ti = make_context(try_number=1, exception=RuntimeError("transient failure"))
    cb.task_retry_callback(context)
    level, message = ti.log.records[0]
    assert level == "warning"
    assert "RETRYING" in message
    assert "try=1" in message


def test_task_success_callback_logs_success():
    context, ti = make_context(exception=None)
    cb.task_success_callback(context)
    level, message = ti.log.records[0]
    assert level == "info"
    assert "SUCCEEDED" in message


def test_dag_success_callback_logs_dag_level_fields():
    context, ti = make_context()
    cb.dag_success_callback(context)
    level, message = ti.log.records[0]
    assert level == "info"
    assert "careflow_end_to_end" in message
    assert "manual__2026-08-06T00:00:00" in message


def test_dag_failure_callback_logs_dag_level_failure():
    context, ti = make_context()
    cb.dag_failure_callback(context)
    level, message = ti.log.records[0]
    assert level == "error"
    assert "FAILED" in message


# -- secret redaction ---------------------------------------------------


def test_task_failure_callback_redacts_password_in_exception_message():
    exc = RuntimeError("connection refused: password=hunter2secret could not authenticate")
    context, ti = make_context(exception=exc)
    cb.task_failure_callback(context)
    _, message = ti.log.records[0]
    assert "hunter2secret" not in message
    assert "password=***" in message


def test_task_failure_callback_redacts_embedded_dsn():
    exc = RuntimeError("could not connect to postgresql://careflow_user:hunter2@localhost:5433/careflow")
    context, ti = make_context(exception=exc)
    cb.task_failure_callback(context)
    _, message = ti.log.records[0]
    assert "hunter2" not in message
    assert "://***:***@" in message


def test_task_failure_callback_redacts_the_real_configured_password(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "supersecretlocalpw")
    exc = RuntimeError("subprocess failed while using supersecretlocalpw as a credential")
    context, ti = make_context(exception=exc)
    cb.task_failure_callback(context)
    _, message = ti.log.records[0]
    assert "supersecretlocalpw" not in message


def test_callbacks_never_log_environment_variables():
    """No callback ever formats os.environ (or any dict resembling it)
    into its log message -- structural check on the module source."""
    import inspect

    source = inspect.getsource(cb)
    assert "os.environ" not in source
    assert "os.getenv" not in source


# -- graceful behavior outside a live task context --------------------------


def test_get_logger_falls_back_to_stdlib_logger_without_task_instance():
    logger = cb._get_logger({})
    assert isinstance(logger, logging.Logger)


def test_task_failure_callback_does_not_raise_without_an_exception_key():
    context, ti = make_context(exception=None)
    del context["exception"]
    cb.task_failure_callback(context)  # must not raise
    assert ti.log.records
