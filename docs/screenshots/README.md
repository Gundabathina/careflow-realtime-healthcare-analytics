# Screenshots (to be captured)

**No screenshots exist in this repository yet.** This file is the
precise capture checklist -- what to open, what to show, what to hide,
and the exact filename to save as. Do not fabricate images. Add real
ones here (`docs/screenshots/*.png`) once captured, and only then link
them from the project `README.md` (see "Usage once captured" below) --
a README linking to a missing image is exactly what
`tests/test_repository_quality.py` checks against.

## Global quality rules (apply to every screenshot)

- **Browser width:** 1440px or larger (1600-1920px preferred for the
  dashboard pages, which are wide with a sidebar).
- **Browser zoom:** 90-100% -- don't zoom out further to force more
  content into frame; capture a representative section instead.
- **Window/viewport:** consistent size across all screenshots in the
  same set (all 7 dashboard pages at the same size, for a uniform
  portfolio gallery).
- **Hide:** terminal windows (don't let one cover any part of the
  dashboard), editor/IDE sidebars (unless a screenshot is intentionally
  showing code -- none of the 10 required screenshots are), OS dock/menu
  bar clutter, notification banners/toasts.
- **Must NOT appear, ever:** real credentials or admin passwords, any
  local absolute filesystem path (e.g. `/Users/<name>/...`), personal
  desktop files or icons, browser bookmarks bar or unrelated open tabs,
  browser/OS notifications, your GitHub account menu/avatar dropdown.
- **Dashboard screenshots must show real, loaded data** -- meaningful
  charts and populated KPI cards, never a loading spinner or an empty
  "no data" state. Confirm the warehouse is loaded and validated first:
  `postgres_validation_report.json` should show 172/172 passed.
- **Format:** PNG, reasonably compressed (a portfolio README shouldn't
  ship multi-megabyte images) -- browser dev tools or a tool like
  `pngquant`/`tinypng` work well for this.

## Required screenshots (10)

### 1. `executive_overview.png`

- **App/page:** Streamlit dashboard, page **1 - Executive Overview**
  (`PYTHONPATH=src python3 scripts/start_dashboard.py`, then
  `localhost:8501` -> sidebar -> "1 Executive Overview").
- **Filters:** leave all sidebar filters at their default ("All" /
  full date range) so the screenshot represents the whole dataset.
- **Must be visible:** the KPI row at the top of the page, the
  "Encounters Over Time" trend chart, the "Monthly Readmission Trend"
  chart, and the "Monthly Healthcare Cost Trend" chart. Scroll so all
  four are captured (a single tall capture, or two stacked captures
  stitched together, is fine).
- **Browser zoom:** 90-100%.
- **Window size:** 1600-1920px wide.
- **Hide:** the Streamlit sidebar's filter list can stay visible (it's
  part of the real product), but collapse any browser dev tools panel.
- **Must NOT appear:** the "Executive Insights" panel's auto-generated
  sentences are fine to include (they're computed, not fabricated),
  but don't crop mid-sentence.
- **Filename:** `docs/screenshots/executive_overview.png`

### 2. `readmission_analytics.png`

- **App/page:** page **2 - Readmission Analytics**.
- **Filters:** default filters; optionally set "Readmission window
  (days)" to `30` (the default) so the KPI labels read "30-Day".
- **Must be visible:** the readmission definition box near the top,
  the 7/14/30-day KPI row, the "Readmission Rate Over Time" trend
  chart, and the "Top Patient Segments by Readmission Rate" table at
  the bottom.
- **Browser zoom:** 90-100%.
- **Window size:** 1600-1920px wide.
- **Hide:** browser dev tools.
- **Must NOT appear:** the segment table only ever shows aggregated
  counts by age group/gender/encounter class -- confirm no
  patient-level row is visible (it shouldn't be; this is enforced by
  `tests/test_dashboard_security.py`, but visually double-check).
- **Filename:** `docs/screenshots/readmission_analytics.png`

### 3. `hospital_operations.png`

- **App/page:** page **3 - Hospital Operations**.
- **Filters:** default filters.
- **Must be visible:** the utilization KPI row, the "Organization
  Comparison" table, and the "Organization x Encounter Class" heatmap
  (this is the visually distinctive chart on this page -- make sure
  it's not cropped).
- **Browser zoom:** 90-100%.
- **Window size:** 1600-1920px wide (the heatmap is wide; don't let it
  get squeezed).
- **Hide:** browser dev tools.
- **Must NOT appear:** organization names are already fine to show
  (synthetic, non-PII) -- no additional redaction needed.
- **Filename:** `docs/screenshots/hospital_operations.png`

### 4. `financial_performance.png`

- **App/page:** page **4 - Financial Performance**.
- **Filters:** default filters.
- **Must be visible:** the cost KPI row, the "Payer Coverage Over
  Time" chart, and the "Monthly Claim Cost" trend chart.
- **Browser zoom:** 90-100%.
- **Window size:** 1600-1920px wide.
- **Hide:** browser dev tools.
- **Must NOT appear:** nothing sensitive here (all figures are
  synthetic claim costs) -- just avoid cropping currency labels.
- **Filename:** `docs/screenshots/financial_performance.png`

### 5. `provider_performance.png`

- **App/page:** page **5 - Provider Performance**.
- **Filters:** default filters.
- **Must be visible:** the utilization KPI row, "Top Providers by
  Encounter Volume" chart, and the "Provider Ranking" table at the
  bottom.
- **Browser zoom:** 90-100%.
- **Window size:** 1600-1920px wide.
- **Hide:** browser dev tools.
- **Must NOT appear:** provider names shown are synthetic Synthea
  names, not real clinicians -- no redaction needed, but don't crop
  the ranking table's header row.
- **Filename:** `docs/screenshots/provider_performance.png`

### 6. `patient_population.png`

- **App/page:** page **6 - Patient Population**.
- **Filters:** default filters.
- **Must be visible:** the demographic KPI row, and at least the
  Age-Group, Gender, and Race distribution charts together (Ethnicity
  and the per-patient histograms can be below the fold).
- **Browser zoom:** 90-100%.
- **Window size:** 1600-1920px wide.
- **Hide:** browser dev tools.
- **Must NOT appear:** the Geographic Distribution chart shows
  state/county only -- confirm no exact address, ZIP, or lat/long is
  visible anywhere on the page (it isn't in the underlying data; just
  a visual sanity check).
- **Filename:** `docs/screenshots/patient_population.png`

### 7. `data_quality.png`

- **App/page:** page **7 - Data Quality**.
- **Filters:** not applicable (this page doesn't use the sidebar
  filters -- it reads pipeline reports directly).
- **Must be visible:** the pipeline stage status cards (Bronze,
  Silver, Gold, PostgreSQL, dbt, Airflow), the "Checks Passed by
  Stage" chart, and ideally the expanded "Full stage detail" table
  (click the expander before capturing).
- **Browser zoom:** 90-100%.
- **Window size:** 1600-1920px wide.
- **Hide:** browser dev tools.
- **Must NOT appear:** nothing to redact -- and don't hide the one
  honest failing Silver check if it's still present; showing it is the
  point of this screenshot (real observability, not a sanitized view).
- **Filename:** `docs/screenshots/data_quality.png`

### 8. `airflow_dag.png`

- **App/page:** Airflow UI (`PYTHONPATH=src python3
  scripts/start_airflow.py`, then trigger a real run first:
  `PYTHONPATH=src python3 scripts/trigger_careflow_dag.py --dag-id
  careflow_end_to_end` and wait for it to finish), `localhost:8081` ->
  the `careflow_end_to_end` DAG -> **Graph** view.
- **Filters:** none -- just select the most recent completed run.
- **Must be visible:** enough of the task graph to show real
  orchestration structure -- the branch points (`decide_generate_data`,
  `decide_dbt_snapshot`, `decide_dbt_docs`) and the linear spine
  through Bronze/Silver/Gold/PostgreSQL/dbt are the most informative
  region. All visible task nodes should be green (success).
- **Browser zoom:** 90-100%; zoom the Airflow graph itself out just
  enough that task names stay legible.
- **Window size:** 1600-1920px wide.
- **Hide:** the Airflow top nav is fine to show; hide any connection
  list or Admin -> Connections page (not part of this screenshot
  anyway).
- **Must NOT appear:** the Airflow login screen, username/password
  fields, the `AIRFLOW_ADMIN_PASSWORD` value, any Fernet/secret key,
  or the Variables/Connections pages (which could show connection
  strings). Capture only the DAG Graph view itself.
- **Filename:** `docs/screenshots/airflow_dag.png`

### 9. `dbt_lineage.png`

- **App/page:** dbt docs
  (`set -a && source .env && set +a`, then
  `PYTHONPATH=src python3 scripts/run_dbt.py docs-generate`, then
  `.venv-dbt/bin/dbt docs serve --project-dir . --profiles-dir .`),
  the **Lineage Graph** view (bottom-right graph icon, or a specific
  model's "View Lineage Graph").
- **Filters:** in the lineage graph's selector, it's fine to select
  "+model_name+" for one of the mart models (e.g.
  `mart_readmission_analysis` or `fct_encounters`) so the graph isn't
  overwhelming -- the goal is a readable staging -> intermediate ->
  marts flow, not every one of the 36 models crammed into one frame.
- **Must be visible:** at least one full staging -> intermediate ->
  marts chain, with node labels legible enough to show dimension and
  fact models by name.
- **Browser zoom:** 90-100%.
- **Window size:** 1600-1920px wide.
- **Hide:** browser dev tools.
- **Must NOT appear:** the dbt docs "Database" tab can leak schema
  connection details in some setups -- stay on the Lineage Graph /
  model documentation tabs only, not any page showing a raw connection
  string.
- **Filename:** `docs/screenshots/dbt_lineage.png`

### 10. `github_repository.png`

- **App/page:** the published GitHub repository's home page:
  `https://github.com/Gundabathina/careflow-realtime-healthcare-analytics`.
- **Filters:** none.
- **Must be visible:** the repository title/name, the README's hero
  section (title, one-line description, technology badges), and
  either the rendered architecture Mermaid diagram (scroll to the
  Architecture section) or the file tree / About sidebar (topics,
  description) -- whichever single screenshot best represents "what is
  this project" at a glance. Two separate crops (hero+badges, and
  architecture diagram) are also fine if one screenshot can't fit both
  well.
- **Browser zoom:** 90-100%.
- **Window size:** 1440px or larger.
- **Hide:** the browser's bookmarks bar and any unrelated open tabs.
- **Must NOT appear:** your GitHub avatar/account menu (top-right),
  any notification bell badge count, and any other repository in a
  "your repositories" sidebar if visible -- crop to just this repo's
  page content.
- **Filename:** `docs/screenshots/github_repository.png`

### Optional: `architecture.png`

Not one of the 10 required screenshots above -- GitHub already renders
the README's Architecture Mermaid diagram natively, so
`github_repository.png` can cover it. Only capture this separately if
you want a standalone, cropped export of just the diagram (e.g. via
the Mermaid Live Editor, or a tight screenshot of
`docs/architecture/architecture.md` rendered on GitHub) for use outside
the repository (a slide, a LinkedIn post). Filename:
`docs/screenshots/architecture.png`.

## Capturing checklist (in order)

1. Load and validate the warehouse first --
   `postgres_validation_report.json` should show 172/172 passed --
   before capturing any dashboard, dbt, or Airflow screenshot.
2. Capture the 7 dashboard screenshots (1-7) in one sitting, at the
   same browser window size, with default filters, so the set looks
   consistent as a gallery.
3. Trigger a real Airflow run and wait for it to complete, then
   capture screenshot 8.
4. Generate dbt docs and capture screenshot 9.
5. Capture the published GitHub repository page (screenshot 10) last,
   since it should reflect the final state of everything else.
6. Save every file as PNG directly into `docs/screenshots/` using the
   exact filenames above -- the README link step below depends on
   exact name matches.

## Usage once captured

Add each image to this directory with the exact filename above, then
add a Markdown image reference in the relevant README section, e.g.:

```markdown
![Executive Overview](docs/screenshots/executive_overview.png)
```

Only add the reference once the file actually exists in the repository
-- a README linking to a missing image is exactly what
`tests/test_repository_quality.py` checks against. See
[`docs/recruiter_walkthrough.md`](../recruiter_walkthrough.md) for how
these screenshots are intended to be used together once captured.
