-- (7) Monthly encounter totals reconcile with fact encounters.
with monthly as (
    select year_month, total_encounters
    from {{ ref('int_monthly_healthcare_metrics') }}
),
actual as (
    select year_month, count(distinct encounter_key) as actual_encounters
    from {{ ref('fct_encounters') }}
    group by year_month
)
select m.year_month, m.total_encounters, a.actual_encounters
from monthly m
join actual a on m.year_month = a.year_month
where m.total_encounters != a.actual_encounters
