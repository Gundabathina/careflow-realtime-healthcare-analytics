# Power BI Page Build Guide (Phase 5B)

Field-by-field instructions for all 7 pages. Follow in order -- build
the model and measures first (`model_relationships.md`, `dax_measures.md`),
apply the theme (`theme.json`), then build pages in this order. Every
page: **Format -> Page information -> Name** the page exactly as
titled below (these become the report's navigation).

General conventions used throughout:
- **Card** = the modern "Card" visual (single KPI, big number + label).
- All currency measures: Format -> Data label -> Display units = None
  (show full numbers, not "1.2M"), unless a chart axis is explicitly
  noted as abbreviated below.
- Every chart gets an explicit **Title** (Format pane -> General ->
  Title) -- never leave the default field-name title.
- Every axis gets an explicit **Axis title** (Format -> X axis / Y axis
  -> Title).
- Add **Sync Slicers** (View -> Sync Slicers pane) for `Organization`,
  `Payer`, and the date slicer across pages where the same slicer
  appears, so filter state carries over as a recruiter clicks through
  pages.

---

## Page 1 -- Executive Overview

**Cards** (top row, 6 cards, `Card` visual, one measure each):

| Card | Measure |
|---|---|
| Patients Served | `[Total Patients]` |
| Total Encounters | `[Total Encounters]` |
| 30-Day Readmission Rate | `[30-Day Readmission Rate]` |
| Average LOS | `[Average Length of Stay]` |
| Total Claim Cost | `[Total Claim Cost]` |
| Coverage Ratio | `[Coverage Ratio]` |

**Visuals:**

| # | Visual type | Table/Fields | Axis | Legend | Values | Sort | Notes |
|---|---|---|---|---|---|---|---|
| 1 | Line chart | `executive_monthly` | `Date[YearMonth]` | -- | `[Total Encounters]` | X axis chronological (already sorted via Date table) | Title: "Encounter Trend by Month" |
| 2 | Line chart | `executive_monthly` | `Date[YearMonth]` | -- | `[Total Claim Cost]` | chronological | Title: "Claim Cost Trend by Month"; Y-axis format `$#,##0,,"K"` if crowded |
| 3 | Donut or Stacked Bar | `hospital_operations` | `encounter_class` | `encounter_class` | `Sum of encounter_count` | Descending by value | Title: "Encounter Class Distribution". Prefer stacked bar if >5 categories are visible (there are up to 10) |
| 4 | Line chart | `executive_monthly` | `Date[YearMonth]` | -- | `[Monthly Readmission Rate]` | chronological | Title: "30-Day Readmission Rate Trend"; Y-axis Percentage format |
| 5 | Horizontal bar | `hospital_operations` | `organization_name` | -- | `Sum of encounter_count` | Descending, **Top N filter = 10** | Title: "Top 10 Organizations by Encounters" |
| 6 | Bar chart | `patient_population` | `age_group` | -- | `Count of patient_key` | Sort by `age_group` (custom sort -- see note) | Title: "Patients by Age Group". Age groups don't sort alphabetically correctly (`"18-34"` < `"35-49"` alphabetically works here by luck, but `"0-17"` vs `"80+"` doesn't) -- add a `Sort Order` calculated column (0,1,2,3,4,5 for the six buckets) and sort the `age_group` column by it (Column tools -> Sort by Column) |
| 7 | Donut chart | `financial_analysis` | `payer_name` | `payer_name` | `Sum of total_payer_coverage` | Descending by value | Title: "Payer Coverage Breakdown". This is the one appropriate donut use per the brief -- a small-category composition breakdown |

**Slicers:** Date range (`Date[Date]`, between), `Organization`
(`hospital_operations[organization_name]`), `Encounter Class`
(`hospital_operations[encounter_class]`), `Payer`
(`financial_analysis[payer_name]`). **Default the Date slicer to the
last 36 months** (relative date filter, "in the last 36 months") --
see `model_relationships.md` for why.

**Formatting:** currency cards -> `$#,##0`; `30-Day Readmission Rate`
card -> `0.0%`; `Average LOS` card -> `#,##0 "min"`.

---

## Page 2 -- Readmission Analytics

**Cards** (5): Qualifying Encounters `[Qualifying Encounters]`, 7-Day
Rate `[7-Day Readmission Rate]`, 14-Day Rate `[14-Day Readmission
Rate]`, 30-Day Rate `[30-Day Readmission Rate]`, Average Days to
Readmission `[Average Days to Readmission]`.

**Methodology note (required):** add a **Text box** directly under the
title, exact wording:

> "A qualifying readmission occurs when a subsequent inpatient or
> emergency encounter begins within 30 days after the previous
> qualifying encounter ends."

Style it in a bordered container (Format -> Effects -> Border, or a
Shape rectangle behind the text box) so it reads as a definition box,
not body copy.

**Visuals:**

| # | Visual type | Table/Fields | Axis | Legend | Values | Sort | Notes |
|---|---|---|---|---|---|---|---|
| 1 | Line chart | `executive_monthly` | `Date[YearMonth]` | -- | `[Monthly Readmission Rate]` | chronological | Title: "Readmission Rate Trend" |
| 2 | Horizontal bar | `readmission_analysis` joined via `index_encounter_key` (row context) -- actually use a table visual grouped: create a bar with `Axis = organization not available here` -- see note | -- | -- | -- | -- | **`readmission_analysis` has no organization field.** Build this visual from a Power Query merge (see below) or omit and note it as a known gap for a later export iteration. Recommended: skip this specific chart in v1 and replace with "Readmissions by Index Encounter Class" (#3 below) shown twice (by class, by age) plus the segment table -- do not fabricate an organization column. |
| 3 | Horizontal bar | `readmission_analysis` | `index_encounter_class` | -- | `[30-Day Readmission Rate]` (as a measure, with `index_encounter_class` on rows) | Descending | Title: "Readmission Rate by Index Encounter Class" |
| 4 | Horizontal bar | `readmission_analysis` | `age_group` | -- | `[30-Day Readmission Rate]` | Sort by age-group order (see Page 1 note) | Title: "Readmission Rate by Age Group" |
| 5 | Horizontal bar | `readmission_analysis` | `gender` | -- | `[30-Day Readmission Rate]` | Descending | Title: "Readmission Rate by Gender" |
| 6 | Histogram (bar chart on binned `days_to_readmission`) | `readmission_analysis` **filtered to `readmitted_within_30_days = TRUE`** | `days_to_readmission` (binned, bin size 5) | -- | `Count of index_encounter_key` | X ascending | Title: "Distribution of Days to Readmission (readmissions only)". Apply a **visual-level filter**: `readmitted_within_30_days = TRUE` -- otherwise the open-ended non-readmission rows (up to 14,000+ days) wreck the bin scale (see `data_dictionary.md`'s methodology note) |
| 7 (table) | Table/Matrix | `readmission_analysis` | Rows: `age_group`, `gender` | -- | `[Qualifying Encounters]`, `[30-Day Readmissions]`, `[30-Day Readmission Rate]` | Sort by rate, descending | Title: "High-Readmission Segments (Age Group x Gender)". This is the required "aggregated segment matrix" -- it is already patient-de-identified (grouped, no `patient_key` column included) |

**On the missing organization breakdown:** `readmission_analysis.csv`
does not carry `organization_key`/`organization_name` (see
`model_relationships.md` -- no relationship exists to add it without a
new export). If "Readmissions by Organization" is required for the
final report, the cleanest fix is a **new** export in a later iteration
(`readmission_analysis` joined to `hospital_operations`'s organization
on `index_encounter_key` upstream in the dbt layer or the export
script) -- not a Power BI-side workaround that would silently fan out
rows. Documented as a known gap here rather than worked around with a
fragile merge.

**Slicers:** Date range (via `index_discharge_timestamp`'s year if
added as a column, or omit if not needed), Age Group, Gender,
Encounter Class.

**Formatting:** rate cards -> `0.0%`; `Average Days to Readmission` ->
`#,##0.0 "days"`.

---

## Page 3 -- Hospital Operations

**Cards** (6): Encounters `[Total Encounters]` *(or `SUM(hospital_operations[encounter_count])` if built from this table directly)*, Unique Patients `DISTINCTCOUNT` is not valid across pre-aggregated rows -- use `SUM(hospital_operations[unique_patients])` as an approximation and label it "Unique Patient-Visits" if summing across organizations double-counts a patient seen at two orgs (rare in this dataset but worth the accurate label), Average Encounter Duration `AVERAGE(hospital_operations[average_duration_minutes])`, Emergency % `DIVIDE(SUM(hospital_operations[emergency_count]), SUM(hospital_operations[encounter_count]))`, Inpatient % `DIVIDE(SUM(hospital_operations[inpatient_count]), SUM(hospital_operations[encounter_count]))`, Active Providers `[Active Providers]` (from the Provider measures group).

**Visuals:**

| # | Visual type | Table/Fields | Axis | Legend | Values | Sort | Notes |
|---|---|---|---|---|---|---|---|
| 1 | Line chart | `hospital_operations` | `Date[Date]` (or `Date[YearMonth]` for a smoother monthly line) | -- | `Sum of encounter_count` | chronological | Title: "Monthly Encounter Volume" |
| 2 | Horizontal bar | `hospital_operations` | `organization_name` | -- | `Sum of encounter_count` | Descending, Top N = 15 | Title: "Encounters by Organization" |
| 3 | Stacked bar or Donut | `hospital_operations` | `encounter_class` | `encounter_class` | `Sum of encounter_count` | Descending | Title: "Encounter Class Distribution" |
| 4 | Horizontal bar | `hospital_operations` | `organization_name` | -- | `Average of average_duration_minutes` | Descending, Top N = 15 | Title: "Average Duration by Organization" |
| 5 | Line chart | `hospital_operations` | `Date[YearMonth]` | -- | `DIVIDE(SUM(emergency_count), SUM(encounter_count))` (new measure `Emergency Utilization %`) | chronological | Title: "Emergency Utilization Trend" |
| 6 | Matrix (heatmap via conditional formatting) | `hospital_operations` | Rows: `organization_name`, Columns: `encounter_class` | -- | `Sum of encounter_count` | Rows sorted by row total, descending | Title: "Organization x Encounter Class". Apply **Conditional formatting -> Background color** on the value field for the heatmap effect (use the theme's sequential blue scale) |

**Slicers:** Date range, Organization, Encounter Class.

**Formatting:** counts -> whole number; percentages -> `0.0%`;
duration -> `#,##0 "min"`.

---

## Page 4 -- Financial Performance

**Required text box** (near the title): *"All financial figures are
derived from Synthea-generated synthetic data and do not represent
real hospital financial performance."*

**Cards** (5): Total Claim Cost `[Total Claim Cost]`, Payer Coverage
`[Total Payer Coverage]`, Patient Responsibility `[Patient
Responsibility]`, Cost per Encounter `[Cost per Encounter]`, Coverage
Ratio `[Coverage Ratio]`.

**Visuals:**

| # | Visual type | Table/Fields | Axis | Legend | Values | Sort | Notes |
|---|---|---|---|---|---|---|---|
| 1 | Line chart | `financial_analysis` | `Date[YearMonth]` | -- | `Sum of total_claim_cost` | chronological | Title: "Monthly Claim Cost Trend"; $ format |
| 2 | Line chart | `financial_analysis` | `Date[YearMonth]` | -- | `Sum of total_payer_coverage` | chronological | Title: "Payer Coverage Trend" |
| 3 | Line chart | `financial_analysis` | `Date[YearMonth]` | -- | `Sum of total_patient_responsibility` | chronological | Title: "Patient Responsibility Trend" |
| 4 | Horizontal bar | `hospital_operations` | `encounter_class` | -- | `Sum of total_claim_cost` | Descending | Title: "Cost by Encounter Class" (uses `hospital_operations`, the only table carrying both cost and encounter class together) |
| 5 | Horizontal bar | `financial_analysis` | `organization_name` | -- | `Sum of total_claim_cost` | Descending, Top N = 15 | Title: "Cost by Organization" |
| 6 | Horizontal bar | `financial_analysis` | `payer_name` | -- | `Average of coverage_ratio` | Descending | Title: "Coverage Ratio by Payer"; Percentage format |

**Slicers:** Date range, Organization, Payer.

**Formatting:** all cost fields -> `$#,##0`; `Coverage Ratio` -> `0.0%`.

---

## Page 5 -- Provider Performance

**Language rule (enforced on every title/label on this page):** use
"encounter volume," "patient volume," "utilization" -- never "top
performer," "best," "worst," or any quality judgment. Volume reflects
activity, not care quality.

**Cards** (5): Active Providers `[Active Providers]`, Provider
Encounters `[Provider Encounters]`, Encounters per Provider
`[Encounters per Provider]`, Patients per Provider `[Unique Patients
per Provider]`, Average Duration `[Average Encounter Duration]`.

**Visuals:**

| # | Visual type | Table/Fields | Axis | Legend | Values | Sort | Notes |
|---|---|---|---|---|---|---|---|
| 1 | Horizontal bar | `provider_performance` | `provider_name` | -- | `Sum of encounter_count` | Descending, Top N = 10 | Title: "Providers by Encounter Volume" (not "Top Providers") |
| 2 | Horizontal bar | `provider_performance` | `provider_name` | -- | `Sum of unique_patients` | Descending, Top N = 10 | Title: "Providers by Patient Volume" |
| 3 | Line chart | `provider_performance` | `Date[YearMonth]` | -- | `Sum of encounter_count` | chronological | Title: "Provider Utilization Trend" (whole-population trend; add a provider slicer for per-provider drill-down) |
| 4 | Bar or Donut | `provider_performance` | `speciality` | `speciality` | `Distinct count of provider_key` | Descending | Title: "Speciality Distribution" |
| 5 | Horizontal bar | `provider_performance` | `provider_name` | -- | `Average of average_encounter_duration_minutes` | Descending, Top N = 15 | Title: "Average Duration by Provider" |
| 6 | Horizontal bar | `provider_performance` | `provider_name` | -- | `Sum of total_claim_cost` | Descending, Top N = 15 | Title: "Claim Cost by Provider" |
| 7 (table) | Table | `provider_performance` | Rows: `provider_name`, `speciality` | -- | `[Provider Encounters]`, `[Unique Patients per Provider]` *(row-level, not the averaged measure -- use `Sum of unique_patients` directly)*, `[Average Encounter Duration]`, `Sum of total_claim_cost` | Sort by encounters, descending | Title: "Provider Ranking" -- this is the required interactive ranking table; every column is independently sortable by clicking its header in Power BI at view time |

**Slicers:** Date range, Speciality, Provider (single-select, for the
utilization trend drill-down).

**Formatting:** counts -> whole number; duration -> `#,##0 "min"`;
cost -> `$#,##0`.

---

## Page 6 -- Patient Population

**Cards** (6): Patient Count `[Patient Count]`, Average Age `[Average
Patient Age (Estimated)]` *(label the card "Average Age (Estimated)")*,
Deceased Patients `[Deceased Patient Count]` *(displays "N/A" -- see
`dax_measures.md`)*, Encounters per Patient `[Encounters per Patient]`,
Conditions per Patient `[Conditions per Patient]`, Medications per
Patient `[Medications per Patient]`.

**Visuals:**

| # | Visual type | Table/Fields | Axis | Legend | Values | Sort | Notes |
|---|---|---|---|---|---|---|---|
| 1 | Bar chart | `patient_population` | `age_group` | -- | `Count of patient_key` | Custom age-group order (see Page 1 note) | Title: "Age-Group Distribution" |
| 2 | Donut or Bar | `patient_population` | `gender` | `gender` | `Count of patient_key` | Descending | Title: "Gender Distribution" |
| 3 | Bar chart | `patient_population` | `race` | -- | `Count of patient_key` | Descending | Title: "Race Distribution" |
| 4 | Bar chart | `patient_population` | `ethnicity` | -- | `Count of patient_key` | Descending | Title: "Ethnicity Distribution" |
| 5 | Bar chart or Filled Map | `patient_population` | `state` (+ `county` as a drill-down level) | -- | `Count of patient_key` | Descending | Title: "Geographic Distribution (State / County)". **State/county only -- never exact address, latitude, or longitude** (not present in the export at all). If using a Map/Filled Map visual, set Location = `state` only; do not attempt to geocode county-level points |
| 6 | Histogram | `patient_population` | `distinct_encounter_count` (binned) | -- | `Count of patient_key` | X ascending | Title: "Encounters per Patient" |
| 7 | Histogram | `patient_population` | `condition_count` (binned) | -- | `Count of patient_key` | X ascending | Title: "Conditions per Patient" |
| 8 | Histogram | `patient_population` | `medication_count` (binned) | -- | `Count of patient_key` | X ascending | Title: "Medications per Patient" |

**Slicers:** Age Group, Gender, Race, State.

**Formatting:** all counts -> whole number; `Average Patient Age
(Estimated)` -> `#,##0.0 " yrs (est.)"`.

---

## Page 7 -- Data Quality / Pipeline Health

**Table used:** `data_quality_status` (standalone -- no relationships
to the rest of the model; see `model_relationships.md`).

**Cards** (2, top row): "Last Pipeline Run" and "Last Successful
Pipeline Run" -- these are text values, not numeric measures. Use a
**Card** visual with:

```dax
Last Pipeline Run =
CALCULATE(MAX(data_quality_status[last_updated]))

Last Successful Pipeline Run =
CALCULATE(
    MAX(data_quality_status[last_updated]),
    data_quality_status[status] = "healthy"
)
```

**Visuals:**

| # | Visual type | Table/Fields | Axis | Legend | Values | Sort | Notes |
|---|---|---|---|---|---|---|---|
| 1 | Table | `data_quality_status` | Rows: `layer`, `check_type` | -- | `passed`, `warnings`, `failed`, `skipped`, `status`, `last_updated` | Sort by `layer` in pipeline order (Bronze -> Silver -> Gold -> PostgreSQL -> dbt -> Airflow) -- add a `Sort Order` calculated column since `layer` won't sort correctly alphabetically | Title: "Pipeline Stage Status". Apply **conditional formatting** on `status`: green background for `healthy`, amber for `warning`, red for `failed`, gray for `unavailable` |
| 2 | Stacked bar chart | `data_quality_status` | `layer` | -- | `passed`, `warnings`, `failed`, `skipped` (all four as separate Values, stacked) | Same pipeline-order sort | Title: "Checks Passed / Warnings / Failed / Skipped by Stage" -- this is the required visualization of all four states together |
| 3 | KPI/Status cards (one per layer, 8 small cards) | `data_quality_status` filtered per row | -- | -- | `status` | -- | Optional but recommended: one small colored status chip per pipeline stage for a quick visual scan, mirroring the Streamlit dashboard's Page 7 layout |

**No slicers needed** -- this page intentionally shows the whole
pipeline at once, unfiltered by the rest of the report's slicers (do
**not** sync the Date/Organization slicers onto this page).

**Known, honest finding to display as-is (do not hide):** the `Silver
Data Quality` row currently shows `status = failed` (1 genuine failed
check). Do not filter it out or reformat it away -- this page exists to
demonstrate real pipeline maturity, not a sanitized version of it.
