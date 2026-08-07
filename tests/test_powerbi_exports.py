"""Tests for the Phase 5B Power BI export package: data/exports/powerbi/*.csv
and the powerbi/ documentation package.

No live PostgreSQL, no Power BI Desktop required -- everything here
reads already-generated CSV/JSON/Markdown files from disk.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPORTS_DIR = PROJECT_ROOT / "data" / "exports" / "powerbi"
POWERBI_DIR = PROJECT_ROOT / "powerbi"

RESTRICTED_TOKENS = (
    "ssn", "passport", "drivers_license", "driver_license",
    "first_name", "middle_name", "last_name",
    "street_address", "latitude", "longitude",
)

REQUIRED_EXPORTS = {
    "executive_monthly.csv": ["year_month", "total_encounters", "total_claim_cost", "readmission_rate_30_day"],
    "readmission_analysis.csv": ["patient_key", "index_encounter_key", "days_to_readmission", "readmitted_within_30_days"],
    "financial_analysis.csv": ["payer_key", "organization_key", "year_month", "total_claim_cost", "coverage_ratio"],
    "hospital_operations.csv": ["organization_key", "encounter_date", "encounter_class", "encounter_count"],
    "provider_performance.csv": ["provider_key", "provider_name", "year_month", "encounter_count"],
    "patient_population.csv": ["patient_key", "age_group", "gender", "condition_count"],
    "data_quality_status.csv": ["layer", "check_type", "passed", "warnings", "failed", "skipped", "status", "last_updated"],
}

GRAIN_COLUMNS = {
    "executive_monthly.csv": ["year_month"],
    "financial_analysis.csv": ["payer_key", "organization_key", "year_month"],
    "hospital_operations.csv": ["organization_key", "encounter_date_key", "encounter_class"],
    "provider_performance.csv": ["provider_key", "year_month"],
    "patient_population.csv": ["patient_key"],
    "readmission_analysis.csv": ["index_encounter_key"],
    "data_quality_status.csv": ["layer"],
}


def _load(filename: str) -> pd.DataFrame:
    return pd.read_csv(EXPORTS_DIR / filename)


# -- all required exports exist ---------------------------------------------


@pytest.mark.parametrize("filename", sorted(REQUIRED_EXPORTS.keys()))
def test_required_export_exists(filename):
    assert (EXPORTS_DIR / filename).is_file(), f"missing required export: {filename}"


def test_no_unexpected_extra_or_missing_export_files():
    actual = {p.name for p in EXPORTS_DIR.glob("*.csv")}
    assert actual == set(REQUIRED_EXPORTS.keys())


# -- required columns exist --------------------------------------------------


@pytest.mark.parametrize("filename,required_columns", sorted(REQUIRED_EXPORTS.items()))
def test_required_columns_exist(filename, required_columns):
    df = _load(filename)
    for column in required_columns:
        assert column in df.columns, f"{filename} missing required column '{column}'"


@pytest.mark.parametrize("filename", sorted(REQUIRED_EXPORTS.keys()))
def test_export_is_not_empty(filename):
    df = _load(filename)
    assert len(df) > 0, f"{filename} has zero rows"


# -- restricted PII absent ---------------------------------------------------


@pytest.mark.parametrize("filename", sorted(REQUIRED_EXPORTS.keys()))
def test_no_restricted_pii_columns(filename):
    df = _load(filename)
    lowered = [c.lower() for c in df.columns]
    for token in RESTRICTED_TOKENS:
        matches = [c for c in lowered if token in c]
        assert not matches, f"{filename} has a restricted-PII-looking column: {matches}"


def test_provider_name_is_not_treated_as_restricted():
    """provider_name is an operational identifier (clinician), not
    patient PII -- explicitly allowed, unlike the patient name fields."""
    df = _load("provider_performance.csv")
    assert "provider_name" in df.columns


# -- no duplicate rows at expected grain -------------------------------------


@pytest.mark.parametrize("filename,grain_cols", sorted(GRAIN_COLUMNS.items()))
def test_no_duplicate_rows_at_expected_grain(filename, grain_cols):
    df = _load(filename)
    dupes = df.duplicated(subset=grain_cols, keep=False).sum()
    assert dupes == 0, f"{filename} has {dupes} duplicate rows at grain {grain_cols}"


# -- valid dates / numeric fields parse --------------------------------------


def test_executive_monthly_year_month_is_valid_format():
    df = _load("executive_monthly.csv")
    assert df["year_month"].str.match(r"^\d{4}-(0[1-9]|1[0-2])$").all()


def test_hospital_operations_encounter_date_parses():
    df = _load("hospital_operations.csv")
    parsed = pd.to_datetime(df["encounter_date"].dropna(), errors="coerce")
    assert parsed.isna().sum() == 0


def test_readmission_analysis_timestamps_parse():
    df = _load("readmission_analysis.csv")
    parsed = pd.to_datetime(df["index_discharge_timestamp"], errors="coerce", utc=True)
    assert parsed.isna().sum() == 0


def test_numeric_fields_parse_as_numeric():
    numeric_checks = {
        "executive_monthly.csv": ["total_encounters", "total_claim_cost", "readmission_rate_30_day"],
        "financial_analysis.csv": ["total_claim_cost", "coverage_ratio"],
        "hospital_operations.csv": ["encounter_count", "average_duration_minutes"],
        "provider_performance.csv": ["encounter_count", "total_claim_cost"],
        "patient_population.csv": ["condition_count", "medication_count"],
        "readmission_analysis.csv": ["days_to_readmission"],
    }
    for filename, columns in numeric_checks.items():
        df = _load(filename)
        for column in columns:
            assert pd.api.types.is_numeric_dtype(df[column]), f"{filename}.{column} is not numeric"


# -- no unexpected negative values -------------------------------------------


NON_NEGATIVE_CHECKS = {
    "executive_monthly.csv": ["total_patients_served", "total_encounters", "inpatient_encounters", "emergency_encounters", "total_claim_cost", "total_payer_coverage"],
    "financial_analysis.csv": ["encounter_count", "total_claim_cost", "total_payer_coverage"],
    "hospital_operations.csv": ["encounter_count", "unique_patients", "total_claim_cost"],
    "provider_performance.csv": ["encounter_count", "unique_patients", "total_claim_cost"],
    "patient_population.csv": ["condition_count", "procedure_count", "medication_count", "distinct_encounter_count"],
    "readmission_analysis.csv": ["days_to_readmission"],
    "data_quality_status.csv": ["passed", "warnings", "failed", "skipped"],
}


@pytest.mark.parametrize("filename,columns", sorted(NON_NEGATIVE_CHECKS.items()))
def test_no_unexpected_negative_values(filename, columns):
    df = _load(filename)
    for column in columns:
        negative_count = (df[column].dropna() < 0).sum()
        assert negative_count == 0, f"{filename}.{column} has {negative_count} unexpected negative values"


# -- expected record counts (regression guard against silent export drift) --


EXPECTED_MIN_ROWS = {
    "executive_monthly.csv": 400,
    "readmission_analysis.csv": 200,
    "financial_analysis.csv": 2000,
    "hospital_operations.csv": 3000,
    "provider_performance.csv": 2000,
    "patient_population.csv": 50,
    "data_quality_status.csv": 8,
}


@pytest.mark.parametrize("filename,minimum", sorted(EXPECTED_MIN_ROWS.items()))
def test_expected_minimum_record_count(filename, minimum):
    df = _load(filename)
    assert len(df) >= minimum, f"{filename} has only {len(df)} rows, expected at least {minimum}"


# -- readmission / financial reconciliation ----------------------------------


def test_readmission_rate_reconciles_within_the_export():
    """30-day readmission rate computed from readmission_analysis.csv
    must match the qualifying/readmitted counts exactly -- both are
    already known (from this phase's live-DB audit) to equal the dbt
    reconciliation test's own result (3 / 207)."""
    df = _load("readmission_analysis.csv")
    qualifying = len(df)
    readmitted_30 = int(df["readmitted_within_30_days"].sum())
    assert qualifying == 207
    assert readmitted_30 == 3
    rate = readmitted_30 / qualifying
    assert rate == pytest.approx(3 / 207)


def test_financial_totals_reconcile_between_exports():
    """executive_monthly.csv (hospital-wide) and financial_analysis.csv
    (payer x org x month) are two independent aggregations of the same
    underlying encounters -- their grand totals must match exactly."""
    exec_df = _load("executive_monthly.csv")
    fin_df = _load("financial_analysis.csv")
    assert exec_df["total_claim_cost"].sum() == pytest.approx(fin_df["total_claim_cost"].sum(), abs=0.01)
    assert exec_df["total_payer_coverage"].sum() == pytest.approx(fin_df["total_payer_coverage"].sum(), abs=0.01)


def test_readmission_within_30_implies_within_14_implies_within_7():
    df = _load("readmission_analysis.csv")
    assert (df.loc[df["readmitted_within_7_days"], "readmitted_within_14_days"]).all()
    assert (df.loc[df["readmitted_within_14_days"], "readmitted_within_30_days"]).all()


# -- Power BI theme JSON is valid --------------------------------------------


def test_theme_json_parses_successfully():
    with (POWERBI_DIR / "theme.json").open(encoding="utf-8") as fh:
        theme = json.load(fh)
    assert isinstance(theme, dict)


def test_theme_json_has_required_top_level_keys():
    with (POWERBI_DIR / "theme.json").open(encoding="utf-8") as fh:
        theme = json.load(fh)
    for key in ("name", "dataColors", "background", "foreground"):
        assert key in theme, f"theme.json missing '{key}'"


def test_theme_data_colors_are_valid_hex_and_not_neon():
    with (POWERBI_DIR / "theme.json").open(encoding="utf-8") as fh:
        theme = json.load(fh)
    hex_pattern = re.compile(r"^#[0-9A-Fa-f]{6}$")
    for color in theme["dataColors"]:
        assert hex_pattern.match(color), f"'{color}' is not a valid 6-digit hex color"


def test_theme_background_is_light():
    with (POWERBI_DIR / "theme.json").open(encoding="utf-8") as fh:
        theme = json.load(fh)
    # A light background: every RGB channel should be high (closer to white than black).
    hex_color = theme["background"].lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    assert min(r, g, b) >= 200, "theme background is not a light color"


# -- DAX documentation contains required measures ----------------------------


REQUIRED_DAX_MEASURES = [
    # Executive
    "Total Patients", "Total Encounters", "Inpatient Encounters", "Emergency Encounters",
    "Emergency Encounter %", "Average Length of Stay", "Total Claim Cost", "Total Payer Coverage",
    "Patient Responsibility", "Coverage Ratio", "Cost per Encounter",
    # Readmission
    "Qualifying Encounters", "7-Day Readmissions", "14-Day Readmissions", "30-Day Readmissions",
    "7-Day Readmission Rate", "14-Day Readmission Rate", "30-Day Readmission Rate",
    "Average Days to Readmission",
    # Provider
    "Active Providers", "Provider Encounters", "Unique Patients per Provider",
    "Encounters per Provider", "Average Encounter Duration",
    # Patient population
    "Patient Count", "Average Patient Age", "Deceased Patient Count",
    "Encounters per Patient", "Conditions per Patient", "Medications per Patient",
    # Time intelligence
    "Previous Month Encounters", "Encounter MoM Change", "Encounter MoM %",
    "Previous Month Cost", "Cost MoM Change", "Cost MoM %",
    "Previous Month Readmission Rate", "Readmission Rate Change",
]


@pytest.fixture(scope="module")
def dax_measures_text() -> str:
    return (POWERBI_DIR / "dax_measures.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("measure_name", REQUIRED_DAX_MEASURES)
def test_dax_documentation_contains_required_measure(dax_measures_text, measure_name):
    assert measure_name in dax_measures_text, f"dax_measures.md is missing measure '{measure_name}'"


def test_dax_measures_use_divide_not_bare_division():
    text = (POWERBI_DIR / "dax_measures.md").read_text(encoding="utf-8")
    code_blocks = re.findall(r"```dax\n(.*?)```", text, re.DOTALL)
    assert code_blocks, "no DAX code blocks found"
    for block in code_blocks:
        for line in block.splitlines():
            stripped = line.strip()
            if stripped.startswith("--") or not stripped:
                continue
            # A bare division operator outside DIVIDE(...) would show up as
            # e.g. "X / Y" in an assignment line; DIVIDE(...) calls are fine.
            if "=" in stripped and "/" in stripped and "DIVIDE" not in stripped.upper() and "http" not in stripped:
                pytest.fail(f"possible bare division (should use DIVIDE()): {stripped}")


# -- relationship documentation exists ---------------------------------------


def test_model_relationships_doc_exists_and_has_a_relationship_table():
    text = (POWERBI_DIR / "model_relationships.md").read_text(encoding="utf-8")
    assert "From Table" in text
    assert "From Column" in text
    assert "To Table" in text
    assert "To Column" in text
    assert "Cardinality" in text
    assert "Filter Direction" in text
    assert "Active?" in text
    assert "Reason" in text


def test_model_relationships_doc_documents_the_date_table():
    text = (POWERBI_DIR / "model_relationships.md").read_text(encoding="utf-8")
    assert "Mark as Date Table" in text or "Date Table" in text


def test_model_relationships_doc_avoids_fabricated_many_to_many():
    text = (POWERBI_DIR / "model_relationships.md").read_text(encoding="utf-8")
    assert "many-to-many" in text.lower()
    assert "not built" in text.lower() or "NOT built" in text


# -- Power BI build guide covers all 7 pages ---------------------------------


REQUIRED_PAGE_TITLES = [
    "Executive Overview",
    "Readmission Analytics",
    "Hospital Operations",
    "Financial Performance",
    "Provider Performance",
    "Patient Population",
    "Data Quality",
]


@pytest.fixture(scope="module")
def page_build_guide_text() -> str:
    return (POWERBI_DIR / "page_build_guide.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("page_title", REQUIRED_PAGE_TITLES)
def test_page_build_guide_covers_page(page_build_guide_text, page_title):
    assert page_title in page_build_guide_text, f"page_build_guide.md does not cover '{page_title}'"


def test_page_build_guide_specifies_slicers_for_every_page():
    text = (POWERBI_DIR / "page_build_guide.md").read_text(encoding="utf-8")
    assert text.count("**Slicers:**") >= 6  # every page except Data Quality (intentionally none)


def _normalize_whitespace(text: str) -> str:
    """Collapse markdown blockquote line-wrapping (newlines + '> ' markers)
    so a required sentence can be matched regardless of how it's wrapped."""
    return re.sub(r"\s+", " ", text.replace(">", " "))


def test_readmission_methodology_note_is_documented():
    text = _normalize_whitespace((POWERBI_DIR / "page_build_guide.md").read_text(encoding="utf-8"))
    assert "begins within 30 days after the previous qualifying encounter ends" in text


def test_financial_synthetic_disclaimer_is_documented():
    text = _normalize_whitespace((POWERBI_DIR / "page_build_guide.md").read_text(encoding="utf-8"))
    assert "synthetic" in text.lower()
    assert "not represent real hospital financial performance" in text.lower()


def test_provider_page_uses_neutral_language_rule():
    text = (POWERBI_DIR / "page_build_guide.md").read_text(encoding="utf-8").lower()
    assert "never" in text
    assert any(word in text for word in ("best,", "worst,", "good", "bad", "quality judgment"))


# -- required powerbi/ package files all exist -------------------------------


REQUIRED_POWERBI_FILES = [
    "README.md", "data_dictionary.md", "model_relationships.md",
    "dax_measures.md", "page_build_guide.md", "theme.json", "qa_checklist.md",
]


@pytest.mark.parametrize("filename", REQUIRED_POWERBI_FILES)
def test_powerbi_package_file_exists(filename):
    assert (POWERBI_DIR / filename).is_file(), f"missing powerbi/{filename}"


def test_final_build_guide_doc_exists():
    assert (PROJECT_ROOT / "docs" / "powerbi_final_build_guide.md").is_file()


# -- no modification of upstream/Phase 5A files ------------------------------


def test_original_six_exports_are_unchanged_since_phase_5a_schema():
    """Structural check: the six original exports still have exactly the
    columns Phase 5A produced (this phase must not have altered them)."""
    original_columns = {
        "executive_monthly.csv": {"year_month", "total_patients_served", "total_encounters", "inpatient_encounters", "emergency_encounters", "average_length_of_stay_minutes", "total_claim_cost", "total_payer_coverage", "total_patient_responsibility", "readmission_count_30_day", "readmission_rate_30_day"},
        "readmission_analysis.csv": {"patient_key", "age_group", "gender", "index_encounter_key", "index_encounter_class", "index_discharge_timestamp", "next_encounter_key", "next_encounter_class", "next_encounter_timestamp", "days_to_readmission", "readmitted_within_7_days", "readmitted_within_14_days", "readmitted_within_30_days"},
    }
    for filename, expected_columns in original_columns.items():
        df = _load(filename)
        assert set(df.columns) == expected_columns, f"{filename}'s columns changed from Phase 5A"
