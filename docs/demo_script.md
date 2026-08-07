# Demo Script (3-5 minutes)

A recruiter/hiring-manager-facing walkthrough. Follow in order; each
step says exactly what to show and what to say. Total target time:
~4 minutes -- practice it once before a live demo.

## Before you start

- Have PostgreSQL running and the warehouse loaded (`make postgres-up`,
  `make warehouse-load`) so the Streamlit dashboard shows real data.
- Have the repository open in GitHub (or your editor) to the README.
- Optional: have Airflow running (`make airflow-up`) and a completed
  DAG run visible in its UI if you want to show it live rather than
  described.

## 1. README overview (30 seconds)

**Show:** the repository's GitHub page, scrolled to the top of `README.md`.

**Say:** *"This is CareFlow Analytics -- an end-to-end healthcare
analytics platform on synthetic data. It's not a single script or
notebook -- it's the full stack: pipeline, warehouse, dbt, Airflow,
and two dashboards."* Point at the badges and the table of contents to
show the scope at a glance.

## 2. Architecture (30 seconds)

**Show:** scroll to the README's Architecture section (Mermaid diagram
renders inline on GitHub), or open `docs/architecture/architecture.md`
directly for the fuller diagram.

**Say:** *"Data flows one direction, through six real layers, into two
delivery surfaces. Every box in this diagram is a working component --
nothing here is aspirational."* Point out the isolated-environment
note if asked why there isn't one single `requirements.txt`.

## 3. Airflow (30-45 seconds)

**Show:** either the live Airflow UI (`localhost:8081`, the
`careflow_end_to_end` DAG's graph view with a completed green run) or,
if not running live, `airflow/dags/careflow_end_to_end.py` in the editor.

**Say:** *"This orchestrates the whole pipeline -- 22 tasks, fully
parameterized, idempotent. I actually found and fixed two real bugs
during this phase -- a foreign-key issue on repeated force-reloads, and
a Docker-socket access issue -- both documented in the interview guide
if you want the technical detail."*

## 4. dbt (30 seconds)

**Show:** `dbt/models/` directory structure (staging/intermediate/marts),
or `dbt docs serve` if you have it running, showing the lineage graph.

**Say:** *"This is the governed analytics layer -- 36 models, 133 tests.
The interesting one is a reconciliation test: I recompute patient
readmissions independently in SQL here and check it matches the Python
layer exactly, so a bug in either implementation gets caught
automatically."*

## 5. PostgreSQL model (30 seconds)

**Show:** `docs/architecture/warehouse_model.md`'s star-schema diagram,
or connect to the database and show `\dt careflow_fact.*` / `\dt
careflow_dim.*`.

**Say:** *"Eight dimensions, eight facts, six marts -- a real star
schema. One specific detail worth mentioning: imaging studies looked
like they had a simple primary key, but didn't -- I found and fixed a
grain bug there, enforced at three separate layers so it can't
regress."*

## 6. Streamlit (45 seconds)

**Show:** `streamlit run dashboard/app.py` (or `make dashboard`), land
on the Executive Overview page.

**Say:** *"This is live, querying PostgreSQL through the dbt layer in
real time."* Change a filter (e.g. organization) and show the charts
update. Point at the Executive Insights panel: *"These sentences are
generated from the actual query results -- month-over-month
comparisons -- never hard-coded, and they only appear when there's
actually enough data to compare."*

## 7. Readmission page (30-45 seconds)

**Show:** navigate to the Readmission Analytics page.

**Say:** *"This is the core clinical-operations use case -- 7/14/30-day
readmission rates, broken down by segment."* Point at the definition
box: *"The methodology is stated right on the page, not buried in
documentation."* Point at the segment table: *"This is aggregated by
age group and gender only -- never individual patients, even though
this is synthetic data with no real privacy risk."*

## 8. Data quality (30 seconds)

**Show:** navigate to the Data Quality page.

**Say:** *"This page pulls real status from every pipeline stage --
Bronze, Silver, Gold, PostgreSQL, dbt, Airflow."* Point at the one
failing Silver check if it's still present: *"This is a real, current
failing check, and I show it honestly rather than filtering it out --
the point of this page is to demonstrate actual pipeline observability,
not a sanitized version of it."*

## 9. GitHub tests (30 seconds)

**Show:** `tests/` directory listing (22 files), or run
`PYTHONPATH=src python3 -m pytest -q tests/` live in a terminal.

**Say:** *"732 automated tests, covering every layer -- ingestion,
transformation, the warehouse loader, dbt, Airflow, and dashboard
security. Nothing in this walkthrough is untested."*

## Closing (15 seconds)

**Say:** *"Everything here uses 100% synthetic data from Synthea -- no
real patient information anywhere. If you want the deeper technical
story on any piece of this, the repo has a full interview guide with
follow-up-question-ready answers."*

## If asked something you didn't plan for

Point to `docs/interview_guide.md` -- it's organized by exactly the
kind of follow-up question this demo tends to generate (why
PostgreSQL, why dbt, how idempotency works, the specific bugs found and
fixed) and every answer there is grounded in what the code actually does.
