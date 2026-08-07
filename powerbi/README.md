# CareFlow Analytics -- Power BI Implementation Package (Phase 5B)

This is a **preparation package**, not a `.pbix` file. Power BI Desktop
is a Windows/GUI application not available in this environment, so
everything needed to build the final report quickly and correctly on
Windows lives here instead: audited source data, a documented data
model, production-quality DAX, a professional theme, and a
field-by-field build guide. No `.pbix` is fabricated.

## What's in this package

| File | Purpose |
|---|---|
| `README.md` | This file -- overview and how to use the package |
| `data_dictionary.md` | Every field in every export: type, meaning, grain, safe-for-reporting |
| `model_relationships.md` | The star-schema model: tables, keys, cardinality, filter direction |
| `dax_measures.md` | Every DAX measure, grouped by topic, ready to paste into Power BI |
| `page_build_guide.md` | Field-by-field instructions for all 7 report pages |
| `theme.json` | A valid Power BI JSON theme (professional healthcare palette) |
| `qa_checklist.md` | Final checklist to run through before calling the report done |

See also `docs/powerbi_final_build_guide.md` for the end-to-end,
start-to-finish build sequence (import -> model -> measures -> pages -> QA).

## Source data

`data/exports/powerbi/*.csv` -- seven files, all generated directly from
`careflow_dbt_mart` (six were exported in Phase 5A; `data_quality_status.csv`
is new in this phase, generated from the existing pipeline reports via
`dashboard/reports.py`'s report-loading logic, never fabricated):

- `executive_monthly.csv`
- `readmission_analysis.csv`
- `financial_analysis.csv`
- `hospital_operations.csv`
- `provider_performance.csv`
- `patient_population.csv`
- `data_quality_status.csv` **(new)**

## Audit summary (Phase 5B)

All seven exports were re-audited before this package was written: row
counts, column types, null patterns, duplicate-grain checks, date
validity, negative-value checks, and reconciliation against the live
PostgreSQL warehouse. Full detail in `data_dictionary.md`; headline
findings:

- **No restricted PII in any file.** No SSN, passport, driver's
  license, first/middle/last name (patient), street address, or precise
  latitude/longitude in any column, in any of the seven exports.
  Provider names (`provider_performance.csv`) are business/operational
  identifiers, not patient PII, and are expected on a Provider
  Performance page exactly as they are in the Streamlit dashboard.
- **No duplicate rows at the expected grain** in any file.
- **Financial and readmission totals reconcile exactly** with a live
  query against `careflow_dbt_mart` (`fct_encounters`, `fct_readmissions`) --
  same total claim cost, same payer coverage, same 30-day readmission
  count and rate, to the cent/row.
- **`executive_monthly.csv` spans 1943-09 through 2026-07 (441 months).**
  This is genuine, not a defect: Synthea generates full synthetic
  patient life histories, so older patients carry real historical
  encounters decades back. Every row has `total_encounters > 0` -- there
  is no zero-padding across the full calendar range. **Recommendation:**
  default every date slicer to the last 24-36 months (see
  `page_build_guide.md`) so charts read cleanly for a recruiter
  audience; leave the full range available for drill-down.
- **18 rows in `hospital_operations.csv` and 18 rows in
  `provider_performance.csv` carry nulls** (`encounter_class`/`encounter_date`
  and `year_month` respectively, with counts/durations at 0). These are
  organizations and providers that exist in the warehouse's dimension
  tables but have zero recorded encounters in the synthetic dataset --
  included via the dbt marts' own outer joins for dimensional
  completeness, not a data quality defect. Documented per-field in
  `data_dictionary.md`.
- **`readmission_analysis.csv`'s `next_encounter_*` and
  `days_to_readmission` nulls (40-45 rows)** represent patients who were
  never readmitted -- correct by definition, not missing data.
- No export required regeneration -- every null/edge case found is a
  genuine, documented characteristic of the underlying dbt marts, not a
  modeling defect.

## How to use this package on Windows

1. Copy `data/exports/powerbi/*.csv` and `powerbi/theme.json` to the
   Windows machine (or a shared drive/OneDrive).
2. Open Power BI Desktop -> **Get Data -> Text/CSV** for each of the
   seven files.
3. Build the model per `model_relationships.md` (star schema, one Date
   table, relationships as specified -- avoid the many-to-many
   relationships that table explicitly calls out as unnecessary).
4. Paste in every measure from `dax_measures.md`.
5. Apply the theme: **View -> Themes -> Browse for themes** -> `theme.json`.
6. Build each page following `page_build_guide.md` exactly -- visual
   type, fields, measures, axis/legend assignments, slicers, and
   formatting are all specified per page.
7. Run through `qa_checklist.md` before considering the report finished.

## Explicitly out of scope for this phase

- No `.pbix` file (Power BI Desktop unavailable here).
- No changes to Bronze/Silver/Gold, PostgreSQL, dbt, Airflow, or the
  Streamlit dashboard -- this phase only prepares Power BI inputs.
- No fabricated screenshots or metrics.
