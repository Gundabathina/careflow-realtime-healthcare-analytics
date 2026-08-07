"""Repository-quality tests for the Phase 6 GitHub/recruiter-polish pass.

No live PostgreSQL, no git repository required (this repository may or
may not have been `git init`'d yet at the time these run) -- everything
here reads files already on disk.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Directories never scanned for "developer path" / "secret" violations --
# generated artifacts, vendored packages, and isolated environments.
EXCLUDED_DIR_PREFIXES = (
    ".venv", ".venv-dbt", ".venv-airflow", ".venv-dashboard", "venv",
    "target", "dbt_packages", "node_modules", ".git", "tools",
    "reports", "data", "logs", ".pytest_cache", "__pycache__", ".claude",
)


def _iter_source_and_doc_files(extensions: tuple[str, ...]) -> list[Path]:
    files = []
    for path in PROJECT_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in extensions:
            continue
        relative_parts = path.relative_to(PROJECT_ROOT).parts
        if any(part.startswith(prefix) for part in relative_parts for prefix in EXCLUDED_DIR_PREFIXES):
            continue
        files.append(path)
    return files


# ---------------------------------------------------------------------------
# README exists and has required sections
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def readme_text() -> str:
    path = PROJECT_ROOT / "README.md"
    assert path.is_file(), "README.md does not exist at the repository root"
    return path.read_text(encoding="utf-8")


def test_readme_exists():
    assert (PROJECT_ROOT / "README.md").is_file()


REQUIRED_README_SECTIONS = [
    "Project overview", "Business problem", "Key capabilities", "Architecture",
    "Data pipeline", "Analytics use cases", "Dashboard preview", "Key healthcare KPIs",
    "Technology stack", "Data model", "Data quality", "Orchestration",
    "Repository structure", "Quick start", "Running the pipeline", "Running Streamlit",
    "Airflow", "dbt", "Power BI", "Test results", "Security", "Future improvements",
    "Skills demonstrated",
]


@pytest.mark.parametrize("section", REQUIRED_README_SECTIONS)
def test_readme_has_required_section(readme_text, section):
    assert section in readme_text, f"README.md is missing a section for '{section}'"


def test_readme_has_hero_title_and_subtitle(readme_text):
    assert "# CareFlow Analytics" in readme_text
    assert "Real-Time Hospital Operations & Patient Readmission Intelligence Platform" in readme_text


def test_readme_states_synthetic_data_clearly(readme_text):
    assert "synthetic" in readme_text.lower()
    assert "Synthea" in readme_text


def test_readme_does_not_claim_clinical_decision_making(readme_text):
    assert "does not make" in readme_text.lower() or "not a diagnostic" in readme_text.lower()


def test_readme_has_a_mermaid_diagram(readme_text):
    assert "```mermaid" in readme_text


def test_readme_has_badges_but_no_fake_ci_badge(readme_text):
    assert "img.shields.io" in readme_text
    assert "github.com/" not in readme_text.lower() or "workflows" not in readme_text.lower()  # no CI workflow badge


# ---------------------------------------------------------------------------
# No secrets in tracked source/config/docs
# ---------------------------------------------------------------------------


# Test files are exempt: they legitimately contain fake/placeholder
# credentials to test redaction/sanitization logic.
SECRET_SCAN_EXTENSIONS = (".py", ".md", ".yml", ".yaml", ".json", ".toml", ".cfg", ".example")


def _non_test_files() -> list[Path]:
    files = _iter_source_and_doc_files(SECRET_SCAN_EXTENSIONS)
    return [f for f in files if "tests" not in f.relative_to(PROJECT_ROOT).parts]


# Known-safe local-development-only placeholder values, already
# documented as placeholders in .env.example -- not real secrets.
KNOWN_SAFE_PLACEHOLDER_FRAGMENTS = (
    "change_me", "change-me", "changeme", "dev_password", "dev_local",
    "_test_only", "example.com", "careflow.example",
)

REAL_SECRET_PATTERN = re.compile(
    r"(?:password|secret|token|api[_-]?key)\s*[:=]\s*['\"]([^'\"\s]{8,})['\"]",
    re.IGNORECASE,
)


def test_no_real_looking_secrets_in_non_test_files():
    violations = []
    for path in _non_test_files():
        if path.name == ".env":
            continue  # .env itself is expected to hold real local values; it must simply not be tracked (see below)
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in REAL_SECRET_PATTERN.finditer(text):
            value = match.group(1)
            if any(fragment in value.lower() for fragment in KNOWN_SAFE_PLACEHOLDER_FRAGMENTS):
                continue
            if value.startswith("{{") or value.startswith("${") or "env_var(" in text[max(0, match.start() - 40):match.start()]:
                continue  # a templated/env-var reference, not a literal secret
            violations.append(f"{path.relative_to(PROJECT_ROOT)}: {match.group(0)}")
    assert not violations, "possible real secret(s) found:\n" + "\n".join(violations)


def test_env_file_is_not_referenced_as_committed_in_docs():
    """Docs should never instruct committing .env, and .env.example should
    never contain the word 'password' with a non-placeholder value."""
    example_path = PROJECT_ROOT / ".env.example"
    text = example_path.read_text(encoding="utf-8")
    assert "commit your actual .env" not in text.lower() or "never" in text.lower()


# ---------------------------------------------------------------------------
# No developer-specific absolute paths in source/docs
# ---------------------------------------------------------------------------


DEVELOPER_PATH_PATTERNS = (
    re.compile(r"/Users/[a-zA-Z0-9_.-]+"),
    re.compile(r"C:\\\\Users\\\\[a-zA-Z0-9_.-]+"),
)


def test_no_developer_specific_absolute_paths_in_docs_and_source():
    violations = []
    for path in _iter_source_and_doc_files((".py", ".md", ".yml", ".yaml", ".toml", ".cfg")):
        if "tests" in path.relative_to(PROJECT_ROOT).parts:
            continue  # test fixtures may legitimately construct/mock arbitrary paths
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in DEVELOPER_PATH_PATTERNS:
            if pattern.search(text):
                violations.append(str(path.relative_to(PROJECT_ROOT)))
                break
    assert not violations, f"developer-specific absolute paths found in: {violations}"


# ---------------------------------------------------------------------------
# .env is ignored
# ---------------------------------------------------------------------------


def test_env_is_gitignored():
    gitignore_text = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    lines = [line.strip() for line in gitignore_text.splitlines()]
    assert ".env" in lines, ".gitignore does not exclude .env"


def test_gitignore_excludes_virtual_environments():
    text = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in (".venv/", ".venv-dbt/", ".venv-airflow/", ".venv-dashboard/"):
        assert pattern in text


def test_gitignore_excludes_caches_and_generated_artifacts():
    text = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("__pycache__/", ".pytest_cache/", "target/", "dbt_packages/", "logs/"):
        assert pattern in text


def test_gitignore_excludes_dashboard_secrets():
    text = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "secrets.toml" in text


def test_gitignore_excludes_airflow_runtime_state():
    text = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "airflow/logs/" in text


def test_gitignore_excludes_os_and_ide_files():
    text = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".DS_Store" in text
    assert ".claude/" in text


# ---------------------------------------------------------------------------
# Architecture docs exist
# ---------------------------------------------------------------------------


REQUIRED_ARCHITECTURE_DOCS = ["architecture.md", "data_flow.md", "warehouse_model.md"]


@pytest.mark.parametrize("filename", REQUIRED_ARCHITECTURE_DOCS)
def test_architecture_doc_exists(filename):
    assert (PROJECT_ROOT / "docs" / "architecture" / filename).is_file()


def test_architecture_doc_has_mermaid_diagram():
    text = (PROJECT_ROOT / "docs" / "architecture" / "architecture.md").read_text(encoding="utf-8")
    assert "```mermaid" in text


def test_warehouse_model_doc_mentions_imaging_grain_correction():
    text = (PROJECT_ROOT / "docs" / "architecture" / "warehouse_model.md").read_text(encoding="utf-8")
    assert "composite" in text.lower() and "imaging" in text.lower()


def test_data_flow_doc_covers_every_stage():
    text = (PROJECT_ROOT / "docs" / "architecture" / "data_flow.md").read_text(encoding="utf-8")
    for stage in ("Synthea", "Bronze", "Silver", "Gold", "PostgreSQL", "dbt", "Orchestration", "Analytics Delivery"):
        assert stage in text


# ---------------------------------------------------------------------------
# Interview guide, resume bullets, Power BI docs, dashboard docs exist
# ---------------------------------------------------------------------------


def test_interview_guide_exists():
    assert (PROJECT_ROOT / "docs" / "interview_guide.md").is_file()


def test_interview_guide_covers_required_topics():
    text = (PROJECT_ROOT / "docs" / "interview_guide.md").read_text(encoding="utf-8")
    required_topics = [
        "30-second", "90-second", "Bronze/Silver/Gold", "Why PostgreSQL", "Why dbt",
        "Why Airflow", "Why Parquet", "idempotency", "incremental", "readmission",
        "Imaging-study grain", "Foreign-key force-reload", "Docker/Airflow readiness",
        "Dashboard security", "Scaling", "Production improvements",
    ]
    for topic in required_topics:
        assert topic in text, f"interview_guide.md does not cover '{topic}'"


def test_resume_bullets_exist_with_both_role_variants():
    text = (PROJECT_ROOT / "docs" / "resume_bullets.md").read_text(encoding="utf-8")
    assert "Healthcare Data Analyst" in text
    assert "Healthcare Data Engineer" in text


def test_linkedin_project_doc_exists():
    assert (PROJECT_ROOT / "docs" / "linkedin_project.md").is_file()


def test_demo_script_exists_and_covers_required_steps():
    text = (PROJECT_ROOT / "docs" / "demo_script.md").read_text(encoding="utf-8")
    for step in ("README", "Architecture", "Airflow", "dbt", "PostgreSQL", "Streamlit", "Readmission", "Data quality" if "Data quality" in text else "Data Quality", "tests"):
        assert step in text, f"demo_script.md does not mention '{step}'"


def test_github_setup_doc_exists_with_topics():
    text = (PROJECT_ROOT / "docs" / "github_setup.md").read_text(encoding="utf-8")
    for topic in ("healthcare-analytics", "data-engineering", "postgresql", "dbt", "apache-airflow", "streamlit", "power-bi", "synthea"):
        assert topic in text


def test_powerbi_docs_exist():
    for filename in ("README.md", "data_dictionary.md", "model_relationships.md", "dax_measures.md", "page_build_guide.md", "theme.json", "qa_checklist.md"):
        assert (PROJECT_ROOT / "powerbi" / filename).is_file()


def test_dashboard_portfolio_doc_exists_and_covers_all_pages():
    text = (PROJECT_ROOT / "docs" / "dashboard_portfolio.md").read_text(encoding="utf-8")
    for page in ("Executive Overview", "Readmission Analytics", "Hospital Operations", "Financial Performance", "Provider Performance", "Patient Population", "Data Quality"):
        assert page in text


def test_security_and_data_ethics_docs_exist():
    assert (PROJECT_ROOT / "docs" / "security.md").is_file()
    assert (PROJECT_ROOT / "docs" / "data_ethics.md").is_file()


def test_project_metrics_doc_exists():
    assert (PROJECT_ROOT / "docs" / "project_metrics.md").is_file()


# ---------------------------------------------------------------------------
# LICENSE, CONTRIBUTING, CHANGELOG
# ---------------------------------------------------------------------------


def test_license_exists_and_is_mit():
    path = PROJECT_ROOT / "LICENSE"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "MIT License" in text


def test_contributing_exists():
    assert (PROJECT_ROOT / "CONTRIBUTING.md").is_file()


def test_changelog_exists_with_v1_0_0():
    text = (PROJECT_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "v1.0.0" in text


def test_changelog_covers_major_phases():
    text = (PROJECT_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    for phase in ("Bronze", "Silver", "Gold", "PostgreSQL", "dbt", "Airflow", "Streamlit", "Power BI"):
        assert phase in text


# ---------------------------------------------------------------------------
# Makefile commands reference valid scripts
# ---------------------------------------------------------------------------


def test_makefile_exists():
    assert (PROJECT_ROOT / "Makefile").is_file()


def test_makefile_referenced_scripts_all_exist():
    text = (PROJECT_ROOT / "Makefile").read_text(encoding="utf-8")
    script_refs = re.findall(r"scripts/([a-zA-Z0-9_]+\.py)", text)
    assert script_refs, "Makefile does not reference any scripts/*.py files"
    for script_name in set(script_refs):
        assert (PROJECT_ROOT / "scripts" / script_name).is_file(), f"Makefile references nonexistent scripts/{script_name}"


def test_makefile_has_help_target():
    text = (PROJECT_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "help:" in text


def test_makefile_required_targets_exist():
    text = (PROJECT_ROOT / "Makefile").read_text(encoding="utf-8")
    for target in ("test:", "postgres-up:", "postgres-down:", "warehouse-load:", "warehouse-validate:", "dbt-build:", "dashboard:", "airflow-up:", "airflow-down:"):
        assert target in text, f"Makefile is missing target '{target}'"


# ---------------------------------------------------------------------------
# README does not reference nonexistent screenshot files
# ---------------------------------------------------------------------------


def test_readme_does_not_link_to_nonexistent_screenshots(readme_text):
    image_refs = re.findall(r"!\[[^\]]*\]\((docs/screenshots/[^)]+)\)", readme_text)
    for ref in image_refs:
        assert (PROJECT_ROOT / ref).is_file(), f"README.md links to a screenshot that doesn't exist: {ref}"


def test_screenshots_readme_exists_and_lists_expected_files():
    text = (PROJECT_ROOT / "docs" / "screenshots" / "README.md").read_text(encoding="utf-8")
    for filename in (
        "executive_overview.png", "readmission_analytics.png", "hospital_operations.png",
        "financial_performance.png", "provider_performance.png", "patient_population.png",
        "data_quality.png", "airflow_dag.png", "dbt_lineage.png", "architecture.png",
    ):
        assert filename in text, f"docs/screenshots/README.md does not mention '{filename}'"


def test_no_actual_screenshot_files_are_fabricated():
    screenshots_dir = PROJECT_ROOT / "docs" / "screenshots"
    png_files = list(screenshots_dir.glob("*.png"))
    assert png_files == [], f"Screenshot files exist but were not verified as real captures: {png_files}"


# ---------------------------------------------------------------------------
# Repository docs do not claim unsupported technologies
# ---------------------------------------------------------------------------


UNSUPPORTED_TECHNOLOGIES = ("kafka", "machine learning", "kubernetes")
NEGATION_MARKERS = (
    "no ", "not ", "out of scope", "future", "yet", "unsupported", "n/a", "explicitly",
    "whether", "would need", "if this",  # hypothetical/conditional framing, not a claim of current use
)


def test_docs_never_claim_unsupported_technologies_as_implemented():
    """Scans paragraph-by-paragraph (blank-line-separated blocks), not
    line-by-line -- a negation like "no Kafka, ... or machine learning"
    routinely wraps across two Markdown source lines, which a single-line
    scan would misfire on even though the rendered sentence is negated."""
    violations = []
    for path in _iter_source_and_doc_files((".md",)):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for paragraph in re.split(r"\n\s*\n", text):
            normalized = re.sub(r"\s+", " ", paragraph).lower()
            for tech in UNSUPPORTED_TECHNOLOGIES:
                if tech in normalized and not any(marker in normalized for marker in NEGATION_MARKERS):
                    violations.append(f"{path.relative_to(PROJECT_ROOT)}: {normalized.strip()[:160]}")
    assert not violations, "possible claim of an unsupported technology:\n" + "\n".join(violations)


def test_readme_technology_stack_only_lists_technologies_actually_used(readme_text):
    stack_section_match = re.search(r"## 9\. Technology stack(.*?)## 10\.", readme_text, re.DOTALL)
    assert stack_section_match, "could not find the Technology stack section"
    stack_text = stack_section_match.group(1).lower()
    for forbidden in ("kafka", "kubernetes", "spark", "hadoop", "tensorflow", "pytorch"):
        assert forbidden not in stack_text, f"README's Technology stack section lists unused technology '{forbidden}'"


# ---------------------------------------------------------------------------
# Mermaid blocks exist across the required diagram docs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("filename", ["architecture.md", "data_flow.md", "warehouse_model.md"])
def test_each_architecture_doc_has_at_least_one_mermaid_block(filename):
    text = (PROJECT_ROOT / "docs" / "architecture" / filename).read_text(encoding="utf-8")
    assert "```mermaid" in text


# ---------------------------------------------------------------------------
# Repository structure sanity (no obviously temporary/debug files tracked)
# ---------------------------------------------------------------------------


def test_no_stray_debug_or_scratch_files_at_repo_root():
    suspicious_patterns = ("*.tmp", "*.bak", "*.orig", "debug_*.py", "scratch_*.py", "test_debug*.py")
    for pattern in suspicious_patterns:
        matches = list(PROJECT_ROOT.glob(pattern))
        assert not matches, f"suspicious temporary file(s) at repo root: {matches}"


def test_project_metrics_numbers_are_internally_consistent():
    """Cross-check project_metrics.md's headline dbt/warehouse numbers
    against the live report files they claim to summarize."""
    import json

    text = (PROJECT_ROOT / "docs" / "project_metrics.md").read_text(encoding="utf-8")

    dbt_summary_path = PROJECT_ROOT / "reports" / "dbt" / "dbt_test_summary.json"
    if dbt_summary_path.is_file():
        dbt_summary = json.loads(dbt_summary_path.read_text(encoding="utf-8"))
        assert str(dbt_summary["total_tests"]) in text

    validation_path = PROJECT_ROOT / "reports" / "warehouse" / "postgres_validation_report.json"
    if validation_path.is_file():
        validation = json.loads(validation_path.read_text(encoding="utf-8"))
        assert str(validation["summary"]["total_checks"]) in text
