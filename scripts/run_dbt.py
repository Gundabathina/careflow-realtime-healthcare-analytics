#!/usr/bin/env python3
"""Run dbt for the CareFlow Analytics dbt project via the isolated dbt toolchain.

dbt is never installed into the project's global Python 3.14 environment
(dbt-core has no supported build for it) -- this script always shells out
to the dedicated ``.venv-dbt`` virtualenv's ``dbt`` executable, using an
argument list (never ``shell=True``) and passing ``--project-dir``/
``--profiles-dir`` explicitly so it never depends on ``~/.dbt/``.

Credentials (POSTGRES_HOST/PORT/DB/USER/PASSWORD) are read from the
process environment by dbt's own ``env_var()`` calls in profiles.yml --
this script never places them on the command line, and sanitizes them out
of anything it logs or writes to a report.

Usage:
    set -a && source .env && set +a
    PYTHONPATH=src python3 scripts/run_dbt.py debug
    PYTHONPATH=src python3 scripts/run_dbt.py deps
    PYTHONPATH=src python3 scripts/run_dbt.py seed
    PYTHONPATH=src python3 scripts/run_dbt.py snapshot
    PYTHONPATH=src python3 scripts/run_dbt.py run
    PYTHONPATH=src python3 scripts/run_dbt.py test
    PYTHONPATH=src python3 scripts/run_dbt.py build
    PYTHONPATH=src python3 scripts/run_dbt.py docs-generate
    PYTHONPATH=src python3 scripts/run_dbt.py full-refresh
"""

from __future__ import annotations

import argparse
import csv as csv_module
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import yaml  # noqa: E402

from careflow.config import load_config  # noqa: E402
from careflow.logging_config import get_logger  # noqa: E402
from careflow.warehouse.postgres_client import (  # noqa: E402
    MissingCredentialsError,
    get_connection,
    load_connection_config,
    validate_identifier,
)

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# Default, host-only location: the isolated Python 3.11 venv built for
# Phase 3C (dbt-core has no supported Python 3.14 build, so it is never
# installed into the project's main environment). CAREFLOW_DBT_BIN
# overrides this -- used inside the Phase 4A Airflow container, where
# dbt is installed into Airflow's own Python environment instead, since
# a venv built from host-compiled binaries cannot run inside a Linux
# container.
DBT_EXECUTABLE = PROJECT_ROOT / ".venv-dbt" / "bin" / "dbt"
TARGET_DIR = PROJECT_ROOT / "target"


def resolve_dbt_executable() -> str:
    override = os.environ.get("CAREFLOW_DBT_BIN")
    if override:
        return override
    if DBT_EXECUTABLE.is_file():
        return str(DBT_EXECUTABLE)
    import shutil

    return shutil.which("dbt") or str(DBT_EXECUTABLE)


def dbt_executable_available() -> bool:
    override = os.environ.get("CAREFLOW_DBT_BIN")
    if override:
        import shutil

        return Path(override).is_file() or shutil.which(override) is not None
    return DBT_EXECUTABLE.is_file()

# Every subcommand maps to the dbt CLI arguments run after the executable.
SUBCOMMANDS: dict[str, list[str]] = {
    "debug": ["debug"],
    "deps": ["deps"],
    "seed": ["seed"],
    "snapshot": ["snapshot"],
    "run": ["run"],
    "test": ["test"],
    "build": ["build"],
    "docs-generate": ["docs", "generate"],
    "full-refresh": ["build", "--full-refresh"],
}

# Names of the singular tests that reconcile dbt's independently-computed
# figures against the Python Gold layer (see dbt/tests/) -- used to build
# dbt_reconciliation_report.json.
RECONCILIATION_TEST_NAMES = {
    "reconcile_monthly_encounter_totals",
    "reconcile_financial_totals_with_tolerance",
    "reconcile_readmission_counts_with_python_gold",
}

_PASSWORD_PATTERN = re.compile(r"password[=:]\S+", re.IGNORECASE)
_DSN_PATTERN = re.compile(r"://[^:@/\s]+:[^@/\s]+@")


def sanitize_text(text: str) -> str:
    """Strip anything resembling a password or embedded DSN before it is logged or written."""
    text = _PASSWORD_PATTERN.sub("password=***", text)
    text = _DSN_PATTERN.sub("://***:***@", text)
    return text


def build_dbt_command(args: list[str]) -> list[str]:
    return [resolve_dbt_executable(), *args, "--project-dir", str(PROJECT_ROOT), "--profiles-dir", str(PROJECT_ROOT)]


def run_dbt_command(args: list[str]) -> subprocess.CompletedProcess:
    """Run one dbt invocation as an argument list (never shell=True) and log it, sanitized."""
    cmd = build_dbt_command(args)
    logger.info("Running: %s", sanitize_text(" ".join(cmd)))
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)
    for stream_name, stream in (("stdout", result.stdout), ("stderr", result.stderr)):
        sanitized = sanitize_text(stream)
        if sanitized.strip():
            logger.info("dbt %s:\n%s", stream_name, sanitized)
    return result


# ---------------------------------------------------------------------------
# Report generation (reads target/run_results.json + target/manifest.json,
# best-effort supplemented with live row counts from PostgreSQL)
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_reconciliation_tolerances() -> dict:
    project = yaml.safe_load((PROJECT_ROOT / "dbt_project.yml").read_text(encoding="utf-8")) or {}
    return project.get("vars", {})


def _fetch_live_row_counts(manifest: dict, run_results: dict) -> dict[str, int]:
    """Best-effort live COUNT(*) per successfully-loaded model/seed/snapshot.

    Never fatal: the reports are still written (without row counts) if
    PostgreSQL is unreachable when this runs.
    """
    nodes = manifest.get("nodes", {})
    targets = []
    for result in run_results.get("results", []):
        node = nodes.get(result["unique_id"])
        if not node or node.get("resource_type") not in ("model", "seed", "snapshot"):
            continue
        if result.get("status") not in ("success", "pass"):
            continue
        targets.append((result["unique_id"], node["schema"], node.get("alias") or node["name"]))

    counts: dict[str, int] = {}
    if not targets:
        return counts
    try:
        config = load_connection_config()
    except MissingCredentialsError:
        return counts
    try:
        with get_connection(config) as conn:
            with conn.cursor() as cur:
                for unique_id, schema_name, table_name in targets:
                    try:
                        validate_identifier(schema_name)
                        validate_identifier(table_name)
                    except Exception:  # noqa: BLE001 - skip anything that fails identifier validation
                        continue
                    cur.execute(f'SELECT COUNT(*) FROM "{schema_name}"."{table_name}"')
                    (count,) = cur.fetchone()
                    counts[unique_id] = count
    except Exception as exc:  # noqa: BLE001 - row counts are a best-effort enrichment, never fatal
        logger.warning("Could not fetch live row counts for reports: %s", sanitize_text(str(exc)))
    return counts


def build_run_summary(manifest: dict, run_results: dict, row_counts: dict[str, int]) -> dict:
    nodes = manifest.get("nodes", {})
    entries = []
    status_counts: dict[str, int] = {}
    for result in run_results.get("results", []):
        unique_id = result["unique_id"]
        node = nodes.get(unique_id)
        if not node or node.get("resource_type") not in ("model", "seed", "snapshot"):
            continue
        status = result.get("status", "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        entries.append({
            "unique_id": unique_id,
            "name": node.get("name"),
            "resource_type": node.get("resource_type"),
            "schema": node.get("schema"),
            "materialized": node.get("config", {}).get("materialized"),
            "status": status,
            "duration_seconds": result.get("execution_time"),
            "rows_affected": row_counts.get(unique_id),
        })
    metadata = run_results.get("metadata", {})
    return {
        "invocation_id": metadata.get("invocation_id"),
        "dbt_version": metadata.get("dbt_version"),
        # run_results.json's "args" doesn't echo the resolved target; the
        # profile resolves it from DBT_TARGET via env_var('DBT_TARGET', 'dev').
        "target": os.environ.get("DBT_TARGET", "dev"),
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "elapsed_time_seconds": run_results.get("elapsed_time"),
        "status_counts": status_counts,
        "models_selected": len(entries),
        "models": entries,
    }


# dbt's TestStatus enum is pass/warn/fail/error/skipped, but run_results.json
# (at least under dbt-core 1.8 + dbt-postgres) records a passing test's
# results[].status as the generic RunStatus "success", not "pass" -- both
# are treated as a pass here so the summary reflects what dbt actually wrote.
_TEST_PASS_STATUSES = ("pass", "success")
_TEST_WARN_STATUSES = ("warn",)
_TEST_FAIL_STATUSES = ("fail", "error", "runtime error")
_TEST_SKIP_STATUSES = ("skipped",)


def build_test_summary(manifest: dict, run_results: dict) -> dict:
    nodes = manifest.get("nodes", {})
    counts = {"pass": 0, "warn": 0, "fail": 0, "skipped": 0}
    tests = []
    for result in run_results.get("results", []):
        node = nodes.get(result["unique_id"])
        if not node or node.get("resource_type") != "test":
            continue
        status = result.get("status", "unknown")
        if status in _TEST_PASS_STATUSES:
            bucket = "pass"
        elif status in _TEST_WARN_STATUSES:
            bucket = "warn"
        elif status in _TEST_SKIP_STATUSES:
            bucket = "skipped"
        else:
            bucket = "fail"  # includes _TEST_FAIL_STATUSES and any unrecognized status, by design
        counts[bucket] = counts.get(bucket, 0) + 1
        tests.append({
            "unique_id": result["unique_id"],
            "name": node.get("name"),
            "status": status,
            "failing_rows": result.get("failures"),
            "duration_seconds": result.get("execution_time"),
        })
    return {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_tests": len(tests),
        "pass": counts["pass"],
        "warn": counts["warn"],
        "fail": counts["fail"],
        "skipped": counts["skipped"],
        "tests": tests,
    }


def build_reconciliation_report(manifest: dict, run_results: dict) -> dict:
    nodes = manifest.get("nodes", {})
    tolerances = _load_reconciliation_tolerances()
    checks = []
    for result in run_results.get("results", []):
        node = nodes.get(result["unique_id"])
        if not node or node.get("resource_type") != "test":
            continue
        if node.get("name") not in RECONCILIATION_TEST_NAMES:
            continue
        checks.append({
            "test_name": node.get("name"),
            "status": result.get("status"),
            "reconciled": result.get("status") in ("pass", "success"),
            "differing_rows": result.get("failures"),
            "duration_seconds": result.get("execution_time"),
        })
    return {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "compared_against": "Python Gold layer (careflow_mart schema, staged as stg_careflow__readmissions and read directly for financial/encounter totals)",
        "tolerances": {
            "currency_reconciliation_tolerance": tolerances.get("currency_reconciliation_tolerance"),
            "count_reconciliation_tolerance": tolerances.get("count_reconciliation_tolerance"),
        },
        "checks_found": len(checks),
        "checks_expected": len(RECONCILIATION_TEST_NAMES),
        "all_reconciled": len(checks) == len(RECONCILIATION_TEST_NAMES) and all(c["reconciled"] for c in checks),
        "checks": checks,
    }


def write_model_inventory_csv(manifest: dict, output_path: Path) -> int:
    nodes = manifest.get("nodes", {})
    rows = []
    for unique_id, node in nodes.items():
        if node.get("resource_type") not in ("model", "seed", "snapshot"):
            continue
        fqn = node.get("fqn", [])
        layer = fqn[1] if node.get("resource_type") == "model" and len(fqn) > 2 else node.get("resource_type")
        rows.append({
            "unique_id": unique_id,
            "name": node.get("name"),
            "resource_type": node.get("resource_type"),
            "layer": layer,
            "schema": node.get("schema"),
            "materialized": node.get("config", {}).get("materialized"),
            "group": node.get("group"),
            "tags": ";".join(node.get("tags", [])),
            "has_description": bool(node.get("description")),
            "contains_pii": bool((node.get("meta") or {}).get("contains_pii", False)),
        })
    rows.sort(key=lambda r: (r["resource_type"], r["layer"] or "", r["name"]))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["unique_id", "name", "resource_type", "layer", "schema", "materialized", "group", "tags", "has_description", "contains_pii"]
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv_module.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def generate_reports(reports_dir: Path) -> dict:
    """Build all four quality-report artifacts from the current target/ state.

    Silently does nothing (returns an empty summary) if target/run_results.json
    or target/manifest.json don't exist yet -- e.g. right after `deps`/`debug`
    with no prior model run.
    """
    manifest = _load_json(TARGET_DIR / "manifest.json")
    run_results = _load_json(TARGET_DIR / "run_results.json")
    if manifest is None or run_results is None:
        logger.info("Skipping report generation: target/manifest.json or run_results.json not found yet")
        return {}

    row_counts = _fetch_live_row_counts(manifest, run_results)
    run_summary = build_run_summary(manifest, run_results, row_counts)
    test_summary = build_test_summary(manifest, run_results)
    reconciliation_report = build_reconciliation_report(manifest, run_results)

    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "dbt_run_summary.json").write_text(json.dumps(run_summary, indent=2, default=str) + "\n", encoding="utf-8")
    (reports_dir / "dbt_test_summary.json").write_text(json.dumps(test_summary, indent=2, default=str) + "\n", encoding="utf-8")
    (reports_dir / "dbt_reconciliation_report.json").write_text(json.dumps(reconciliation_report, indent=2, default=str) + "\n", encoding="utf-8")
    inventory_count = write_model_inventory_csv(manifest, reports_dir / "dbt_model_inventory.csv")

    return {
        "run_summary": run_summary,
        "test_summary": test_summary,
        "reconciliation_report": reconciliation_report,
        "model_inventory_rows": inventory_count,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("subcommand", choices=sorted(SUBCOMMANDS.keys()))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not dbt_executable_available():
        logger.error(
            "dbt executable not found (resolved to %s) -- create the isolated dbt environment first "
            "(see docs/dbt_analytics_guide.md), or set CAREFLOW_DBT_BIN. Never install dbt into the "
            "project's global Python.",
            resolve_dbt_executable(),
        )
        return 1

    try:
        connection_config = load_connection_config()
    except MissingCredentialsError as exc:
        logger.error("PostgreSQL credentials not configured: %s", exc)
        return 1
    logger.info("Target warehouse: %s", connection_config.safe_repr())

    result = run_dbt_command(SUBCOMMANDS[args.subcommand])

    config = load_config()
    reports_dir = config.get_path("reports") / "dbt"
    report_summary = generate_reports(reports_dir)
    if report_summary:
        logger.info(
            "Reports written to %s (run_summary status_counts=%s, test_summary pass/warn/fail/skipped=%d/%d/%d/%d)",
            reports_dir,
            report_summary["run_summary"]["status_counts"],
            report_summary["test_summary"]["pass"],
            report_summary["test_summary"]["warn"],
            report_summary["test_summary"]["fail"],
            report_summary["test_summary"]["skipped"],
        )

    if result.returncode != 0:
        logger.error("dbt %s failed with exit code %d", args.subcommand, result.returncode)
    else:
        logger.info("dbt %s completed successfully", args.subcommand)

    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
