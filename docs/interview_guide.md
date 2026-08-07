# Interview Guide

Every answer here is defensible against a real follow-up -- grounded in
what this project actually does, not what would sound impressive. If an
answer below claims a technology or technique, it's because the code in
this repository actually does it; nothing here overstates the project.

## 1. 30-second project explanation

> "CareFlow Analytics is an end-to-end healthcare analytics platform I
> built on synthetic patient data. It takes synthetic records through a
> Bronze/Silver/Gold transformation pipeline into a dimensional
> PostgreSQL warehouse, adds a governed dbt analytics layer with 133
> automated tests, orchestrates the whole thing with Airflow, and
> delivers the results through a live Streamlit dashboard and a
> prepared Power BI report. It's the same kind of stack a hospital
> system's data engineering team would actually build -- just on
> synthetic data instead of real patient records."

## 2. 90-second project explanation

> "The goal was to build a realistic healthcare analytics platform end
> to end, not just a dashboard on top of a CSV. I start with Synthea, an
> open-source synthetic patient generator, which gives me realistic-
> looking encounters, conditions, claims, and demographics with zero
> real-PHI risk. From there, data moves through three layers: Bronze,
> which is just typed Parquet with a data-quality gate; Silver, which
> standardizes and cleans; and Gold, which builds an actual dimensional
> star schema -- eight dimensions, eight facts, six marts -- with
> deterministic surrogate keys and independently-computed readmission
> logic. That loads into PostgreSQL transactionally and incrementally.
> On top of that I built a dbt layer -- staging, intermediate, and mart
> models, 133 tests, and a reconciliation test that independently
> recomputes readmissions in SQL and checks it matches the Python Gold
> layer exactly, so I'd catch a bug in either implementation. Apache
> Airflow orchestrates all of it with two DAGs -- a full parameterized
> run and a daily incremental one -- and I actually found and fixed a
> couple of real bugs during that phase, which I'm happy to walk
> through. For delivery, there's a live Streamlit dashboard with seven
> pages, and I also fully specified a Power BI implementation -- data
> model, DAX measures, page-by-page build guide -- since Power BI
> Desktop wasn't available in my dev environment and I didn't want to
> fake a `.pbix` file. The whole thing has 732 automated tests."

## 3. Architecture explanation

**Q: Walk me through the architecture.**

**A:** Layered, one-directional data flow: Synthea generates raw
CSV -> profiling and validation gate what gets promoted -> Bronze
(typed Parquet) -> Silver (standardized) -> Gold (dimensional model) ->
PostgreSQL -> dbt (governed reporting layer) -> Streamlit/Power BI.
Airflow orchestrates it; Docker Compose hosts PostgreSQL and Airflow
locally. Every layer only reads from the layer directly below it --
nothing skips ahead or reaches back upstream, which is what makes the
checksum-based incremental skip logic at each layer safe to reason about.

**Follow-up: Why not fewer layers -- why Bronze *and* Silver *and* Gold?**

**A:** Each layer has a distinct job and a distinct failure mode I
wanted to isolate. Bronze's only job is "is this file structurally
valid and does it pass the data-quality gate" -- typed but otherwise
raw. Silver's job is standardization -- if a source system changes a
category label, that's a Silver-layer concern, not a Bronze or Gold
one. Gold's job is dimensional modeling and business logic (surrogate
keys, readmission computation) -- if I find a modeling bug, I know
exactly which layer to fix without touching ingestion or standardization
code. Collapsing them would make each bug fix riskier, not simpler.

## 4. Bronze/Silver/Gold explanation

**Q: What does each layer actually do?**

**A:** Bronze: typed Parquet conversion of the raw CSVs, gated by a
data-quality/relationship check (a file with a blocking issue is
skipped, not ingested), with a manifest recording row counts, schema,
and checksums per file. Silver: standardizes types and categorical
values across datasets, derives fields like age at a reference date,
and skips a dataset entirely if its Bronze checksum hasn't changed.
Gold: builds the actual star schema -- deterministic surrogate keys
(so re-running produces the same keys, not new ones), independently
computed 7/14/30-day readmission logic, and its own checksum/dependency-
signature-based incremental skip.

**Follow-up: How do you know Silver/Gold are actually correct, not just
"ran without errors"?**

**A:** Each layer writes its own quality report -- 229 checks at
Silver, 119 at Gold -- comparing outputs back against known invariants
(row counts, referential integrity, business-rule ranges). And it's not
hypothetical: Silver's report currently shows one genuine failed check
out of 229, and I show that honestly on the dashboard's Data Quality
page rather than hiding it. That's deliberate -- a portfolio project
that only ever shows green checkmarks doesn't actually demonstrate a
data quality *strategy*.

## 5. Why PostgreSQL

**Q: Why PostgreSQL instead of a cloud warehouse like Snowflake or BigQuery?**

**A:** For this project's scope -- a local, reproducible, single-node
analytical workload -- PostgreSQL gives me everything I need: a real
relational engine with foreign keys, transactions, window functions,
and `COPY` for fast bulk loads, runnable entirely in Docker with no
cloud account or billing dependency. It's also what dbt-postgres and
most local analytics-engineering tutorials target, so it kept the dbt
layer straightforward.

**Follow-up: What would change if this needed to scale to a real
hospital system's data volume?**

**A:** I'd look at a columnar cloud warehouse (Snowflake, BigQuery,
Redshift) once single-node PostgreSQL's write/query performance became
the bottleneck -- but I'd want to see an actual bottleneck first rather
than pre-optimizing. dbt's model layer is largely warehouse-agnostic by
design, so that migration would mostly touch the loading script and
Postgres-specific SQL (window functions, `COPY`), not the whole
analytics layer.

## 6. Why dbt

**Q: Why add dbt on top of a warehouse you already load with Python?**

**A:** The Python loader's job is getting Gold's Parquet files into
PostgreSQL reliably -- it's not meant to also own business-logic SQL,
testing, or documentation. dbt gives me a governed, tested,
documented reporting layer on top: staging models with explicit
columns (no `SELECT *`, no accidental PII), intermediate models with
the actual business logic, and marts that are the only thing dashboards
and Power BI ever touch. The 133 tests and lineage graph are the real
value -- I can change a model and immediately know what broke.

**Follow-up: Doesn't that duplicate the readmission logic that's
already in the Python Gold layer?**

**A:** Yes, deliberately. dbt's `int_readmission_events` independently
recomputes 7/14/30-day readmissions in SQL, and a singular test
reconciles its counts against the Python Gold layer's own
`mart_readmission` output within an explicit tolerance. If the two ever
disagreed, that's a real bug in one implementation, and I'd rather find
it via a failing test than a stakeholder noticing a wrong number on a
dashboard.

## 7. Why Airflow

**Q: Why Airflow instead of just running the scripts in sequence with cron?**

**A:** Cron would work for a fixed schedule, but I wanted retry
semantics, task-level observability, parameterized manual runs (should
this run regenerate synthetic data? force a reload?), and a proper DAG
so a failure at one stage correctly blocks downstream stages instead of
silently continuing with stale data. Airflow's UI also makes pipeline
state visible at a glance, which matters for the "data engineering
maturity" story this whole project is trying to tell.

**Follow-up: Your custom operator has a fixed command registry instead
of running arbitrary shell commands -- why?**

**A:** Security and predictability. `CareFlowCommandOperator` only
executes commands from a hard-coded `COMMAND_REGISTRY` dict, and only
appends extra flags from an explicit per-command allow-list (`--force`
where applicable, a bounds-checked `--population`). DAG-run parameters
never reach a shell string directly -- they're validated, then mapped
through that allow-list. It's a small amount of extra code for a
guarantee that a bad or malicious `dag_run.conf` can't turn into
arbitrary command execution.

## 8. Why Parquet

**Q: Why Parquet instead of just keeping everything as CSV, or writing
straight to PostgreSQL?**

**A:** Parquet is typed and columnar -- Bronze needs typed data (a CSV
column is just text until something parses it), and every layer after
Bronze benefits from columnar reads when only a few fields are needed.
It's also what makes the checksum-based incremental skip pattern clean:
I can hash a Parquet file's contents and know definitively whether
anything changed, which is messier with a mutable database table.
Writing straight to PostgreSQL from Bronze would also couple ingestion
to warehouse availability -- with Parquet as the intermediate format,
Bronze/Silver/Gold can run and be tested completely independently of
whether PostgreSQL is even up.

## 9. How idempotency works

**Q: What happens if you run the same pipeline stage twice?**

**A:** Every layer is designed so a repeated run with unchanged input
produces the same output, not a duplicate or a corrupted one. Bronze
re-validates and re-ingests every gated-clear file each run (no
incremental skip there by design). Silver and Gold skip a
dataset/table entirely if its upstream checksum/dependency signature is
unchanged. The PostgreSQL loader is the most interesting case: a
non-force run skips unchanged tables by checksum, and a `--force` run
does a whole-batch transactional clear-then-reload (all marts, then all
facts, then all dimensions cleared; then dimensions, facts, marts
reloaded, in that dependency order, in one transaction) so it's safe to
run `--force` back-to-back without a Docker volume reset in between.

**Follow-up: How did you actually verify that, not just assume it?**

**A:** By running it twice for real, deliberately, and checking the
resulting row counts and warehouse validation matched -- both `python
load_postgres_warehouse.py --force` runs and Airflow's
`careflow_end_to_end` DAG twice in a row, confirming 22/22 tables load
successfully both times and PostgreSQL validation stays at 172/172.

## 10. How incremental processing works

**Q: How does the pipeline avoid reprocessing everything on every run?**

**A:** Checksum comparison at Silver and Gold (a dataset's/table's
current source checksum is compared against the checksum recorded the
last time it was successfully processed; unchanged means skip), and the
same pattern at the PostgreSQL load step, keyed off Gold's own
`source_checksum` per table. `--force` bypasses the check when you
genuinely want a full reprocess.

**Follow-up: What's the tradeoff of checksum-based skip versus, say,
a `updated_at` timestamp column?**

**A:** Checksums are more reliable when the *source file* is the unit
of change (a whole Parquet file, rewritten atomically) -- there's no
risk of a stale or missing timestamp giving a false "unchanged" signal.
The tradeoff is you can't skip *part* of a file -- it's all-or-nothing
per table/dataset. For this project's grain (whole-table Parquet
outputs), that's the right tradeoff; a row-level `updated_at` pattern
would matter more for a system with true row-level streaming updates.

## 11. Data quality strategy

**Q: What's your overall approach to data quality in this pipeline?**

**A:** Validate early and often, and never hide a failure. Raw data
gets profiled and relationship-checked before Bronze will even ingest
it. Silver and Gold each run their own quality-check suite (229 and
119 checks respectively) against their own outputs. PostgreSQL gets a
172-check validation comparing the warehouse back to Gold. dbt adds 133
more tests, including three that independently reconcile computed
figures against the Python layer. And critically, the Data Quality
dashboard page surfaces all of this -- including the one real failing
Silver check -- rather than only showing a sanitized "all green" state.

**Follow-up: What would you do differently for a production system?**

**A:** I'd add alerting on failure (this project surfaces failures in
reports and a dashboard, but nothing pages anyone), and I'd want
data-quality checks to be able to *block* a downstream stage more
granularly than "the whole layer skip/fail" -- e.g. quarantine a single
bad record rather than failing an entire file.

## 12. Readmission definition

**Q: How do you define a readmission?**

**A:** "A qualifying readmission occurs when a subsequent inpatient or
emergency encounter begins within 30 days after the previous qualifying
encounter ends." It's computed twice, independently -- once in the
Python Gold layer using pandas, once in dbt using a SQL window function
(`lead()` over encounters ordered by patient and start time) -- and a
test reconciles the two. I also compute 7- and 14-day variants of the
same logic, and test that a 7-day readmission is always also a 14- and
30-day readmission (a logical consistency check, not just a numeric one).

**Follow-up: What data quality issue did that logic surface?**

**A:** Overlapping/back-to-back encounters in the synthetic data --
some patients have a next encounter that starts *before* the previous
one's recorded discharge time, which would make "days since previous
encounter" negative. Rather than emit a negative number (which is
nonsensical) or silently clip it to zero (which hides the issue), I
null it out and treat it as "not meaningfully computable for this
record" -- applied consistently in both the Python and dbt
implementations.

## 13. Imaging-study grain issue and how it was fixed

**Q: Tell me about a real bug you found and fixed in this project.**

**A:** The imaging studies source data's `Id` column looked like it
should be a primary key, but it isn't -- a single imaging study can
have multiple series, and each series multiple instances, all sharing
the same `Id`. Treating `Id` as the grain would have silently collapsed
real rows together or thrown a duplicate-key error depending on load
order. I fixed it by making the surrogate key a deterministic hash of
the composite `(study_id, series_uid, instance_uid)` -- the actual row
grain -- and enforced it at three layers: Gold builds the key from the
composite, dbt has an explicit `dbt_utils.unique_combination_of_columns`
test on the composite (and a comment/test specifically asserting
`study_id` alone is *not* tested as unique, so nobody "fixes" it back
to the wrong grain later), and the Power BI data dictionary repeats the
same warning.

**Follow-up: How did you catch it in the first place?**

**A:** Profiling the raw source data before writing any transformation
logic -- checking cardinality of what looked like an identifier column
against the actual row count showed it wasn't 1:1. That's exactly why
the pipeline has a dedicated profiling stage before Bronze ingestion,
not just a "run it and see what breaks" approach.

## 14. Foreign-key force-reload problem and how it was fixed

**Q: What happens if you tell me about a bug that only showed up on a
second run, not the first?**

**A:** Exactly that happened with the PostgreSQL loader's `--force`
flag. The original implementation reloaded tables one at a time, in
dependency order (dimensions, then facts, then marts) -- deleting and
re-inserting each table's rows in its own transaction. That's fine on
an empty database. But on a *second* `--force` run against an already-
populated warehouse, deleting a dimension table's rows failed with a
foreign-key violation, because fact tables still held rows referencing
it -- the loop hadn't gotten to clearing those facts yet.

**Follow-up: How did you fix it, and how did you verify the fix without
just trusting it?**

**A:** Rewrote it as a single whole-batch transaction: clear every
in-scope table in *reverse* dependency order first (marts, then facts,
then dimensions -- so nothing is ever deleted while something still
references it), then reload in forward dependency order (dimensions,
facts, marts), all inside one transaction that rolls back completely on
any failure. I verified it by actually running `--force` twice in a
row against the same running container -- no volume reset in between --
and confirming 22/22 tables loaded successfully both times, then did
the same thing again through the Airflow DAG for full end-to-end proof.

## 15. Docker/Airflow readiness issue and how it was fixed

**Q: What broke when you actually orchestrated the pipeline with Airflow
that didn't break running the scripts by hand?**

**A:** The "ensure PostgreSQL is ready" task originally shelled out to
`docker compose up -d postgres` and `docker inspect` -- fine on the
host, but the Airflow container deliberately has no Docker socket
mounted (mounting one is a real privilege-escalation surface, and I
didn't want to accept that risk just to make a readiness check
convenient). So that task failed every time it ran *inside* the Airflow
container, even though PostgreSQL itself was healthy and reachable.

**Follow-up: What was the actual fix, and why is it the right one
rather than a workaround?**

**A:** I realized the task's real job, once Airflow itself is already
running in Docker, isn't "start the container" -- that's already done,
separately, by the script that starts Airflow in the first place. The
task only needs to confirm PostgreSQL is *reachable*. So the fix was:
fall back to a plain connectivity check (a direct PostgreSQL connection
attempt) whenever the Docker daemon isn't reachable from wherever the
script is running, leaving the original `docker compose up` + health-
check-wait behavior completely unchanged when it *is* run on the host.
That's a correct behavioral fix, not a workaround -- it matches what
the task is actually supposed to verify in each context.

## 16. Dashboard security strategy

**Q: How do you make sure the dashboard can't leak PII, even by accident?**

**A:** Defense in depth, not one control. The data never carries
restricted PII past the Gold layer in the first place. dbt's public
marts are tested for it explicitly. And independently of both of those,
`dashboard/database.assert_no_restricted_columns` checks every query
result's *column names* against a restricted-token list before the
result is ever rendered or offered for CSV download -- so even a future
mistake in a new query would fail loudly instead of silently rendering
PII.

**Follow-up: How do you prevent SQL injection in the dashboard's filters?**

**A:** Every query in `dashboard/queries.py` uses `%s` parameter
placeholders via psycopg -- filter values are always passed as query
parameters, never string-formatted into SQL text. The one thing that
*does* get chosen by user input and land in SQL text is a column name
(which readmission-window column to filter on), and that's resolved
through a fixed `{7: "readmitted_within_7_days", ...}` dict lookup, so
an unexpected input just falls back to a safe default rather than
reaching the query at all.

## 17. Scaling strategy

**Q: This runs on 58 patients. How would this architecture handle a real
hospital system's volume?**

**A:** The layered design scales reasonably well conceptually: Parquet
and columnar reads handle much larger volumes fine; the checksum-based
incremental pattern means growth in *historical* data doesn't force
reprocessing everything, only what changed. The places I'd actually
need to revisit: PostgreSQL's single-node write throughput (I'd
benchmark `COPY` performance at real volume before assuming it's fine),
whether `fact_observation`-scale tables (already the largest in this
small dataset) need partitioning, and whether Airflow's LocalExecutor
(fine for this project) needs to become CeleryExecutor/KubernetesExecutor
for real parallelism across a bigger DAG.

**Follow-up: Where's the first bottleneck you'd expect?**

**A:** Bronze ingestion's chunked-but-still-sequential CSV read, and
the PostgreSQL bulk load step, are the two places doing the most I/O
per row. I'd profile both under realistic volume before optimizing
either speculatively.

## 18. Production improvements

**Q: What would you need to add before this could be a real production
system?**

**A:** Several things, in rough priority order: (1) CI/CD -- there's no
automated test run on push yet, just the same pytest suite run
locally; (2) real secrets management (a vault/KMS, not `.env` files) if
this ever touched real credentials; (3) HIPAA-appropriate infrastructure
and access controls if it ever touched real PHI -- encryption at rest,
audit logging, formal access review, none of which this project
implements because it doesn't need to for synthetic data; (4) alerting
on pipeline/data-quality failure, not just reporting it; (5) a cloud
warehouse migration path if PostgreSQL's single-node limits are actually
hit. I'd rather list these honestly than imply the current project
already handles them.
