# Business Value

**Framing note up front:** this project runs on 100% synthetic data
(Synthea) and was never deployed against a real hospital or measured
against a real business outcome. Nothing below claims a measured
dollar figure, cost saving, or outcome -- that would be fabricated.
What follows is what the *category* of platform this project
demonstrates is built to enable, the same way a case study describes
the intended use of a tool, not a specific customer's audited results.

## Who would use this, and for what

| Stakeholder | Question this platform is built to answer |
|---|---|
| Hospital operations leadership | Where is encounter volume concentrated, and where is emergency/inpatient utilization trending? |
| Finance / revenue cycle | What's total claim cost, and what share is covered by payer vs. patient responsibility? |
| Quality / care management | Which patient segments (age, gender, encounter type) show elevated 7/14/30-day readmission rates? |
| Provider operations | How is clinical activity distributed across providers and organizations? |
| Data/analytics leadership | Can the numbers above be trusted -- is the pipeline behind them actually correct? |

Every one of these questions maps directly to a page in the Streamlit
dashboard or a prepared Power BI report page -- see
[`dashboard_portfolio.md`](dashboard_portfolio.md) for the mapping.

## Why the *platform*, not just a dashboard, is the point

A dashboard is only as trustworthy as the pipeline feeding it. This
project's value case rests on the layers underneath the charts:

- **Validated ingestion** -- data quality and referential-integrity
  checks gate what's allowed into the warehouse at all, so a malformed
  source file can't silently corrupt downstream reporting.
- **Independent reconciliation** -- readmissions and financial totals
  are computed twice, once in Python and once in SQL, and a test fails
  the build if they disagree. This is the difference between "the
  dashboard shows a number" and "the number is verified correct by two
  independent implementations."
- **Governed, tested analytics layer (dbt)** -- every public model has
  documented columns and enforced tests, so a downstream analyst (or
  Power BI report) is working from a governed contract, not an
  ad-hoc query against raw tables.
- **PII discipline treated as a hard requirement, not an
  afterthought** -- enforced at three independent layers even though
  the data is synthetic, because a platform designed this way is one
  that would already meet the bar if real patient data were introduced
  later.
- **Honest observability** -- the Data Quality page surfaces a real,
  currently-failing check rather than hiding it, because a leadership
  team acting on these numbers needs to know when a specific figure
  is not yet trustworthy, not just see green everywhere.

## What this is explicitly *not*

- **Not a clinical decision-support or diagnostic tool.** It reports
  operational and financial metrics; it does not recommend, predict,
  or influence individual patient care.
- **Not a measured ROI case study.** No real deployment, no real
  users, no audited savings figure -- see the framing note above.
- **Not a claim of production-scale readiness as-is.** See
  [`README.md#22-future-improvements`](../README.md#22-future-improvements)
  and [`deployment_guide.md`](deployment_guide.md) for what a real
  production rollout would still need (cloud hosting, CI/CD, monitoring).

## See also

- [`project_summary.md`](project_summary.md) -- one-page overview
- [`technical_highlights.md`](technical_highlights.md) -- the engineering behind the trust properties above
- [`data_ethics.md`](data_ethics.md) -- the synthetic-data and PII policy in full
