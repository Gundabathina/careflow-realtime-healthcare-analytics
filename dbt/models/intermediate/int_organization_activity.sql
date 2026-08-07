select
    o.organization_key,
    o.organization_name,
    e.encounter_date_key,
    d.full_date as encounter_date,
    e.encounter_class,
    count(distinct e.encounter_key) as encounter_count,
    count(distinct e.patient_key) as unique_patients,
    avg(e.encounter_duration_minutes) as average_duration_minutes,
    percentile_cont(0.5) within group (order by e.encounter_duration_minutes) as median_duration_minutes,
    sum(case when e.is_inpatient then 1 else 0 end) as inpatient_count,
    sum(case when e.is_emergency then 1 else 0 end) as emergency_count,
    sum(e.total_claim_cost) as total_claim_cost,
    sum(e.payer_coverage) as payer_coverage,
    sum(e.patient_responsibility) as patient_responsibility,
    count(distinct e.provider_key) as provider_count
from {{ ref('stg_careflow__organizations') }} o
left join {{ ref('stg_careflow__encounters') }} e on o.organization_key = e.organization_key
left join {{ ref('stg_careflow__dates') }} d on e.encounter_date_key = d.date_key
group by o.organization_key, o.organization_name, e.encounter_date_key, d.full_date, e.encounter_class
