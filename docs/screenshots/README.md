# Screenshots (to be captured)

**No screenshots exist in this repository yet.** This file documents
exactly what to capture, with what filename, once the dashboard,
Airflow, and dbt docs are running locally. Do not fabricate images --
add real ones here (`docs/screenshots/*.png`) when available, and only
then link them from the project `README.md`. Until a screenshot exists,
the README intentionally does not embed it.

## Dashboard pages

Run `PYTHONPATH=src python3 scripts/start_dashboard.py`, then capture
each page at a wide viewport (1600px+) with real data loaded and at
least one filter applied where relevant:

- [ ] `executive_overview.png` -- Page 1, full page including the KPI row and Executive Insights panel
- [ ] `readmission_analytics.png` -- Page 2, including the readmission definition box and segment table
- [ ] `hospital_operations.png` -- Page 3, including the organization x encounter class heatmap
- [ ] `financial_performance.png` -- Page 4
- [ ] `provider_performance.png` -- Page 5, including the provider ranking table
- [ ] `patient_population.png` -- Page 6
- [ ] `data_quality.png` -- Page 7, including pipeline stage status cards

## Orchestration & modeling

- [ ] `airflow_dag.png` -- `careflow_end_to_end` DAG graph view in the Airflow UI (`localhost:8081`), a completed successful run (all tasks green)
- [ ] `dbt_lineage.png` -- `dbt docs generate` lineage graph (`.venv-dbt/bin/dbt docs serve --project-dir . --profiles-dir .`), showing staging -> intermediate -> marts
- [ ] `architecture.png` -- a rendered export of `docs/architecture/architecture.md`'s Mermaid diagram (e.g. via the Mermaid Live Editor, or a GitHub-rendered screenshot of the `.md` file itself)

## Capturing checklist

1. Ensure the warehouse is loaded and validated (`postgres_validation_report.json` shows 172/172) before capturing any dashboard or dbt screenshot -- an empty/error state isn't representative.
2. Use a consistent browser window size across all dashboard screenshots (recommended: 1920x1080, then crop to content).
3. For the Airflow screenshot, trigger a real run first (`scripts/trigger_careflow_dag.py`) and wait for it to complete.
4. Do not include any browser chrome, bookmarks bar, or personal information in the capture.
5. Save as PNG, reasonably compressed (a portfolio README shouldn't ship multi-megabyte images).

## Usage once captured

Add the image to this directory with the exact filename above, then
add a Markdown image reference in the relevant README section, e.g.:

```markdown
![Executive Overview](docs/screenshots/executive_overview.png)
```

Only add the reference once the file actually exists in the repository
-- a README linking to a missing image is exactly what this phase's
repository-quality checks (`tests/test_repository_quality.py`) verify
against.
