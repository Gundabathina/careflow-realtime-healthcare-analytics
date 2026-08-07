# Dashboard Portfolio

What each of the 7 Streamlit dashboard pages (mirrored in the Power BI
specification) answers, and how to read it. Every number below is a
real, live value from this project's own synthetic dataset (verified
against `careflow_dbt_mart` at the time of writing) -- not a fabricated
example.

## 1. Executive Overview

**Business question:** *"How is the hospital system performing right
now, at a glance?"*

**KPIs:** Patients Served, Total Encounters, Inpatient Encounters,
Emergency Encounters, 30-Day Readmission Rate, Average Length of Stay,
Total Claim Cost, Payer Coverage Ratio, Average Patient Responsibility.

**Visuals:** encounter volume trend, encounter class distribution,
monthly readmission trend, monthly cost trend, top organizations by
encounters, patient age-group distribution, payer coverage breakdown.

**Interpretation example:** the live dataset shows 58 patients served
across 3,180 total encounters, a 30-day readmission rate of ~1.4%
(3 readmissions out of 207 qualifying encounters), and a payer coverage
ratio of ~77.8% ($6.15M of $7.9M total claim cost covered by payers).
An executive insight panel calculates month-over-month changes directly
from the query results (e.g. *"Total encounters increased 12.4%
compared with the previous month"*) -- and only renders when a real
comparison exists, never a fabricated one.

## 2. Readmission Analytics

**Business question:** *"Which patients and conditions are driving
readmissions, and is the rate improving or worsening?"*

**KPIs:** Qualifying Index Encounters, 7/14/30-Day Readmission Rate,
Average Days to Readmission.

**Visuals:** readmission rate trend, readmissions by encounter class,
by age group, by gender, by organization, distribution of days to
readmission, a table of aggregated high-readmission segments.

**Methodology (shown directly on the page):** *"A qualifying readmission
occurs when a subsequent inpatient or emergency encounter begins within
30 days after the previous qualifying encounter ends."*

**Interpretation example:** of 207 qualifying index encounters, 1 was
readmitted within 7 days, 2 within 14 days, and 3 within 30 days --
every 7-day readmission is also a 14-day readmission, and every 14-day
readmission is also a 30-day readmission (enforced by a dbt test). The
segment table groups by age group x gender only -- never by individual
patient -- so it can be shared without exposing anyone's identity.

## 3. Hospital Operations

**Business question:** *"Where is encounter volume concentrated, and
how does utilization vary by organization?"*

**KPIs:** Total Encounters, Unique Patients, Average/Median Encounter
Duration, Emergency Encounter %, Inpatient Encounter %, Providers
Active.

**Visuals:** encounter volume by month, encounter volume by
organization, encounter type distribution, average duration by
organization, emergency/inpatient utilization trends, an organization x
encounter class heatmap, and a full organization comparison table.

**Interpretation example:** emergency encounters make up roughly 5.3%
and inpatient encounters roughly 1.2% of total volume in the live
dataset -- most encounters are ambulatory/outpatient/wellness, which is
realistic for a synthetic population skewed toward routine care.

## 4. Financial Performance

**Business question:** *"What is the hospital system's cost and
coverage picture, and how is it trending?"*

**KPIs:** Total Claim Cost, Total Payer Coverage, Patient
Responsibility, Average Cost per Encounter, Coverage Ratio.

**Visuals:** monthly claim cost trend, payer coverage over time,
patient responsibility trend, cost by encounter class, cost by
organization, coverage ratio by payer, top payers by total coverage.

**Interpretation example:** total claim cost across the dataset is
$7,904,354.16, with $6,148,883.54 (77.8%) covered by payers and the
remainder as patient responsibility. Every page carries a visible note
that these figures are synthetic and do not represent real hospital
financial performance.

## 5. Provider Performance

**Business question:** *"How is clinical activity distributed across
providers?"*

**KPIs:** Active Providers, Total Provider Encounters, Average
Encounters per Provider, Average Patients per Provider, Average
Encounter Duration.

**Visuals:** top providers by encounter volume, top providers by unique
patients, provider utilization trend, speciality distribution, average
duration by provider, claim cost by provider, an interactive ranking
table.

**Language discipline:** every label uses neutral operational terms --
"encounter volume," "patient volume," "utilization" -- never "top
performer," "best," or "worst." Volume reflects activity, not quality
of care, and the dashboard is deliberately careful not to imply
otherwise.

**Interpretation example:** 161 providers are recorded in the warehouse,
averaging roughly 1.5 encounters and ~1.0 unique patients each across
the dataset's full multi-decade span -- a realistic long-tail
distribution for a small synthetic population.

## 6. Patient Population

**Business question:** *"What does the patient population look like,
demographically?"*

**KPIs:** Patient Count, Average Age (estimated), Deceased Patient
Count, Average Encounters/Conditions/Medications per Patient.

**Visuals:** age-group distribution, gender, race, ethnicity
distribution, geographic distribution (state/county), and distributions
of encounters/conditions/medications per patient.

**Interpretation example:** 58 patients are recorded, spanning all six
age-group buckets (`0-17` through `80+`). Average Age is explicitly
labeled "estimated" -- computed from age-group bucket midpoints, since
the underlying data intentionally never exposes an exact age or birth
date. Deceased Patient Count is labeled "N/A," not a fabricated 0 -- the
public data model doesn't carry that field at all.

**PII discipline:** this page never displays patient names, SSNs,
passports, driver's licenses, exact street addresses, or precise
latitude/longitude -- those fields are dropped in the Gold layer and
never present in `careflow_dbt_mart` to begin with (see `docs/security.md`).

## 7. Data Quality / Pipeline Health

**Business question:** *"Is the pipeline actually healthy, and when did
it last run successfully?"* -- this page exists specifically to
demonstrate data engineering maturity, not to make the pipeline look
better than it is.

**What it shows:** Bronze ingestion status, Silver/Gold validation
results, PostgreSQL warehouse validation, dbt test results, and Airflow
orchestration status -- pulled live from each stage's own existing
report file, never re-run or re-computed by the dashboard itself.

**Interpretation example:** as of the last verified run, PostgreSQL
validation shows 172/172 checks passed and dbt shows 133/133 tests
passed -- but Silver Data Quality shows one genuine failed check (out
of 229). That failure is displayed as-is, not hidden or filtered out --
the whole point of this page is honest pipeline visibility.

## See also

- `docs/dashboard_guide.md` -- the dashboard's technical architecture
- `powerbi/page_build_guide.md` -- the same seven pages, specified for Power BI
- `docs/screenshots/README.md` -- what to capture once the dashboard is running
