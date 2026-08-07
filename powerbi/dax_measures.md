# DAX Measures (Phase 5B)

Every measure below is ready to paste into Power BI (**Modeling -> New
Measure**, on the table noted in the heading). All division uses
`DIVIDE()` (never `/`) so a zero/blank denominator returns `BLANK()`
instead of an error or a fabricated `0`. Variables (`VAR`) are used
wherever they meaningfully improve readability.

Create a **Measures** table (a blank table with no rows, `= { }`... or
`= ROW("x", 1)` then hide the column) if you want to keep every measure
organized in one place in the Fields pane, separate from the data
tables -- optional, but recommended for large models like this one.

---

## Executive (base table: `executive_monthly`)

```dax
Total Patients = SUM(executive_monthly[total_patients_served])

Total Encounters = SUM(executive_monthly[total_encounters])

Inpatient Encounters = SUM(executive_monthly[inpatient_encounters])

Emergency Encounters = SUM(executive_monthly[emergency_encounters])

Emergency Encounter % =
DIVIDE([Emergency Encounters], [Total Encounters])

-- Average of each month's own average duration. This is a reasonable
-- summary at the monthly grain this table provides (there is no
-- encounter-level table in this model to average directly); it will
-- read correctly on a monthly trend line and as a single-period KPI
-- card, and is the same simplification the Streamlit dashboard makes
-- at this same grain.
Average Length of Stay =
AVERAGE(executive_monthly[average_length_of_stay_minutes])

Total Claim Cost = SUM(executive_monthly[total_claim_cost])

Total Payer Coverage = SUM(executive_monthly[total_payer_coverage])

Patient Responsibility = SUM(executive_monthly[total_patient_responsibility])

Coverage Ratio =
DIVIDE([Total Payer Coverage], [Total Claim Cost])

Cost per Encounter =
DIVIDE([Total Claim Cost], [Total Encounters])
```

**Formatting:** `Total Patients`/`Total Encounters`/`Inpatient
Encounters`/`Emergency Encounters` -> Whole number, thousands
separator. `Emergency Encounter %`/`Coverage Ratio` -> Percentage, 1
decimal. `Average Length of Stay` -> Decimal number, 0-1 decimals,
custom format `#,##0 "min"`. `Total Claim Cost`/`Total Payer
Coverage`/`Patient Responsibility`/`Cost per Encounter` -> Currency,
`$#,##0` (0 decimals for large totals, 2 decimals for `Cost per
Encounter`).

---

## Readmission (base table: `readmission_analysis`)

```dax
Qualifying Encounters = COUNTROWS(readmission_analysis)

7-Day Readmissions =
CALCULATE(
    COUNTROWS(readmission_analysis),
    readmission_analysis[readmitted_within_7_days] = TRUE
)

14-Day Readmissions =
CALCULATE(
    COUNTROWS(readmission_analysis),
    readmission_analysis[readmitted_within_14_days] = TRUE
)

30-Day Readmissions =
CALCULATE(
    COUNTROWS(readmission_analysis),
    readmission_analysis[readmitted_within_30_days] = TRUE
)

7-Day Readmission Rate =
DIVIDE([7-Day Readmissions], [Qualifying Encounters])

14-Day Readmission Rate =
DIVIDE([14-Day Readmissions], [Qualifying Encounters])

30-Day Readmission Rate =
DIVIDE([30-Day Readmissions], [Qualifying Encounters])

-- Filtered to actual readmissions only (readmitted_within_30_days = TRUE) --
-- days_to_readmission is otherwise open-ended (see data_dictionary.md's
-- methodology note) and would badly skew a naive AVERAGE.
Average Days to Readmission =
CALCULATE(
    AVERAGE(readmission_analysis[days_to_readmission]),
    readmission_analysis[readmitted_within_30_days] = TRUE
)

-- Monthly hospital-wide rate, pre-computed by dbt -- used for trend
-- lines (one point per month; see executive_monthly.csv), not as the
-- KPI card (use "30-Day Readmission Rate" above for that -- it's
-- recomputed independently from the readmission mart, matching the
-- project's own dbt-vs-Python-Gold reconciliation discipline).
Monthly Readmission Rate =
AVERAGE(executive_monthly[readmission_rate_30_day])
```

**Formatting:** `Qualifying Encounters`/`7/14/30-Day Readmissions` ->
Whole number. `7/14/30-Day Readmission Rate`/`Monthly Readmission Rate`
-> Percentage, 1 decimal. `Average Days to Readmission` -> Decimal
number, 1 decimal, custom format `#,##0.0 "days"`.

---

## Provider (base table: `provider_performance`)

```dax
-- Only providers with recorded activity -- excludes the 18 zero-
-- activity providers included in the export for dimensional
-- completeness (see data_dictionary.md).
Active Providers =
CALCULATE(
    DISTINCTCOUNT(provider_performance[provider_key]),
    provider_performance[encounter_count] > 0
)

Provider Encounters = SUM(provider_performance[encounter_count])

-- Row-level field for tables/matrices: drop provider_performance[unique_patients]
-- directly into a table visual grouped by provider_name. As a single
-- KPI card, this measure gives the average across active providers:
Unique Patients per Provider =
DIVIDE(SUM(provider_performance[unique_patients]), [Active Providers])

Encounters per Provider =
DIVIDE([Provider Encounters], [Active Providers])

Average Encounter Duration =
AVERAGE(provider_performance[average_encounter_duration_minutes])
```

**Formatting:** `Active Providers`/`Provider Encounters` -> Whole
number. `Unique Patients per Provider`/`Encounters per Provider` ->
Decimal, 1 decimal. `Average Encounter Duration` -> Decimal, 0-1
decimals, custom format `#,##0 "min"`.

**Language note (per the brief):** never build a measure or visual
title implying a provider is "good" or "bad" from these numbers alone
-- label everything as volume/utilization (see `page_build_guide.md`).

---

## Patient Population (base table: `patient_population`)

```dax
Patient Count = COUNTROWS(patient_population)

-- age_group midpoints, weighted by patient count -- mirrors the
-- Streamlit dashboard's AGE_GROUP_MIDPOINTS exactly. patient_population
-- never carries an exact age or birth date (Phase 3C PII rules), so
-- this is explicitly an estimate -- label it as such on every card/axis.
Average Patient Age (Estimated) =
VAR Midpoints =
    DATATABLE(
        "age_group", STRING, "midpoint", DOUBLE,
        {
            {"0-17", 8.5}, {"18-34", 26.0}, {"35-49", 42.0},
            {"50-64", 57.0}, {"65-79", 72.0}, {"80+", 85.0}
        }
    )
VAR PatientsByAgeGroup =
    SUMMARIZE(patient_population, patient_population[age_group], "PatientCount", COUNTROWS(patient_population))
VAR WeightedSum =
    SUMX(
        PatientsByAgeGroup,
        VAR CurrentGroup = [age_group]
        VAR Midpoint = MAXX(FILTER(Midpoints, [age_group] = CurrentGroup), [midpoint])
        RETURN [PatientCount] * Midpoint
    )
RETURN
    DIVIDE(WeightedSum, [Patient Count])

-- Not available: dim_patient_safe/mart_patient_population carry no
-- is_deceased column in the public mart (Phase 3C PII/scope rules).
-- Returns BLANK() (displays as "N/A" with the format string below)
-- rather than a fabricated 0 -- do not replace this with COUNTROWS of
-- anything; there is no deceased flag to count.
Deceased Patient Count = BLANK()

Encounters per Patient =
DIVIDE(SUM(patient_population[distinct_encounter_count]), [Patient Count])

Conditions per Patient =
DIVIDE(SUM(patient_population[condition_count]), [Patient Count])

Medications per Patient =
DIVIDE(SUM(patient_population[medication_count]), [Patient Count])
```

**Formatting:** `Patient Count` -> Whole number. `Average Patient Age
(Estimated)` -> Decimal, 1 decimal, custom format `#,##0.0 " yrs (est.)"`.
`Deceased Patient Count` -> custom format `"N/A"` (Format -> Custom ->
`"N/A"`) so a `BLANK()` displays as text, not an empty card.
`Encounters/Conditions/Medications per Patient` -> Decimal, 1 decimal.

---

## Time Intelligence (requires the `Date` table + relationships in `model_relationships.md`)

```dax
Previous Month Encounters =
CALCULATE([Total Encounters], DATEADD('Date'[Date], -1, MONTH))

Encounter MoM Change =
[Total Encounters] - [Previous Month Encounters]

Encounter MoM % =
DIVIDE([Encounter MoM Change], [Previous Month Encounters])

Previous Month Cost =
CALCULATE([Total Claim Cost], DATEADD('Date'[Date], -1, MONTH))

Cost MoM Change =
[Total Claim Cost] - [Previous Month Cost]

Cost MoM % =
DIVIDE([Cost MoM Change], [Previous Month Cost])

Previous Month Readmission Rate =
CALCULATE([Monthly Readmission Rate], DATEADD('Date'[Date], -1, MONTH))

Readmission Rate Change =
[Monthly Readmission Rate] - [Previous Month Readmission Rate]
```

**Formatting:** `Previous Month Encounters` -> Whole number.
`Encounter MoM Change` -> Whole number, `+#,##0;-#,##0` (signed).
`Encounter MoM %`/`Readmission Rate Change` -> Percentage, 1 decimal,
signed (`+0.0%;-0.0%`). `Previous Month Cost`/`Cost MoM Change` ->
Currency, signed. `Previous Month Readmission Rate` -> Percentage, 1 decimal.

**Why `DATEADD` works even though the marts are month-grain, not
day-grain:** `DATEADD('Date'[Date], -1, MONTH)` shifts the *filter
context* on the Date table back one month; that shifted date range then
flows through the `Date[YearMonth] -> executive_monthly[year_month]`
relationship exactly as the unshifted range does for the current-period
measures. No day-level data is required in the fact tables themselves.

---

## Measure count

**39 measures** across five groups: 11 Executive, 9 Readmission, 5
Provider, 6 Patient Population, 8 Time Intelligence.
