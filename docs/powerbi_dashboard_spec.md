# Power BI Dashboard Specification (prepared in Phase 5A, built in a later phase)

This document specifies the Power BI implementation that will follow
this Streamlit dashboard -- it is a specification only. **No `.pbix`
file is created in this phase.** Source data: the CSV exports in
`data/exports/powerbi/`, generated from the same `careflow_dbt_mart`
tables the Streamlit dashboard queries live.

## Source files

| File | Source dbt model | Grain |
|---|---|---|
| `executive_monthly.csv` | `mart_executive_monthly` | 1 row per month |
| `readmission_analysis.csv` | `mart_readmission_analysis` | 1 row per index encounter |
| `financial_analysis.csv` | `mart_financial_analysis` | 1 row per payer x organization x month |
| `hospital_operations.csv` | `mart_hospital_operations` | 1 row per organization x date x encounter class |
| `provider_performance.csv` | `mart_provider_performance` | 1 row per provider x month |
| `patient_population.csv` | `mart_patient_population` | 1 row per patient |

All six are already aggregated, PII-safe outputs of the dbt layer (Phase
3C) -- the same governance and column-exclusion rules the Streamlit
dashboard relies on apply here unchanged. No file contains SSN,
passport, driver's license, first/middle/last name, street address, or
precise latitude/longitude.

## Power BI data model

Import all six CSVs as separate tables. Relationships (all 1:many from
the "one" side to the fact-like tables, on the shared key):

```
patient_population[patient_key] 1 ---- * readmission_analysis[patient_key]
executive_monthly[year_month]   1 ---- * financial_analysis[year_month]
executive_monthly[year_month]   1 ---- * hospital_operations[encounter_date]  (via a Date table, below)
executive_monthly[year_month]   1 ---- * provider_performance[year_month]
```

Add a standalone **Date** table (`CALENDAR(MIN date, MAX date)` in DAX,
or a `dim_date`-equivalent CSV export in a later phase) with a `year_month`
text column matching the `"YYYY-MM"` format already used throughout
these exports, so `executive_monthly`, `financial_analysis`, and
`provider_performance` can all relate to it on `year_month`, and
`hospital_operations` relates to it on `encounter_date`. This centralizes
time intelligence (month-over-month, YTD) in one table rather than
duplicating date logic per fact table.

`organization_name` and `payer_name` are denormalized directly onto
`financial_analysis` and `hospital_operations` (no separate dimension
file exported in this phase) -- for a cleaner star schema, a later phase
could export `dim_organization_reporting`/`dim_payer_reporting`/
`dim_provider_reporting` as their own CSVs and relate on their `*_key`
columns instead of the name.

## Measures (DAX)

```dax
Total Encounters := SUM(executive_monthly[total_encounters])
Total Patients Served := SUM(executive_monthly[total_patients_served])
Inpatient Encounters := SUM(executive_monthly[inpatient_encounters])
Emergency Encounters := SUM(executive_monthly[emergency_encounters])

30-Day Readmission Rate :=
    DIVIDE(
        SUM(executive_monthly[readmission_count_30_day]),
        SUM(executive_monthly[total_encounters]),
        BLANK()
    )

Total Claim Cost := SUM(financial_analysis[total_claim_cost])
Total Payer Coverage := SUM(financial_analysis[total_payer_coverage])
Total Patient Responsibility := SUM(financial_analysis[total_patient_responsibility])

Coverage Ratio :=
    DIVIDE([Total Payer Coverage], [Total Claim Cost], BLANK())

Average Claim Cost per Encounter :=
    DIVIDE([Total Claim Cost], [Total Encounters], BLANK())

Average Encounter Duration (Operations) :=
    AVERAGE(hospital_operations[average_duration_minutes])

Readmission Rate (7-Day) :=
    DIVIDE(
        CALCULATE(COUNTROWS(readmission_analysis), readmission_analysis[readmitted_within_7_days] = TRUE),
        COUNTROWS(readmission_analysis),
        BLANK()
    )
-- Readmission Rate (14-Day) / (30-Day): identical pattern on the
-- corresponding readmitted_within_14_days / readmitted_within_30_days column.

Encounters MoM % Change :=
    VAR CurrentMonth = [Total Encounters]
    VAR PreviousMonth = CALCULATE([Total Encounters], DATEADD('Date'[Date], -1, MONTH))
    RETURN DIVIDE(CurrentMonth - PreviousMonth, PreviousMonth, BLANK())

Average Age (Estimated) :=
    -- age_group midpoints, weighted by patient count -- mirrors
    -- dashboard/config.py's AGE_GROUP_MIDPOINTS exactly; patient_population
    -- never carries an exact age or birth date.
    VAR Midpoints =
        DATATABLE(
            "age_group", STRING, "midpoint", DOUBLE,
            {{"0-17", 8.5}, {"18-34", 26.0}, {"35-49", 42.0}, {"50-64", 57.0}, {"65-79", 72.0}, {"80+", 85.0}}
        )
    RETURN
        DIVIDE(
            SUMX(
                SUMMARIZE(patient_population, patient_population[age_group], "PatientCount", COUNTROWS(patient_population)),
                VAR grp = patient_population[age_group]
                RETURN [PatientCount] * MAXX(FILTER(Midpoints, [age_group] = grp), [midpoint])
            ),
            COUNTROWS(patient_population)
        )
```

## Page layouts and recommended visuals

Mirrors the Streamlit dashboard's seven pages so the two stay in sync
conceptually, using Power BI-native visual types:

### 1. Executive Overview
- **Card visuals** (top row): Total Patients Served, Total Encounters,
  Inpatient Encounters, Emergency Encounters, 30-Day Readmission Rate.
- **Card visuals** (second row): Average Length of Stay, Total Claim
  Cost, Coverage Ratio, Average Patient Responsibility.
- **Line chart**: Total Encounters by `year_month`.
- **Clustered bar chart**: encounters by encounter class (needs an
  encounter-class-level export in a later phase; `hospital_operations`
  already carries `encounter_class` and can substitute).
- **Line chart**: 30-Day Readmission Rate by `year_month`.
- **Line chart** (dual line): Total Claim Cost & Total Payer Coverage by `year_month`.
- **Bar chart**: Top 10 organizations by encounter count (`hospital_operations`, summarized).
- **Donut chart**: payer coverage share (`financial_analysis`, summarized by payer).

### 2. Readmission Analytics
- **Cards**: Qualifying Index Encounters, 7/14/30-Day Readmission Rate, Average Days to Readmission.
- **Line chart**: 30-Day Readmission Rate by `year_month`.
- **Bar chart**: readmission rate by `index_encounter_class`.
- **Bar chart**: readmission rate by `age_group`, by `gender`.
- **Histogram** (or binned bar chart): `days_to_readmission` distribution.
- **Table**: aggregated age-group x gender segments with readmission rate -- never patient-level rows.

### 3. Hospital Operations
- **Cards**: Total Encounters, Unique Patients, Avg/Median Duration, Emergency %, Inpatient %.
- **Line chart**: encounter volume by month.
- **Bar chart**: encounter volume by organization.
- **Matrix/heatmap visual**: organization x encounter class.
- **Table**: organization comparison (encounters, patients, duration, cost).

### 4. Financial Performance
- **Cards**: Total Claim Cost, Total Payer Coverage, Patient Responsibility, Coverage Ratio.
- **Line charts**: monthly claim cost, payer coverage over time, patient responsibility trend.
- **Bar charts**: cost by organization, coverage ratio by payer, top payers by coverage.

### 5. Provider Performance
- **Cards**: Active Providers, Total Provider Encounters, Avg Encounters/Provider, Avg Duration.
- **Bar charts**: top providers by encounters, by unique patients, by claim cost.
- **Line chart**: provider utilization over time.
- **Table**: full provider ranking (sortable).

### 6. Patient Population
- **Cards**: Patient Count, Average Age (Estimated), Avg Encounters/Conditions/Medications per patient.
- **Bar/donut charts**: age group, gender, race, ethnicity distribution.
- **Filled map or bar chart**: state/county distribution (state/county only -- never exact address or coordinates).

### 7. Data Quality
- Out of scope for the CSV-driven Power BI model in this phase (it reads
  JSON pipeline reports, not tabular mart data) -- revisit if/when a
  pipeline-metrics table is exported specifically for BI consumption.

## Filter design (Power BI slicers)

- Date range slicer bound to the Date table.
- Organization, Provider, Payer slicers (from their respective name
  columns, or a future dedicated dimension export).
- Encounter class slicer.
- Age group / Gender / Race slicers (patient-level pages).
- Readmission window: implemented as a **field parameter** or
  bookmark-driven measure switch between the three
  `Readmission Rate (7/14/30-Day)` measures, since Power BI has no
  native "choose which measure" slicer without one of those two patterns.

## KPI definitions (must match the Streamlit dashboard and dbt layer exactly)

| KPI | Definition |
|---|---|
| 30-Day Readmission Rate | Qualifying inpatient/emergency encounters with a next qualifying encounter starting within 30 days of discharge, divided by all qualifying encounters. |
| Coverage Ratio | Total payer coverage / total claim cost. |
| Average Length of Stay | Average `encounter_duration_minutes` across encounters. |
| Emergency / Inpatient % | Share of encounters flagged `is_emergency` / `is_inpatient`. |
| Average Age (Estimated) | Age-group midpoint, weighted by patient count -- never an exact age. |

These mirror `dashboard/queries.py` and the dbt marts' own documented
business definitions (Phase 3C's `schema.yml`) exactly -- the Power BI
model must not redefine any of them differently.
