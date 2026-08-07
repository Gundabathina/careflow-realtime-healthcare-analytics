# Power BI QA Checklist (Phase 5B)

Run through every item before calling the `.pbix` finished. Check
items off as you verify them in Power BI Desktop -- most items name the
exact place to look.

## Data & PII

- [ ] **No PII.** Open every table in Data view and confirm no column
  named/containing `ssn`, `passport`, `drivers_license`,
  `first_name`/`middle_name`/`last_name` (patient), `street_address`,
  `latitude`, `longitude`. (Provider names are expected and fine --
  they're operational, not patient PII; see `data_dictionary.md`.)
- [ ] **Synthetic-data disclaimer is visible.** Financial Performance
  page has the required text box; every page ideally carries a small
  footer note that data is synthetic (mirrors the Streamlit dashboard's
  `render_synthetic_data_notice()` on every page).
- [ ] **No restricted PII in any tooltip, table, or export.** Right-click
  a table visual -> Export data -> confirm the exported columns match
  what's documented as safe in `data_dictionary.md`.

## Model

- [ ] **No broken relationships.** Model view: every relationship line
  from `model_relationships.md` is present, active (solid, not dashed
  unless intentionally inactive -- there are none inactive in this
  model), and shows the correct 1:many cardinality arrow.
- [ ] **No unintended many-to-many relationships.** Confirm
  `hospital_operations` and `financial_analysis` are **not** directly
  related to each other (see `model_relationships.md` for why).
- [ ] **No ambiguous filter paths.** Power BI will warn at relationship-
  creation time if a path is ambiguous -- if you see that warning,
  stop and re-check against `model_relationships.md` rather than
  forcing it through.
- [ ] **Date table is marked correctly.** Model view -> `Date` table has
  the calendar icon (Mark as Date Table, on the `Date` column).
- [ ] **`Month Name` sorts chronologically**, not alphabetically, in
  every visual that uses it (Column tools -> Sort by Column ->
  `Month Number`).
- [ ] **`age_group` sorts in clinical order** (`0-17` ... `80+`), not
  alphabetically, everywhere it's used as an axis/legend.

## Measures

- [ ] **All 39 measures from `dax_measures.md` exist** in the model
  (Fields pane) with matching names and formatting.
- [ ] **Every division uses `DIVIDE()`.** Spot-check 3-4 measures in the
  formula bar -- no bare `/` operators.
- [ ] **Measure totals reconcile.** Compare `[Total Claim Cost]` and
  `[Total Payer Coverage]` (unfiltered) against the values printed in
  this phase's audit (`$7,904,354.16` / `$6,148,883.54`) and against
  `docs/dashboard_guide.md`'s live Streamlit KPI values -- they must
  match to the cent.
- [ ] **Readmission rates reconcile.** `[30-Day Readmission Rate]`
  (unfiltered) must equal `3 / 207 = 1.4%`, matching the dbt
  reconciliation test (`reconcile_readmission_counts_with_python_gold`)
  and the live Streamlit dashboard's Readmission Analytics page.
- [ ] **Financial totals reconcile per page.** Sum of `total_claim_cost`
  on the Financial Performance page's "Cost by Organization" bar chart
  (all bars, unfiltered) equals the `[Total Claim Cost]` KPI card.

## Formatting

- [ ] **Currency formatting** is consistent (`$#,##0`, 0 decimals for
  totals, 2 decimals for per-encounter averages) on every card, axis,
  and table column showing a dollar figure.
- [ ] **Percentage formatting** is consistent (`0.0%`, 1 decimal) on
  every rate/ratio measure.
- [ ] **Duration formatting** shows units (`#,##0 "min"`), never a bare
  number that could be misread as something else.
- [ ] **No default Power BI "Sum of columnname" visual titles remain**
  -- every visual has an explicit, descriptive title (per
  `page_build_guide.md`).
- [ ] **Axis titles are present** on every chart (not just the visual
  title).

## Interactivity

- [ ] **Slicers behave correctly.** For each page's slicer set (listed
  in `page_build_guide.md`), select a value and confirm every visual on
  the page updates; clear it and confirm the page returns to its full,
  unfiltered state.
- [ ] **Empty-state behavior.** Pick a slicer combination guaranteed to
  return zero rows (e.g. `Organization = <any org>` AND `Payer =
  <a payer that org never uses>` on Page 1) -- visuals should show a
  blank/"no data" state, not an error or a crash.
- [ ] **Sync Slicers works** across pages for Date/Organization/Payer
  where documented in `page_build_guide.md`.
- [ ] **Cross-filtering direction matches the model.** Clicking a bar in
  a `hospital_operations`-based visual should not unexpectedly filter a
  `financial_analysis`-based visual on another page (there's no
  relationship between them, by design -- confirm that's actually true
  in the built report, not just in the docs).

## Layout & readability

- [ ] **Every page uses a consistent title/subtitle block** at the top
  (mirrors the Streamlit dashboard's header pattern).
- [ ] **No visual overlaps** at the report's default canvas size (View
  -> Page view -> Fit to page).
- [ ] **Mobile layout configured** for at least the Executive Overview
  page (View -> Mobile layout) if the report will be viewed on the
  Power BI mobile app -- stack the 6 KPI cards vertically, trend chart
  next.
- [ ] **Color usage matches the theme** -- no visual manually overridden
  with a color outside `theme.json`'s palette (check Format pane ->
  Colors for stray custom colors).

## Data Quality page specifically

- [ ] **The one genuine failed check is visible, not hidden or
  filtered out** (`Silver Data Quality`, `status = failed`).
- [ ] **Last Pipeline Run / Last Successful Pipeline Run cards** show
  real timestamps from `data_quality_status.csv`, not blank or
  fabricated values.

## Final sign-off

- [ ] File saved as `CareFlow_Analytics.pbix`.
- [ ] Report opens without a "Column not found"/"Can't load model"
  error on a fresh machine (test by closing and reopening the file).
- [ ] Screenshots captured per `docs/screenshots/README.md`'s checklist
  once the visuals above are all confirmed.
