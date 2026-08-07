"""Reusable Airflow callbacks for the CareFlow DAGs.

Each callback logs only non-sensitive, structured context (DAG id, task
id, run id, execution timestamp, exception type, retry number, log URL)
-- never secrets, never a raw environment dump, never the full exception
message unredacted (it is passed through sanitize_text() first, in case
a subprocess error happened to echo a connection string).
"""

from __future__ import annotations

from typing import Any

try:
    from careflow_operators import sanitize_text  # Airflow adds plugins/ to sys.path directly
except ImportError:  # pragma: no cover - fallback for alternate import mechanisms
    from .careflow_operators import sanitize_text  # type: ignore


def _base_context(context: dict[str, Any]) -> dict[str, Any]:
    task_instance = context.get("task_instance") or context.get("ti")
    dag_run = context.get("dag_run")
    exception = context.get("exception")

    info: dict[str, Any] = {
        "dag_id": context.get("dag", {}).dag_id if hasattr(context.get("dag"), "dag_id") else context.get("dag_id"),
        "task_id": getattr(task_instance, "task_id", None),
        "run_id": getattr(dag_run, "run_id", None) or context.get("run_id"),
        "execution_timestamp": str(context.get("logical_date") or context.get("execution_date") or ""),
        "try_number": getattr(task_instance, "try_number", None),
        "exception_type": type(exception).__name__ if exception is not None else None,
        "log_url": getattr(task_instance, "log_url", None),
    }
    return info


def task_failure_callback(context: dict[str, Any]) -> None:
    info = _base_context(context)
    exception = context.get("exception")
    exception_summary = sanitize_text(str(exception)) if exception is not None else None
    logger = _get_logger(context)
    logger.error(
        "CareFlow task FAILED dag_id=%s task_id=%s run_id=%s try=%s exception_type=%s exception=%s log_url=%s",
        info["dag_id"], info["task_id"], info["run_id"], info["try_number"],
        info["exception_type"], exception_summary, info["log_url"],
    )


def task_retry_callback(context: dict[str, Any]) -> None:
    info = _base_context(context)
    logger = _get_logger(context)
    logger.warning(
        "CareFlow task RETRYING dag_id=%s task_id=%s run_id=%s try=%s exception_type=%s log_url=%s",
        info["dag_id"], info["task_id"], info["run_id"], info["try_number"], info["exception_type"], info["log_url"],
    )


def task_success_callback(context: dict[str, Any]) -> None:
    info = _base_context(context)
    logger = _get_logger(context)
    logger.info(
        "CareFlow task SUCCEEDED dag_id=%s task_id=%s run_id=%s try=%s",
        info["dag_id"], info["task_id"], info["run_id"], info["try_number"],
    )


def dag_success_callback(context: dict[str, Any]) -> None:
    info = _base_context(context)
    logger = _get_logger(context)
    logger.info(
        "CareFlow DAG SUCCEEDED dag_id=%s run_id=%s execution_timestamp=%s",
        info["dag_id"], info["run_id"], info["execution_timestamp"],
    )


def dag_failure_callback(context: dict[str, Any]) -> None:
    info = _base_context(context)
    logger = _get_logger(context)
    logger.error(
        "CareFlow DAG FAILED dag_id=%s run_id=%s execution_timestamp=%s",
        info["dag_id"], info["run_id"], info["execution_timestamp"],
    )


def _get_logger(context: dict[str, Any]):
    """Prefer the task instance's own logger (shows up in the Airflow UI's
    task log); fall back to a plain stdlib logger outside a live task
    context (e.g. under test)."""
    task_instance = context.get("task_instance") or context.get("ti")
    if task_instance is not None and hasattr(task_instance, "log"):
        return task_instance.log
    import logging

    return logging.getLogger("careflow.airflow")
