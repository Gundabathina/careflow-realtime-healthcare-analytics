# Power BI Data Model -- Relationships (Phase 5B)

## The honest answer first

The six CareFlow exports are **pre-aggregated, denormalized marts**,
not raw fact/dimension pairs -- each one already carries the
organization/provider/payer *names* directly on its rows (there is no
separate `dim_organization`/`dim_provider`/`dim_payer` export in this
phase). That has a direct consequence for the model:

- **A true star schema exists for exactly two things in this dataset:
  time, and patients.** Every table with a date/month column can relate
  to one shared Date table; `patient_population` (one row per patient)
  can relate to `readmission_analysis` (many rows per patient) on
  `patient_key`.
- **There is no clean, correct relationship between the other tables.**
  `hospital_operations` and `financial_analysis` both carry
  `organization_key`, but at incompatible grains (org x date x class vs.
  org x payer x month) -- relating them directly on `organization_key`
  would be a many-to-many relationship between two "many" tables, which
  Power BI either blocks as ambiguous or requires enabling
  many-to-many/bidirectional filtering for, and the brief explicitly
  says to avoid that. **Do not build it.** The honest fix is either (a)
  treat them as independent marts, cross-filtered only by a *synced
  slicer* on organization name (not a formal relationship), or (b) in a
  later phase, export dedicated `dim_organization_reporting.csv` /
  `dim_provider_reporting.csv` / `dim_payer_reporting.csv` (one row per
  key, straight from the warehouse) and relate every fact-grain table to
  those on `*_key` instead. Recommendation for **this** build: option
  (a) -- ship what's accurate now, note (b) as the natural next
  iteration.
- `patient_population`, `hospital_operations`, `financial_analysis`,
  `provider_performance`, and `executive_monthly` have **no shared key
  with each other** below the Date table (none of them carry
  `patient_key` except `patient_population`/`readmission_analysis`).
  There is nothing to relate there -- don't invent a join.

## Tables to import

| Table | Grain | Row count | Role |
|---|---|---|---|
| `Date` | 1 row per calendar day | ~30,000 (generated) | Shared date dimension (mark as **Date Table**) |
| `executive_monthly` | 1 row per month | 441 | Hospital-wide monthly mart |
| `financial_analysis` | 1 row per payer x org x month | 2,192 | Financial mart |
| `hospital_operations` | 1 row per org x date x encounter class | 3,112 | Operations mart |
| `provider_performance` | 1 row per provider x month | 2,167 | Provider mart |
| `patient_population` | 1 row per patient | 58 | Patient dimension-like mart (unique `patient_key`) |
| `readmission_analysis` | 1 row per qualifying index encounter | 207 | Readmission mart |
| `data_quality_status` | 1 row per pipeline layer | 8 | Standalone -- no relationships (Page 7 only) |

## Relationships to build

| From Table | From Column | To Table | To Column | Cardinality | Filter Direction | Active? | Reason |
|---|---|---|---|---|---|---|---|
| `Date` | `YearMonth` | `executive_monthly` | `year_month` | 1:many | Single (Date -> executive_monthly) | Yes | Month-grain mart; text-match on the shared `YYYY-MM` format since there's no numeric month key exported |
| `Date` | `YearMonth` | `financial_analysis` | `year_month` | 1:many | Single (Date -> financial_analysis) | Yes | Same pattern; enables date-range slicers to filter Financial visuals |
| `Date` | `YearMonth` | `provider_performance` | `year_month` | 1:many | Single (Date -> provider_performance) | Yes | Same pattern; enables date-range slicers to filter Provider visuals |
| `Date` | `Date` | `hospital_operations` | `encounter_date` | 1:many | Single (Date -> hospital_operations) | Yes | This table has an actual date column (daily grain), so relate on `Date`, not `YearMonth` |
| `patient_population` | `patient_key` | `readmission_analysis` | `patient_key` | 1:many | Single (patient_population -> readmission_analysis) | Yes | `patient_key` is unique in `patient_population` (verified, 0 duplicates) -- the only genuine dimension-to-fact relationship in this dataset |

**Every relationship above is single-direction (Date/patient filters
flow down into the marts, never back up)** -- this is deliberate.
Bidirectional filtering here would let a Financial Performance slicer
selection silently affect Provider Performance visuals through the
shared Date table in ways that are easy to build correctly but easy to
misread; single-direction keeps each page's filtering behavior obvious.

## Relationships deliberately NOT built (and why)

| Candidate relationship | Why it's not built |
|---|---|
| `hospital_operations[organization_key]` <-> `financial_analysis[organization_key]` | Both are "many" tables at incompatible grains -- a direct relationship would be many-to-many. Use a synced slicer on `organization_name` across both pages instead (Power BI: **Sync Slicers** pane), or introduce a real `dim_organization` export in a later phase and relate both to it. |
| `provider_performance[provider_key]` <-> `hospital_operations` (any column) | No shared key exists -- `hospital_operations` doesn't carry `provider_key`, only a `provider_count`. Don't force a join through `organization_key` either; that would silently fan out provider rows across every organization's operations rows. |
| `financial_analysis[payer_key]` <-> any other table | Only `financial_analysis` carries payer information in this export set. No relationship to build. |
| `patient_population` <-> `hospital_operations` / `financial_analysis` / `provider_performance` / `executive_monthly` | None of those four carry `patient_key` -- they're pre-aggregated above the patient grain. No relationship exists to build. |
| `executive_monthly` <-> `financial_analysis` / `provider_performance` (on `year_month` directly, table-to-table) | Relate each to the shared `Date` table instead of each other -- chaining fact tables directly on a text key instead of through one shared date dimension is exactly the kind of "looks like a relationship but isn't a real dimension" pattern this brief asks to avoid. |

## The Date table

Not exported as a CSV -- build it in Power BI with a calculated table
(DAX) so it always spans the full range present in the data:

```dax
Date =
VAR MinDate = DATE(1943, 9, 1)   -- matches executive_monthly's earliest year_month
VAR MaxDate = DATE(2026, 12, 31) -- comfortably past the latest data (2026-07)
RETURN
ADDCOLUMNS(
    CALENDAR(MinDate, MaxDate),
    "Year", YEAR([Date]),
    "Quarter", "Q" & FORMAT([Date], "Q"),
    "Month Number", MONTH([Date]),
    "Month Name", FORMAT([Date], "MMMM"),
    "YearMonth", FORMAT([Date], "YYYY-MM"),
    "Week", WEEKNUM([Date]),
    "Day", DAY([Date]),
    "Day Name", FORMAT([Date], "dddd")
)
```

After creating it:

1. **Model view -> right-click `Date` table -> Mark as Date Table ->**
   select the `Date` column.
2. Sort `Month Name` by `Month Number` (Column tool -> **Sort by
   Column**) so month names sort chronologically, not alphabetically,
   in every visual.
3. Build the five relationships listed above from this table.

**Recommended default filter:** because `executive_monthly` genuinely
spans 1943-2026 (see `data_dictionary.md`), set a **default relative
date filter** (or a bookmarked slicer state) of "last 36 months" on the
Executive Overview and Financial Performance pages so the report opens
on a readable, recent window -- with the full range still available by
clearing the slicer. Document this default in `page_build_guide.md`.
