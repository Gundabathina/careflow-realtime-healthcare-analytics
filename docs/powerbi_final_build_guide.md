# Power BI Final Build Guide (Phase 5B)

Start-to-finish sequence for building `CareFlow_Analytics.pbix` in
Power BI Desktop on Windows, from the package prepared in `powerbi/`.
This is the single entry point -- each step links to the detailed
document that covers it.

## Prerequisites

- Power BI Desktop (Windows) -- not available in this development
  environment, hence this preparation-only phase.
- The seven CSV files in `data/exports/powerbi/` (six from Phase 5A,
  `data_quality_status.csv` new in this phase).
- `powerbi/theme.json`.

Copy the `data/exports/powerbi/` folder and `powerbi/theme.json` to the
Windows machine (USB, shared drive, OneDrive, or git if the repo is
cloned there too).

## Step 1 -- Import data

**Get Data -> Text/CSV**, once per file, for all seven CSVs. Use
**Transform Data** (Power Query) rather than Load directly, so column
types can be fixed before the model is built:

- Any column noted in `powerbi/data_dictionary.md` as "integer (as
  float in CSV)" -> change type to **Whole Number** (not Decimal).
- `year_month` columns -> leave as Text (used for the relationship to
  `Date[YearMonth]`, also Text).
- `encounter_date`, `index_discharge_timestamp`,
  `next_encounter_timestamp` -> change type to **Date** /
  **Date/Time**, respectively.
- `last_updated` (in `data_quality_status`) -> leave as Text (mixed
  formats across source reports; see `data_dictionary.md`) or split
  into a separate cleaned Date/Time column if precise sorting by it
  matters for your build.

Full column-by-column detail: **`powerbi/data_dictionary.md`**.

## Step 2 -- Build the model

Create the `Date` calculated table and the five relationships (all
single-direction, all 1:many) exactly as specified -- and deliberately
skip the relationships marked "do not build" (they'd be many-to-many or
have no real shared key).

Full detail, including the exact `Date` table DAX and the "why not" for
every skipped relationship: **`powerbi/model_relationships.md`**.

## Step 3 -- Write the measures

Paste all 39 measures in. Group by topic (Executive, Readmission,
Provider, Patient Population, Time Intelligence) using **Display
folders** (right-click a measure -> Properties -> Display folder) so
the Fields pane stays navigable.

Full DAX, with formatting notes per measure: **`powerbi/dax_measures.md`**.

## Step 4 -- Apply the theme

**View -> Themes -> Browse for themes...** -> select `theme.json`.
Confirm cards render with the blue accent color and the background
stays light/white -- if a visual shows unstyled defaults, it likely
needs the theme reapplied after that visual was added (Power BI
sometimes requires a re-apply after adding new visual types).

## Step 5 -- Build all 7 pages

Follow in order; each page lists every visual, its exact fields
(axis/legend/values), which measure to use, slicers, sort order, and
formatting -- built to be followed without guessing:

1. Executive Overview
2. Readmission Analytics (includes the required methodology text box)
3. Hospital Operations
4. Financial Performance (includes the required synthetic-data note)
5. Provider Performance (neutral-language rule)
6. Patient Population
7. Data Quality / Pipeline Health

Full detail: **`powerbi/page_build_guide.md`**.

## Step 6 -- QA and sign-off

Work through every item -- data/PII, model integrity, measure
reconciliation, formatting, interactivity, layout, and the Data Quality
page's specific checks -- before considering the report done.

Full checklist: **`powerbi/qa_checklist.md`**.

## Step 7 -- Save and capture screenshots

Save as `CareFlow_Analytics.pbix`. Capture screenshots per
`docs/screenshots/README.md`'s checklist (which already lists the
Power BI-specific items alongside the Streamlit dashboard and Airflow/dbt
ones) and add them there.

## What this phase intentionally did not do

- No `.pbix` file -- Power BI Desktop isn't available in this
  environment; fabricating one wasn't attempted.
- No changes to Bronze/Silver/Gold, PostgreSQL, dbt, Airflow, or the
  Streamlit dashboard.
- No new metrics invented for `data_quality_status.csv` -- every number
  in it traces back to an existing pipeline report via
  `dashboard/reports.py`'s report-loading logic.
- The one identified gap (no organization breakdown for readmissions,
  since `readmission_analysis.csv` doesn't carry an organization key)
  is documented as a known limitation in `powerbi/page_build_guide.md`
  rather than worked around with a fragile, fan-out-prone Power Query
  merge.
