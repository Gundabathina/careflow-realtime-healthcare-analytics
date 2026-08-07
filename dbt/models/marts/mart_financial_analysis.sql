select
    e.payer_key,
    pay.payer_name,
    e.organization_key,
    o.organization_name,
    e.year_month,
    count(distinct e.encounter_key) as encounter_count,
    sum(e.total_claim_cost) as total_claim_cost,
    sum(e.payer_coverage) as total_payer_coverage,
    sum(e.patient_responsibility) as total_patient_responsibility,
    {{ safe_divide('sum(e.total_claim_cost)', 'count(distinct e.encounter_key)') }} as average_claim_cost,
    {{ safe_divide('sum(e.payer_coverage)', 'sum(e.total_claim_cost)') }} as coverage_ratio
from {{ ref('int_encounters_enriched') }} e
left join {{ ref('stg_careflow__payers') }} pay on e.payer_key = pay.payer_key
left join {{ ref('stg_careflow__organizations') }} o on e.organization_key = o.organization_key
group by e.payer_key, pay.payer_name, e.organization_key, o.organization_name, e.year_month
