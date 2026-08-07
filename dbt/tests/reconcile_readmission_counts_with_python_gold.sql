-- (9) dbt's independently-computed readmission counts reconcile with the
-- Python Gold readmission mart (staged as stg_careflow__readmissions).
-- Uses count_reconciliation_tolerance (default 0 -- exact match expected
-- since both implementations apply the same definition to the same data).
with dbt_counts as (
    select
        count(*) filter (where readmitted_within_7_days) as dbt_7day,
        count(*) filter (where readmitted_within_14_days) as dbt_14day,
        count(*) filter (where readmitted_within_30_days) as dbt_30day
    from {{ ref('int_readmission_events') }}
),
python_counts as (
    select
        count(*) filter (where readmitted_within_7_days) as py_7day,
        count(*) filter (where readmitted_within_14_days) as py_14day,
        count(*) filter (where readmitted_within_30_days) as py_30day
    from {{ ref('stg_careflow__readmissions') }}
)
select dbt_counts.*, python_counts.*
from dbt_counts, python_counts
where abs(dbt_7day - py_7day) > {{ var('count_reconciliation_tolerance') }}
   or abs(dbt_14day - py_14day) > {{ var('count_reconciliation_tolerance') }}
   or abs(dbt_30day - py_30day) > {{ var('count_reconciliation_tolerance') }}
