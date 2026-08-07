# Recruiter Walkthrough (60 seconds)

A fixed viewing sequence for a recruiter or hiring manager with one
minute of attention -- whether you're narrating it live, sending a
recorded screen capture, or laying out screenshots in a portfolio post.
Each step is ~8-10 seconds; one sentence on what to notice, not what to
say.

This sequence assumes the screenshots in
[`screenshots/README.md`](screenshots/README.md) have been captured --
until then, use this as a live-demo script instead (see
[`demo_script.md`](demo_script.md) for the longer 3-5 minute version of
the same idea).

## The sequence

1. **GitHub README** (`github_repository.png` / the repo home page)
   -- *Notice:* this isn't a single script -- the badges and table of
   contents signal a full stack (PostgreSQL, dbt, Airflow, Streamlit,
   Power BI) with 804 passing tests, not a weekend notebook.

2. **Architecture** (README's Architecture section, or
   `docs/architecture/architecture.md`)
   -- *Notice:* data flows one direction through six real layers into
   two delivery surfaces -- every box in the diagram is a working
   component, not an aspirational roadmap.

3. **Executive dashboard** (`executive_overview.png`)
   -- *Notice:* this is a live, filterable dashboard querying
   PostgreSQL in real time -- not static charts exported from a
   notebook.

4. **Readmission dashboard** (`readmission_analytics.png`)
   -- *Notice:* the methodology (7/14/30-day windows) is stated
   directly on the page, and the segment table is aggregated by
   age/gender/encounter-class only -- PII discipline applied even
   though the data is synthetic.

5. **Airflow DAG** (`airflow_dag.png`)
   -- *Notice:* this is real orchestration -- 22 tasks with actual
   conditional branching, not a single cron job calling one script.

6. **dbt lineage** (`dbt_lineage.png`)
   -- *Notice:* the analytics layer is governed and tested (36 models,
   133 tests) -- lineage is visible and traceable from raw staging
   through to the marts the dashboard reads.

7. **Data-quality page** (`data_quality.png`)
   -- *Notice:* pipeline health is reported honestly, including a real
   unresolved check if one is currently failing -- this is
   observability, not a polished facade.

## If you only have 20 seconds

Steps 1, 3, and 7 alone make the point: real README/architecture,
real live dashboard, honest data-quality reporting.

## Assembling this as a portfolio post

Once all screenshots exist (`docs/screenshots/*.png`), the same seven
images in this order make a clean carousel/gallery post (LinkedIn,
personal site, or a portfolio README section) -- see
[`linkedin_project.md`](linkedin_project.md) for accompanying caption
text and [`project_summary.md`](project_summary.md) for a one-page
written companion.
