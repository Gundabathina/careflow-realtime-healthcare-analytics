-- (8) Financial totals reconcile within an explicit numeric tolerance
-- (var: currency_reconciliation_tolerance, dbt_project.yml).
with dbt_total as (
    select coalesce(sum(total_claim_cost), 0) as total from {{ ref('fct_encounters') }}
),
source_total as (
    select coalesce(sum(total_claim_cost), 0) as total from {{ ref('stg_careflow__encounters') }}
)
select dbt_total.total as dbt_total, source_total.total as source_total,
       abs(dbt_total.total - source_total.total) as difference
from dbt_total, source_total
where abs(dbt_total.total - source_total.total) > {{ var('currency_reconciliation_tolerance') }}
