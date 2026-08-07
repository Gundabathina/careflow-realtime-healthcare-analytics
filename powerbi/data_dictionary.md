# Data Dictionary -- Power BI Source Exports (Phase 5B)

Every field in every file in `data/exports/powerbi/`. All seven files
are generated directly from `careflow_dbt_mart` (the dbt reporting
layer, Phase 3C) -- never from Raw, Bronze, or Silver data. "Safe for
reporting" is `Yes` for every field below; none carry restricted PII
(verified in `tests/test_powerbi_exports.py`).

**CSV integer-with-nulls note:** any column with an `int64 (as float in
CSV)` type below contains whole numbers but was written as a decimal in
the CSV (e.g. `935105882606332028.0`) because pandas upcasts an integer
column to float the moment it contains any null. In Power Query, change
these columns' type to **Whole Number** (Power BI will parse the `.0`
correctly) rather than leaving them as Decimal Number.

---

## 1. `executive_monthly.csv`

**Grain:** one row per calendar month (`year_month`), hospital-wide.
**Row count:** 441. **Date range:** 1943-09 to 2026-07 (see README --
genuine, from Synthea's full patient life histories; every row has
`total_encounters > 0`).

| Column | Type | Meaning | Example | Safe |
|---|---|---|---|---|
| `year_month` | text (`YYYY-MM`) | Calendar month | `"2026-01"` | Yes |
| `total_patients_served` | integer | Distinct patients with an encounter that month | `58` | Yes |
| `total_encounters` | integer | Total encounters that month | `120` | Yes |
| `inpatient_encounters` | integer | Encounters flagged inpatient | `4` | Yes |
| `emergency_encounters` | integer | Encounters flagged emergency | `9` | Yes |
| `average_length_of_stay_minutes` | decimal | Mean encounter duration, minutes | `346.79` | Yes |
| `total_claim_cost` | decimal (USD) | Sum of claim cost | `7904354.16` (file total) | Yes |
| `total_payer_coverage` | decimal (USD) | Sum of payer-covered amount | `6148883.54` (file total) | Yes |
| `total_patient_responsibility` | decimal (USD) | Sum of patient-owed amount (negative-flagged rows excluded upstream) | `250.00` | Yes |
| `readmission_count_30_day` | integer | Qualifying encounters discharged that month with a readmission within 30 days | `0` | Yes |
| `readmission_rate_30_day` | decimal (0-1) | `readmission_count_30_day / qualifying encounters that month`; **null when zero qualifying encounters** (288/441 months -- mostly the sparse pre-2020 months) | `0.0145` or blank | Yes |

**Example use:** Executive Overview KPI cards and trend lines (Total
Encounters, Total Claim Cost, 30-Day Readmission Rate by month).

---

## 2. `readmission_analysis.csv`

**Grain:** one row per qualifying index encounter (`index_encounter_key`).
**Row count:** 207.

| Column | Type | Meaning | Example | Safe |
|---|---|---|---|---|
| `patient_key` | integer | Surrogate patient key (no name/SSN attached) | `837823970529111356` | Yes |
| `age_group` | text | Patient age bucket at the time | `"80+"` | Yes |
| `gender` | text | `M`/`F` | `"F"` | Yes |
| `index_encounter_key` | integer | The qualifying (inpatient/emergency) encounter | `935105882606332028` | Yes |
| `index_encounter_class` | text | `inpatient` or `emergency` | `"inpatient"` | Yes |
| `index_discharge_timestamp` | datetime (UTC) | When the index encounter ended | `"1951-05-20 00:04:14+00:00"` | Yes |
| `next_encounter_key` | integer (as float in CSV) | The next qualifying encounter after the index, **regardless of how far in the future** -- null if the patient was never readmitted | `609193661906047900.0` | Yes |
| `next_encounter_class` | text | Class of that next qualifying encounter | `"inpatient"` | Yes |
| `next_encounter_timestamp` | datetime (UTC) | When that next encounter started | `"1990-07-25 23:08:16+00:00"` | Yes |
| `days_to_readmission` | decimal | Days between index discharge and the next qualifying encounter -- **open-ended, not capped at 30 days**; see methodology note below | `14311.96` | Yes |
| `readmitted_within_7_days` | boolean | `days_to_readmission <= 7` | `False` | Yes |
| `readmitted_within_14_days` | boolean | `days_to_readmission <= 14` | `False` | Yes |
| `readmitted_within_30_days` | boolean | `days_to_readmission <= 30` -- **this is the actual readmission determination**, not `days_to_readmission` alone | `False` | Yes |

**Methodology note (important for the readmission page's build):**
`next_encounter_*`/`days_to_readmission` capture the patient's *next*
qualifying encounter after the index, whenever it occurs -- a value of
14,311 days does **not** mean a "39-year readmission"; it means this
patient's next inpatient/emergency encounter, whatever it was, happened
that far out, so none of the three `readmitted_within_*_days` flags are
true for that row. Always filter/aggregate on the boolean flags for
readmission-rate calculations, never on `days_to_readmission` directly.
`days_to_readmission`/`next_encounter_*` are null (40-45 rows) for
patients who were never readmitted at all.

**Example use:** Readmission Analytics page -- rates by class/age/gender,
days-to-readmission distribution (filtered to `readmitted_within_30_days = TRUE`
for a meaningful histogram; see `page_build_guide.md`).

---

## 3. `financial_analysis.csv`

**Grain:** one row per payer x organization x month.
**Row count:** 2,192.

| Column | Type | Meaning | Example | Safe |
|---|---|---|---|---|
| `payer_key` | integer | Surrogate payer key | `38194544675647776` | Yes |
| `payer_name` | text | Payer/insurer name | `"Medicaid"` | Yes |
| `organization_key` | integer | Surrogate organization key | `164982158369714689` | Yes |
| `organization_name` | text | Organization (business) name | `"A&A HEALTHCARE LLC"` | Yes |
| `year_month` | text (`YYYY-MM`) | Calendar month | `"2026-01"` | Yes |
| `encounter_count` | integer | Encounters in this payer/org/month | `2` | Yes |
| `total_claim_cost` | decimal (USD) | Sum of claim cost | `1999.20` | Yes |
| `total_payer_coverage` | decimal (USD) | Sum of payer-covered amount | `1899.20` | Yes |
| `total_patient_responsibility` | decimal (USD) | Sum of patient-owed amount | `100.00` | Yes |
| `average_claim_cost` | decimal (USD) | `total_claim_cost / encounter_count` | `999.60` | Yes |
| `coverage_ratio` | decimal (0-1) | `total_payer_coverage / total_claim_cost` | `0.9500` | Yes |

**Example use:** Financial Performance page -- cost/coverage trends,
cost by organization, coverage ratio by payer.

---

## 4. `hospital_operations.csv`

**Grain:** one row per organization x encounter date x encounter class.
**Row count:** 3,112 (of which 18 are zero-activity organizations, see below).

| Column | Type | Meaning | Example | Safe |
|---|---|---|---|---|
| `organization_key` | integer | Surrogate organization key | `164982158369714689` | Yes |
| `organization_name` | text | Organization name | `"A&A HEALTHCARE LLC"` | Yes |
| `encounter_date_key` | integer (as float in CSV) | `YYYYMMDD` integer date key; **null for the 18 zero-activity organizations** | `19430915.0` | Yes |
| `encounter_date` | date | Calendar date | `"1943-09-15"` | Yes |
| `encounter_class` | text | e.g. `inpatient`, `emergency`, `ambulatory`; **null for zero-activity organizations** | `"wellness"` | Yes |
| `encounter_count` | integer | Encounters that org/date/class | `2` | Yes |
| `unique_patients` | integer | Distinct patients | `2` | Yes |
| `average_duration_minutes` | decimal | Mean encounter duration | `52.30` | Yes |
| `median_duration_minutes` | decimal | Median encounter duration | `52.30` | Yes |
| `inpatient_count` | integer | Of `encounter_count`, how many inpatient | `0` | Yes |
| `emergency_count` | integer | Of `encounter_count`, how many emergency | `0` | Yes |
| `total_claim_cost` | decimal (USD) | Sum of claim cost | `1999.20` | Yes |
| `payer_coverage` | decimal (USD) | Sum of payer-covered amount | `1899.20` | Yes |
| `patient_responsibility` | decimal (USD) | Sum of patient-owed amount | `100.00` | Yes |
| `provider_count` | integer | Distinct providers involved | `1` | Yes |

**18 zero-activity rows:** organizations present in the warehouse's
organization dimension with **zero recorded encounters** in the
synthetic dataset (`provider_count = 0`, `encounter_class`/`encounter_date`
null). Included via the underlying dbt mart's outer join, for
dimensional completeness -- not missing/corrupt data. In Power BI,
visuals grouped by `encounter_class` will naturally exclude these (null
group); a table of "organizations with zero encounters" is itself a
valid, intentional use of these 18 rows if wanted.

**Example use:** Hospital Operations page -- volume trends, duration by
organization, organization x class heatmap.

---

## 5. `provider_performance.csv`

**Grain:** one row per provider x month.
**Row count:** 2,167 (of which 18 are zero-activity providers, see below).

| Column | Type | Meaning | Example | Safe |
|---|---|---|---|---|
| `provider_key` | integer | Surrogate provider key | `280079293355470746` | Yes |
| `provider_name` | text | Provider (clinician) name -- a **business/operational** identifier, not patient PII; expected on a provider performance page | `"Wynell591 Mayert710"` | Yes |
| `speciality` | text | Clinical speciality | `"GENERAL PRACTICE"` | Yes |
| `year_month` | text (`YYYY-MM`); **null for the 18 zero-activity providers** | Calendar month | `"2026-01"` | Yes |
| `encounter_count` | integer | Encounters that provider/month | `1` | Yes |
| `unique_patients` | integer | Distinct patients seen | `1` | Yes |
| `procedure_count` | integer | Procedures performed | `0` | Yes |
| `average_encounter_duration_minutes` | decimal | Mean encounter duration | `41.03` | Yes |
| `total_claim_cost` | decimal (USD) | Sum of claim cost for this provider/month | `1329.55` | Yes |

**18 zero-activity rows:** providers with zero recorded encounters
across the entire dataset (`year_month` null, all counts 0) -- same
outer-join-for-completeness pattern as `hospital_operations.csv`, and
likely the staff of record for the same 18 zero-activity organizations.

**Example use:** Provider Performance page -- top providers, utilization
trend, speciality distribution.

**Note on provider names:** displayed as-is (they are synthetic, Synthea-generated
names, not real clinicians) -- this is standard for a "Provider
Performance" page and distinct from the project's *patient* PII rules.

---

## 6. `patient_population.csv`

**Grain:** one row per patient (`patient_key`).
**Row count:** 58.

| Column | Type | Meaning | Example | Safe |
|---|---|---|---|---|
| `patient_key` | integer | Surrogate patient key -- no name/SSN attached | `81526021757495383` | Yes |
| `age_group` | text; 1 null row | Age bucket (`0-17`, `18-34`, `35-49`, `50-64`, `65-79`, `80+`) | `"50-64"` | Yes |
| `gender` | text | `M`/`F` | `"M"` | Yes |
| `race` | text | Race category | `"white"` | Yes |
| `ethnicity` | text | Ethnicity category | `"nonhispanic"` | Yes |
| `marital_status` | text; 23 null rows (unmarried/unspecified, common for minors in Synthea) | Marital status code | `"M"` | Yes |
| `state` | text | State (safe -- synthetic data) | `"Massachusetts"` | Yes |
| `county` | text | County (safe -- synthetic data) | `"Plymouth County"` | Yes |
| `condition_count` | integer | Total recorded conditions | `31` | Yes |
| `active_condition_count` | integer | Currently active conditions | `12` | Yes |
| `procedure_count` | integer | Total recorded procedures | `75` | Yes |
| `medication_count` | integer | Total recorded medications | `20` | Yes |
| `observation_count` | integer | Total recorded observations | `450` | Yes |
| `immunization_count` | integer | Total recorded immunizations | `15` | Yes |
| `distinct_encounter_count` | integer | Distinct encounters | `34` | Yes |

**No exact age, birth date, name, SSN, passport, driver's license,
street address, or lat/long are present** -- `mart_patient_population`
intentionally exposes only the safe fields above (Phase 3C's PII rules).
Average Age on the Power BI report is therefore an **estimate** from
`age_group` midpoints (same approach as the Streamlit dashboard's
`AGE_GROUP_MIDPOINTS`) -- see `dax_measures.md`. **Deceased Patient
Count is not available** at this layer for the same reason (no
`is_deceased` column in the public mart) -- the measure/card should
display "N/A" rather than a fabricated 0.

**Example use:** Patient Population page -- demographic composition.

---

## 7. `data_quality_status.csv` (new in Phase 5B)

**Grain:** one row per pipeline layer/stage.
**Row count:** 8. **Generated from:** the existing Bronze/Silver/Gold
manifests, PostgreSQL validation report, dbt test summary, and Airflow
run summary already written by every prior phase -- via the same
report-loading logic as the Streamlit dashboard's Data Quality page
(`dashboard/reports.py`). Never a re-run of any check, never a
fabricated number.

| Column | Type | Meaning | Example | Safe |
|---|---|---|---|---|
| `layer` | text | Pipeline stage name | `"Bronze Ingestion"` | Yes |
| `check_type` | text | What kind of report this stage's row summarizes (`ingestion_manifest`, `transformation_manifest`, `quality_report`, `warehouse_validation`, `test_suite`, `orchestration_run`) | `"quality_report"` | Yes |
| `passed` | integer | Checks passed (Bronze/Silver/Gold transformation rows use "successfully processed" as the equivalent of passed; Airflow uses 1 if the run succeeded) | `172` | Yes |
| `warnings` | integer | Checks that passed with a warning | `1` | Yes |
| `failed` | integer | Checks that failed | `0` or `1` | Yes |
| `skipped` | integer | Checks/datasets skipped (e.g. unchanged-checksum incremental skips) | `18` | Yes |
| `status` | text | `healthy`, `warning`, `failed`, or `unavailable` -- derived from the counts above | `"healthy"` | Yes |
| `last_updated` | text | Timestamp the underlying report was generated (format varies slightly by source report -- ISO 8601 for most, Python `str(datetime)` for Airflow's; both parse correctly in Power Query's Date/Time type) | `"2026-08-07T03:43:24Z"` | Yes |

**Honest finding, not fabricated:** the `Silver Data Quality` row
currently shows `status = failed` (1 genuine failed check out of 229) --
a real, pre-existing condition from earlier phases. This is intentional
and should be displayed as-is; the whole point of this page is to show
real pipeline health, not a sanitized version of it.

**Example use:** Data Quality / Pipeline Health page.
