# Interview Notes (Quick-Reference Cheat Sheet)

A one-page skim before a call -- bullet points only, no prose. For the
full explanation behind any line here, see
[`interview_guide.md`](interview_guide.md), which this file indexes
but deliberately does not repeat.

## 30-second pitch

Synthetic healthcare data (Synthea) -> Bronze/Silver/Gold pipeline ->
dimensional PostgreSQL warehouse -> dbt (36 models, 133 tests) ->
Airflow orchestration (2 DAGs) -> Streamlit dashboard + Power BI.
804 tests. 100% synthetic data, zero real PHI.

## Numbers to have ready

- 58 patients, 3,180 encounters, 22 warehouse tables (8 dim / 8 fact / 6 mart)
- 36 dbt models, 133 dbt tests, 172 PostgreSQL validation checks
- 804 pytest tests (2 intentionally skipped -- isolated envs, both run for real there)
- 2 Airflow DAGs: 22 tasks (full run) + 11 tasks (daily incremental)
- Full source: [`project_metrics.md`](project_metrics.md) -- every number there is regenerable, not hand-typed

## "Why X" one-liners

| Question | One-liner | Full answer |
|---|---|---|
| Why PostgreSQL? | Real transactional guarantees + FK/indexing, closer to production than files | [§5](interview_guide.md#5-why-postgresql) |
| Why dbt? | Separates load logic (Python) from governance/testing/docs (SQL+YAML); enables independent reconciliation tests | [§6](interview_guide.md#6-why-dbt) |
| Why Airflow? | DAG branching maps directly onto real conditional pipeline steps | [§7](interview_guide.md#7-why-airflow) |
| Why Parquet? | Typed, columnar, compressed -- fast for the profiling/transform read patterns between stages | [§8](interview_guide.md#8-why-parquet) |
| Why isolated venvs (dbt/Airflow/Streamlit)? | Each has a Python-version or `pyarrow` pin conflicting with the main env; isolation beats compromising the main pin | [`architecture/technology_stack.md`](architecture/technology_stack.md) |

## Bugs found and fixed (always have 1-2 ready)

1. **Imaging-study grain bug** -- looked like a simple primary key,
   wasn't; fixed with a composite key enforced at 3 layers (Gold, dbt,
   warehouse validator) so it can't silently regress. [§13](interview_guide.md#13-imaging-study-grain-issue-and-how-it-was-fixed)
2. **Foreign-key force-reload failure** -- repeated `--force` runs could
   trip FK violations; fixed with a whole-batch transactional
   clear-then-reload order (marts, then facts, then dims -- reload
   reversed). [§14](interview_guide.md#14-foreign-key-force-reload-problem-and-how-it-was-fixed)
3. **Docker-socket/Airflow readiness issue** -- fixed startup race
   between dependent services. [§15](interview_guide.md#15-dockerairflow-readiness-issue-and-how-it-was-fixed)

## Design decisions likely to be probed

- **Idempotency** -- every stage safely re-runnable; checksums/dependency
  signatures gate reprocessing. [§9](interview_guide.md#9-how-idempotency-works)
- **Incremental processing** -- checksum-based skip at Silver/Gold,
  dependency-signature at Gold, checksum at PostgreSQL load. [§10](interview_guide.md#10-how-incremental-processing-works)
- **Readmission definition** -- 7/14/30-day windows, computed
  independently in Python (Gold) and SQL (dbt), reconciled by a
  singular dbt test. [§12](interview_guide.md#12-readmission-definition)
- **Dashboard security** -- parameterized SQL only, PII-column
  allow/deny check on every result set, checked at 3 layers total
  (Gold, dbt, dashboard). [§16](interview_guide.md#16-dashboard-security-strategy)

## If asked "what would you change for real production"

Cloud-managed warehouse, CI/CD test gate, real-time/streaming ingestion
(explicitly out of scope here), ML readmission risk scoring (explicitly
out of scope here), dedicated dimension exports for Power BI
cross-filtering. See [§17-18](interview_guide.md#17-scaling-strategy)
and [`README.md#22-future-improvements`](../README.md#22-future-improvements).

## Guardrails (don't overclaim in the room)

- This is **operational/financial analytics**, not a clinical
  decision-support or diagnostic tool.
- All data is synthetic (Synthea) -- there is no real business-impact
  measurement to cite; frame value in terms of the *kind* of decisions
  this analysis enables, not measured dollar savings. See
  [`business_value.md`](business_value.md).
