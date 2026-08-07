"""Tests for the Phase 3C dbt project's structure, conventions, and the
scripts/run_dbt.py wrapper.

None of these tests require a running PostgreSQL server. The one dbt
subprocess test (test_dbt_parses_cleanly_via_isolated_venv) shells out to
`dbt parse`, which only compiles Jinja/YAML and resolves ref()/source()
against the manifest -- it never opens a database connection -- and is
skipped outright if the isolated .venv-dbt environment hasn't been created.
"""

from __future__ import annotations

import ast
import csv
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DBT_ROOT = PROJECT_ROOT / "dbt"
DBT_VENV_PYTHON = PROJECT_ROOT / ".venv-dbt" / "bin" / "python"
DBT_EXECUTABLE = PROJECT_ROOT / ".venv-dbt" / "bin" / "dbt"


def _load_run_dbt_module():
    spec = importlib.util.spec_from_file_location("run_dbt", PROJECT_ROOT / "scripts" / "run_dbt.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def run_dbt():
    return _load_run_dbt_module()


# -- project structure -----------------------------------------------------


REQUIRED_ROOT_FILES = ["dbt_project.yml", "profiles.yml.example", "packages.yml"]

REQUIRED_STAGING_MODELS = [
    "stg_careflow__patients", "stg_careflow__providers", "stg_careflow__organizations",
    "stg_careflow__payers", "stg_careflow__dates", "stg_careflow__encounters",
    "stg_careflow__conditions", "stg_careflow__procedures", "stg_careflow__medications",
    "stg_careflow__observations", "stg_careflow__claims", "stg_careflow__immunizations",
    "stg_careflow__imaging_studies", "stg_careflow__readmissions",
]

REQUIRED_INTERMEDIATE_MODELS = [
    "int_encounters_enriched", "int_patient_encounter_history", "int_patient_clinical_activity",
    "int_claim_financials", "int_provider_activity", "int_organization_activity",
    "int_readmission_events", "int_monthly_healthcare_metrics",
]

REQUIRED_MART_MODELS = [
    "fct_encounters", "fct_readmissions", "fct_claim_financials", "fct_provider_activity",
    "dim_patient_safe", "dim_provider_reporting", "dim_organization_reporting", "dim_payer_reporting",
    "mart_executive_monthly", "mart_readmission_analysis", "mart_financial_analysis",
    "mart_hospital_operations", "mart_provider_performance", "mart_patient_population",
]

# The 12 required singular tests, by requirement description -> the actual
# file already implementing it (names predate a later restatement of this
# requirement that suggested slightly different filenames; the underlying
# checks are equivalent and are not renamed just to match wording).
REQUIRED_SINGULAR_TESTS = [
    "no_negative_patient_responsibility.sql",
    "no_encounter_stop_before_start.sql",
    "no_self_readmission.sql",
    "no_negative_readmission_intervals.sql",
    "readmission_7_day_implies_14_and_30_day.sql",
    "readmission_14_day_implies_30_day.sql",
    "reconcile_monthly_encounter_totals.sql",
    "reconcile_financial_totals_with_tolerance.sql",
    "reconcile_readmission_counts_with_python_gold.sql",
    "no_restricted_pii_in_public_models.sql",
    "imaging_study_composite_grain_is_unique.sql",
    "no_unexpected_orphan_dimension_keys.sql",
]

RECONCILIATION_SINGULAR_TESTS = [
    "reconcile_monthly_encounter_totals.sql",
    "reconcile_financial_totals_with_tolerance.sql",
    "reconcile_readmission_counts_with_python_gold.sql",
]

PUBLIC_MART_MODELS_SCHEMA_YML_COVERAGE = [
    "dim_patient_safe", "mart_patient_population", "mart_readmission_analysis",
    "mart_executive_monthly", "fct_encounters", "fct_readmissions", "fct_claim_financials",
    "fct_provider_activity", "dim_provider_reporting", "dim_organization_reporting",
    "dim_payer_reporting", "mart_financial_analysis", "mart_hospital_operations",
    "mart_provider_performance",
]

RESTRICTED_PII_TOKENS = ["ssn", "passport", "drivers_license", "driver_license", "first_name", "middle_name", "last_name", "street_address"]


@pytest.mark.parametrize("filename", REQUIRED_ROOT_FILES)
def test_required_root_dbt_file_exists(filename):
    assert (PROJECT_ROOT / filename).is_file(), f"missing {filename}"


@pytest.mark.parametrize("model", REQUIRED_STAGING_MODELS)
def test_required_staging_model_exists(model):
    assert (DBT_ROOT / "models" / "staging" / f"{model}.sql").is_file()


@pytest.mark.parametrize("model", REQUIRED_INTERMEDIATE_MODELS)
def test_required_intermediate_model_exists(model):
    assert (DBT_ROOT / "models" / "intermediate" / f"{model}.sql").is_file()


@pytest.mark.parametrize("model", REQUIRED_MART_MODELS)
def test_required_mart_model_exists(model):
    assert (DBT_ROOT / "models" / "marts" / f"{model}.sql").is_file()


def test_no_extra_or_missing_staging_models():
    on_disk = {p.stem for p in (DBT_ROOT / "models" / "staging").glob("*.sql")}
    assert on_disk == set(REQUIRED_STAGING_MODELS)


def test_no_extra_or_missing_intermediate_models():
    on_disk = {p.stem for p in (DBT_ROOT / "models" / "intermediate").glob("*.sql")}
    assert on_disk == set(REQUIRED_INTERMEDIATE_MODELS)


def test_no_extra_or_missing_mart_models():
    on_disk = {p.stem for p in (DBT_ROOT / "models" / "marts").glob("*.sql")}
    assert on_disk == set(REQUIRED_MART_MODELS)


def test_required_scaffolding_directories_exist():
    for sub in ("models/staging", "models/intermediate", "models/marts", "macros", "seeds", "snapshots", "tests"):
        assert (DBT_ROOT / sub).is_dir(), f"missing dbt/{sub}"


def test_scripts_and_docs_and_tests_exist():
    assert (PROJECT_ROOT / "scripts" / "run_dbt.py").is_file()
    assert (PROJECT_ROOT / "docs" / "dbt_analytics_guide.md").is_file()
    assert (PROJECT_ROOT / "tests" / "test_dbt_project.py").is_file()


# -- YAML validity -----------------------------------------------------------


def _all_project_yaml_files() -> list[Path]:
    files = [PROJECT_ROOT / "dbt_project.yml", PROJECT_ROOT / "packages.yml", PROJECT_ROOT / "profiles.yml.example"]
    files += sorted(DBT_ROOT.rglob("*.yml")) + sorted(DBT_ROOT.rglob("*.yaml"))
    return files


@pytest.mark.parametrize("path", _all_project_yaml_files(), ids=lambda p: str(p.relative_to(PROJECT_ROOT)))
def test_yaml_file_is_valid(path):
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert data is not None


def test_dbt_project_yml_has_required_top_level_keys():
    data = yaml.safe_load((PROJECT_ROOT / "dbt_project.yml").read_text(encoding="utf-8"))
    for key in ("name", "profile", "model-paths", "macro-paths", "seed-paths", "snapshot-paths", "test-paths", "vars"):
        assert key in data, f"dbt_project.yml missing '{key}'"
    assert data["vars"].get("count_reconciliation_tolerance") is not None
    assert data["vars"].get("currency_reconciliation_tolerance") is not None


def test_packages_yml_pins_dbt_utils_version():
    data = yaml.safe_load((PROJECT_ROOT / "packages.yml").read_text(encoding="utf-8"))
    packages = data.get("packages", [])
    dbt_utils = next((p for p in packages if p.get("package") == "dbt-labs/dbt_utils"), None)
    assert dbt_utils is not None, "dbt_utils not declared in packages.yml"
    assert dbt_utils.get("version"), "dbt_utils version must be pinned, not left unbounded"


# -- profile / credential hygiene --------------------------------------------


def test_profiles_example_uses_env_var_for_every_credential():
    text = (PROJECT_ROOT / "profiles.yml.example").read_text(encoding="utf-8")
    for var in ("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "DBT_TARGET"):
        assert f"env_var('{var}'" in text or f'env_var("{var}"' in text, f"profiles.yml.example does not read {var} via env_var()"


def test_profiles_example_never_hardcodes_the_local_port():
    """5433 may appear in an explanatory comment (why not to hard-code it),
    but never as an actual YAML value for the port key."""
    data = yaml.safe_load((PROJECT_ROOT / "profiles.yml.example").read_text(encoding="utf-8"))
    text_without_comments = repr(data)
    assert "5433" not in text_without_comments


def test_profiles_example_contains_no_literal_password():
    data = yaml.safe_load((PROJECT_ROOT / "profiles.yml.example").read_text(encoding="utf-8"))
    text = repr(data)
    assert "careflow_dev_local_test_only" not in text  # the real .env password, must never leak into a committed file


def test_real_profiles_yml_and_dbt_artifacts_are_gitignored():
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("profiles.yml", "target/", "dbt_packages/", ".venv-dbt/"):
        assert pattern in gitignore, f".gitignore missing '{pattern}'"


def test_no_hardcoded_port_5433_in_tracked_dbt_config():
    for path in (PROJECT_ROOT / "dbt_project.yml", DBT_ROOT / "models" / "sources.yml"):
        assert "5433" not in path.read_text(encoding="utf-8"), f"{path} hard-codes the local port"


# -- staging conventions: explicit columns, source() only, no ref() --------


@pytest.mark.parametrize("model", REQUIRED_STAGING_MODELS)
def test_staging_model_never_uses_select_star(model):
    text = (DBT_ROOT / "models" / "staging" / f"{model}.sql").read_text(encoding="utf-8")
    assert not re.search(r"select\s*\*", text, re.IGNORECASE)


@pytest.mark.parametrize("model", REQUIRED_STAGING_MODELS)
def test_staging_model_uses_source_not_ref(model):
    text = (DBT_ROOT / "models" / "staging" / f"{model}.sql").read_text(encoding="utf-8")
    assert "source(" in text, f"{model} must select from source(), never ref()"
    assert "ref(" not in text, f"{model} is a staging model and must not use ref()"


@pytest.mark.parametrize("model", REQUIRED_INTERMEDIATE_MODELS + REQUIRED_MART_MODELS)
def test_intermediate_and_mart_models_never_call_source_directly(model):
    layer = "intermediate" if model in REQUIRED_INTERMEDIATE_MODELS else "marts"
    text = (DBT_ROOT / "models" / layer / f"{model}.sql").read_text(encoding="utf-8")
    assert "source(" not in text, f"{model} must reference staging via ref(), not source() directly"
    assert "ref(" in text, f"{model} should build on other models via ref()"


# -- PII exclusion in public models ------------------------------------------


def _strip_sql_line_comments(text: str) -> str:
    """Drop ``-- ...`` comment lines before scanning SQL for forbidden
    tokens -- a comment explaining what's deliberately excluded (e.g.
    "no ssn/passport") would otherwise trip a naive substring search."""
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("--"))


@pytest.mark.parametrize("model", REQUIRED_MART_MODELS)
def test_mart_model_sql_excludes_restricted_pii_tokens(model):
    text = _strip_sql_line_comments((DBT_ROOT / "models" / "marts" / f"{model}.sql").read_text(encoding="utf-8")).lower()
    for token in RESTRICTED_PII_TOKENS:
        assert token not in text, f"{model}.sql references restricted PII token '{token}'"


def test_dim_patient_safe_selects_only_the_documented_safe_columns():
    text = _strip_sql_line_comments((DBT_ROOT / "models" / "marts" / "dim_patient_safe.sql").read_text(encoding="utf-8"))
    for forbidden in ("patient_id", "latitude", "longitude", "ssn", "passport"):
        assert forbidden not in text.lower()


def test_pii_guard_macro_and_singular_pii_test_exist():
    macro_text = (DBT_ROOT / "macros" / "pii_guard.sql").read_text(encoding="utf-8")
    assert "restricted_pii_columns" in macro_text
    assert "assert_no_restricted_pii_columns" in macro_text
    singular_test = (DBT_ROOT / "tests" / "no_restricted_pii_in_public_models.sql").read_text(encoding="utf-8")
    assert "assert_no_restricted_pii_columns" in singular_test
    for model in PUBLIC_MART_MODELS_SCHEMA_YML_COVERAGE:
        assert model in singular_test, f"no_restricted_pii_in_public_models.sql does not cover mart '{model}'"


# -- singular tests: all 12 present, imaging grain rule respected ----------


@pytest.mark.parametrize("filename", REQUIRED_SINGULAR_TESTS)
def test_required_singular_test_exists_and_is_nonempty(filename):
    path = DBT_ROOT / "tests" / filename
    assert path.is_file(), f"missing required singular test: {filename}"
    text = path.read_text(encoding="utf-8")
    # Some singular tests delegate entirely to a Jinja test-helper macro
    # (assert_non_negative / assert_implies / assert_no_restricted_pii_columns)
    # whose own SQL lives in dbt/macros/ -- either a literal SELECT or a
    # macro call counts as "looks like a real singular test".
    assert "select" in text.lower() or "{{" in text, f"{filename} does not look like a singular test"


def test_exactly_twelve_singular_tests_are_registered():
    on_disk = {p.name for p in (DBT_ROOT / "tests").glob("*.sql")}
    assert on_disk == set(REQUIRED_SINGULAR_TESTS)


@pytest.mark.parametrize("filename", RECONCILIATION_SINGULAR_TESTS)
def test_reconciliation_singular_test_exists(filename):
    assert (DBT_ROOT / "tests" / filename).is_file()


def test_imaging_composite_grain_test_uses_the_composite_key_not_study_id_alone():
    text = (DBT_ROOT / "tests" / "imaging_study_composite_grain_is_unique.sql").read_text(encoding="utf-8")
    assert "series_uid" in text and "instance_uid" in text and "study_id" in text
    # group by / partition by all three -- never study_id alone
    assert re.search(r"group by\s+study_id,\s*series_uid,\s*instance_uid", text, re.IGNORECASE)


def test_imaging_studies_source_id_never_tested_as_unique_alone():
    schema_text = (DBT_ROOT / "models" / "staging" / "stg_careflow__schema.yml").read_text(encoding="utf-8")
    data = yaml.safe_load(schema_text)
    imaging_model = next(m for m in data["models"] if m["name"] == "stg_careflow__imaging_studies")
    study_id_column = next((c for c in imaging_model.get("columns", []) if c["name"] == "study_id"), None)
    assert study_id_column is not None
    assert not study_id_column.get("data_tests"), "study_id must never carry its own unique/not_null test"
    assert not study_id_column.get("tests"), "study_id must never carry its own unique/not_null test"


def test_reconcile_readmission_test_compares_against_python_gold_baseline():
    text = (DBT_ROOT / "tests" / "reconcile_readmission_counts_with_python_gold.sql").read_text(encoding="utf-8")
    assert "stg_careflow__readmissions" in text  # sourced from the Python Gold mart_readmission table
    assert "int_readmission_events" in text  # dbt's independently-recomputed readmissions
    assert "count_reconciliation_tolerance" in text


def test_reconcile_financial_test_uses_currency_tolerance_var():
    text = (DBT_ROOT / "tests" / "reconcile_financial_totals_with_tolerance.sql").read_text(encoding="utf-8")
    assert "currency_reconciliation_tolerance" in text


# -- reports/ generation code: parsing, subprocess safety, sanitization ----


def test_run_dbt_never_uses_shell_true():
    """AST-based, not substring search: the module's own docstring
    mentions "never shell=True" as documentation, which a naive text
    search would misfire on."""
    tree = ast.parse((PROJECT_ROOT / "scripts" / "run_dbt.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg == "shell":
                    assert not (isinstance(keyword.value, ast.Constant) and keyword.value.value is True), (
                        "found shell=True in a subprocess-style call"
                    )


def test_run_dbt_subprocess_call_uses_an_argument_list():
    tree = ast.parse((PROJECT_ROOT / "scripts" / "run_dbt.py").read_text(encoding="utf-8"))
    subprocess_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
    ]
    assert subprocess_calls, "expected at least one subprocess.run(...) call"
    for call in subprocess_calls:
        assert call.args, "subprocess.run must be called with a positional command argument"
        first_arg = call.args[0]
        assert isinstance(first_arg, (ast.List, ast.Name, ast.Call)), (
            "subprocess.run's command must be built as an argument list, not an inline shell string"
        )
        if isinstance(first_arg, ast.Constant):
            pytest.fail("subprocess.run must not be called with a single string command")


def test_run_dbt_command_builder_returns_argument_list_not_string(run_dbt):
    cmd = run_dbt.build_dbt_command(["run"])
    assert isinstance(cmd, list)
    assert all(isinstance(part, str) for part in cmd)
    assert "--project-dir" in cmd
    assert "--profiles-dir" in cmd


@pytest.mark.parametrize("subcommand", ["debug", "deps", "seed", "snapshot", "run", "test", "build", "docs-generate", "full-refresh"])
def test_run_dbt_supports_all_required_subcommands(run_dbt, subcommand):
    assert subcommand in run_dbt.SUBCOMMANDS


def test_run_dbt_command_never_embeds_credentials_as_arguments(run_dbt):
    for subcommand, args in run_dbt.SUBCOMMANDS.items():
        cmd = run_dbt.build_dbt_command(args)
        for part in cmd:
            assert "PASSWORD" not in part.upper(), f"{subcommand} command embeds a credential-like argument: {part}"


def test_sanitize_text_masks_password_assignment(run_dbt):
    sanitized = run_dbt.sanitize_text("connection failed: password=hunter2 could not authenticate")
    assert "hunter2" not in sanitized
    assert "password=***" in sanitized


def test_sanitize_text_masks_embedded_dsn_credentials(run_dbt):
    sanitized = run_dbt.sanitize_text("could not connect to postgresql://careflow_user:hunter2@localhost:5433/careflow")
    assert "hunter2" not in sanitized
    assert "://***:***@" in sanitized


def test_run_dbt_logs_a_sanitized_command_before_running(monkeypatch, run_dbt, tmp_path):
    """The executed command is logged (without credentials) even though
    credentials never appear in the command line to begin with -- they
    reach dbt only via the subprocess's inherited environment."""
    calls = []

    class FakeCompletedProcess:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, cwd=None, capture_output=None, text=None):
        calls.append(cmd)
        return FakeCompletedProcess()

    monkeypatch.setattr(run_dbt.subprocess, "run", fake_run)
    run_dbt.run_dbt_command(["debug"])
    assert len(calls) == 1
    assert all("PASSWORD" not in part.upper() for part in calls[0])


# -- report / dbt-artifact parsing (pure functions, fed fake data) ---------


FAKE_MANIFEST = {
    "nodes": {
        "model.careflow_analytics.stg_careflow__patients": {
            "resource_type": "model", "name": "stg_careflow__patients", "schema": "careflow_dbt_staging",
            "config": {"materialized": "view"}, "tags": ["staging"], "group": "staging", "alias": "stg_careflow__patients",
            "fqn": ["careflow_analytics", "staging", "stg_careflow__patients"], "description": "x", "meta": {},
        },
        "model.careflow_analytics.mart_patient_population": {
            "resource_type": "model", "name": "mart_patient_population", "schema": "careflow_dbt_mart",
            "config": {"materialized": "table"}, "tags": ["marts"], "group": "marts", "alias": "mart_patient_population",
            "fqn": ["careflow_analytics", "marts", "mart_patient_population"], "description": "x",
            "meta": {"contains_pii": False},
        },
        "test.careflow_analytics.reconcile_monthly_encounter_totals": {
            "resource_type": "test", "name": "reconcile_monthly_encounter_totals",
        },
        "test.careflow_analytics.no_self_readmission": {
            "resource_type": "test", "name": "no_self_readmission",
        },
    }
}

FAKE_RUN_RESULTS = {
    "metadata": {"invocation_id": "abc-123", "dbt_version": "1.8.9"},
    "elapsed_time": 2.5,
    "args": {},
    "results": [
        {"unique_id": "model.careflow_analytics.stg_careflow__patients", "status": "success", "execution_time": 0.1, "adapter_response": {}, "failures": None},
        {"unique_id": "model.careflow_analytics.mart_patient_population", "status": "success", "execution_time": 0.2, "adapter_response": {}, "failures": None},
        {"unique_id": "test.careflow_analytics.reconcile_monthly_encounter_totals", "status": "success", "execution_time": 0.05, "adapter_response": {}, "failures": None},
        {"unique_id": "test.careflow_analytics.no_self_readmission", "status": "fail", "execution_time": 0.03, "adapter_response": {}, "failures": 2},
    ],
}


def test_build_run_summary_counts_only_models_seeds_snapshots(run_dbt):
    summary = run_dbt.build_run_summary(FAKE_MANIFEST, FAKE_RUN_RESULTS, row_counts={})
    assert summary["models_selected"] == 2  # the two test results must be excluded
    assert summary["status_counts"] == {"success": 2}
    assert summary["dbt_version"] == "1.8.9"
    assert summary["invocation_id"] == "abc-123"


def test_build_run_summary_attaches_live_row_counts_when_available(run_dbt):
    row_counts = {"model.careflow_analytics.stg_careflow__patients": 58}
    summary = run_dbt.build_run_summary(FAKE_MANIFEST, FAKE_RUN_RESULTS, row_counts=row_counts)
    by_id = {m["unique_id"]: m for m in summary["models"]}
    assert by_id["model.careflow_analytics.stg_careflow__patients"]["rows_affected"] == 58
    assert by_id["model.careflow_analytics.mart_patient_population"]["rows_affected"] is None


def test_build_test_summary_counts_pass_and_fail_correctly(run_dbt):
    summary = run_dbt.build_test_summary(FAKE_MANIFEST, FAKE_RUN_RESULTS)
    assert summary["total_tests"] == 2
    assert summary["pass"] == 1
    assert summary["fail"] == 1
    assert summary["warn"] == 0
    assert summary["skipped"] == 0


def test_build_reconciliation_report_flags_all_three_required_checks_when_present(run_dbt):
    manifest = {"nodes": dict(FAKE_MANIFEST["nodes"])}
    for name in ("reconcile_financial_totals_with_tolerance", "reconcile_readmission_counts_with_python_gold"):
        manifest["nodes"][f"test.careflow_analytics.{name}"] = {"resource_type": "test", "name": name}
    run_results = {
        "results": FAKE_RUN_RESULTS["results"] + [
            {"unique_id": f"test.careflow_analytics.{name}", "status": "success", "execution_time": 0.01, "failures": None}
            for name in ("reconcile_financial_totals_with_tolerance", "reconcile_readmission_counts_with_python_gold")
        ],
    }
    report = run_dbt.build_reconciliation_report(manifest, run_results)
    assert report["checks_found"] == 3
    assert report["checks_expected"] == 3
    assert report["all_reconciled"] is True


def test_build_reconciliation_report_flags_missing_checks_as_not_fully_reconciled(run_dbt):
    report = run_dbt.build_reconciliation_report(FAKE_MANIFEST, FAKE_RUN_RESULTS)
    assert report["checks_found"] == 1  # only reconcile_monthly_encounter_totals is present here
    assert report["all_reconciled"] is False


def test_write_model_inventory_csv_covers_models_and_derives_layer(run_dbt, tmp_path):
    output_path = tmp_path / "dbt_model_inventory.csv"
    row_count = run_dbt.write_model_inventory_csv(FAKE_MANIFEST, output_path)
    assert row_count == 2
    with output_path.open(newline="", encoding="utf-8") as fh:
        rows = {row["name"]: row for row in csv.DictReader(fh)}
    assert rows["stg_careflow__patients"]["layer"] == "staging"
    assert rows["mart_patient_population"]["layer"] == "marts"
    assert rows["mart_patient_population"]["contains_pii"] == "False"


def test_generate_reports_is_a_noop_when_dbt_artifacts_are_absent(run_dbt, tmp_path, monkeypatch):
    monkeypatch.setattr(run_dbt, "TARGET_DIR", tmp_path / "no_such_target")
    result = run_dbt.generate_reports(tmp_path / "reports_out")
    assert result == {}
    assert not (tmp_path / "reports_out").exists()


def test_generate_reports_writes_all_four_artifacts(run_dbt, tmp_path, monkeypatch):
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    (target_dir / "manifest.json").write_text(__import__("json").dumps(FAKE_MANIFEST), encoding="utf-8")
    (target_dir / "run_results.json").write_text(__import__("json").dumps(FAKE_RUN_RESULTS), encoding="utf-8")
    monkeypatch.setattr(run_dbt, "TARGET_DIR", target_dir)
    monkeypatch.setattr(run_dbt, "_fetch_live_row_counts", lambda manifest, run_results: {})

    reports_dir = tmp_path / "reports_out"
    result = run_dbt.generate_reports(reports_dir)

    assert result
    for filename in ("dbt_run_summary.json", "dbt_test_summary.json", "dbt_reconciliation_report.json", "dbt_model_inventory.csv"):
        assert (reports_dir / filename).is_file(), f"missing {filename}"


def test_generate_reports_never_touches_upstream_gold_files(run_dbt, tmp_path, monkeypatch):
    gold_dir = PROJECT_ROOT / "data" / "gold"
    if not gold_dir.is_dir():
        pytest.skip("data/gold not present in this environment")
    sample = next(gold_dir.glob("*.parquet"), None)
    if sample is None:
        pytest.skip("no Gold Parquet files present in this environment")
    before = sample.read_bytes()

    target_dir = tmp_path / "target"
    target_dir.mkdir()
    (target_dir / "manifest.json").write_text(__import__("json").dumps(FAKE_MANIFEST), encoding="utf-8")
    (target_dir / "run_results.json").write_text(__import__("json").dumps(FAKE_RUN_RESULTS), encoding="utf-8")
    monkeypatch.setattr(run_dbt, "TARGET_DIR", target_dir)
    monkeypatch.setattr(run_dbt, "_fetch_live_row_counts", lambda manifest, run_results: {})

    run_dbt.generate_reports(tmp_path / "reports_out")

    assert sample.read_bytes() == before


# -- structural guarantee that dbt never touches upstream Parquet layers ---


def test_no_dbt_model_or_script_references_upstream_parquet_paths():
    forbidden_fragments = ["data/raw", "data/bronze", "data/silver", "data/gold"]
    sql_files = list(DBT_ROOT.rglob("*.sql"))
    script_text = (PROJECT_ROOT / "scripts" / "run_dbt.py").read_text(encoding="utf-8")
    for path in sql_files:
        text = path.read_text(encoding="utf-8")
        for fragment in forbidden_fragments:
            assert fragment not in text, f"{path} references upstream Parquet path '{fragment}'"
    for fragment in forbidden_fragments:
        assert fragment not in script_text, f"scripts/run_dbt.py references upstream Parquet path '{fragment}'"


# -- optional live subprocess check: dbt parse never needs a DB connection -


@pytest.mark.skipif(not DBT_EXECUTABLE.is_file(), reason=".venv-dbt is not set up in this environment")
def test_dbt_parses_cleanly_via_isolated_venv(tmp_path):
    """dbt parse compiles every model/source/test reference without ever
    opening a database connection -- run with deliberately unreachable
    credentials to prove refs/sources resolve independent of PostgreSQL."""
    env = {
        "PATH": "/usr/bin:/bin",
        "POSTGRES_HOST": "unreachable-host.invalid",
        "POSTGRES_PORT": "1",
        "POSTGRES_DB": "unreachable",
        "POSTGRES_USER": "unreachable",
        "POSTGRES_PASSWORD": "unreachable",
        "DBT_TARGET": "dev",
    }
    result = subprocess.run(
        [str(DBT_EXECUTABLE), "parse", "--project-dir", str(PROJECT_ROOT), "--profiles-dir", str(PROJECT_ROOT),
         "--target-path", str(tmp_path)],
        cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"dbt parse failed:\n{result.stdout}\n{result.stderr}"
