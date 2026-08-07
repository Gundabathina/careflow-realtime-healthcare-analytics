# GitHub Repository Setup

Recommended configuration for publishing this repository, once
`git init` and an initial commit/push have happened (this project is
not currently a git repository -- see the final Phase 6 report for
what's tracked-vs-not before that first commit).

## Repository description

> End-to-end healthcare analytics platform on synthetic Synthea data --
> Bronze/Silver/Gold pipeline, dimensional PostgreSQL warehouse, dbt,
> Apache Airflow orchestration, Streamlit dashboard, and a prepared
> Power BI implementation. 732 automated tests.

## About section

- **Description:** use the repository description above.
- **Website:** leave blank unless the Streamlit dashboard is deployed
  somewhere publicly reachable (e.g. Streamlit Community Cloud) -- don't
  link a `localhost` URL.
- **Topics:** see below.

## Recommended topics

```
healthcare-analytics
data-engineering
data-analytics
analytics-engineering
python
postgresql
dbt
apache-airflow
streamlit
docker
power-bi
synthea
etl
elt
data-warehouse
dimensional-modeling
data-quality
pytest
synthetic-data
```

## Pinned repository strategy

If this is one of several portfolio repositories on your profile, pin
it alongside 1-2 others that demonstrate *different* skills (e.g. a
frontend project, a systems/algorithms project) rather than several
similar data projects -- this one already covers data engineering,
analytics engineering, orchestration, and BI in a single repository, so
it doesn't need a companion data project to round it out.

## Before publishing: final checklist

- [ ] `.env` is not tracked (`git ls-files | grep '^\.env$'` returns nothing)
- [ ] No virtual environment directories are tracked (`.venv*`, `venv/`)
- [ ] `README.md` renders correctly on GitHub (Mermaid diagrams included
      -- GitHub renders ```mermaid fenced blocks natively)
- [ ] All internal doc links resolve (no broken relative links)
- [ ] LICENSE, CONTRIBUTING.md, CHANGELOG.md are present at the repo root
- [ ] Run `PYTHONPATH=src python3 -m pytest -q tests/` one more time
      immediately before pushing

## CI/CD note

This repository does not currently include a GitHub Actions workflow.
Adding one (running `pytest` on every push/PR) is listed as a future
improvement in the README -- deliberately not added yet, and no CI
badge is shown in the README, so as not to claim automation that isn't
actually configured.
