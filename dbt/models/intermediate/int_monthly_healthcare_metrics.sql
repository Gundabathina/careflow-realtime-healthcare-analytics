select
    d.year_month,
    count(distinct e.patient_key) as total_patients_served,
    count(distinct e.encounter_key) as total_encounters,
    sum(case when e.is_inpatient then 1 else 0 end) as inpatient_encounters,
    sum(case when e.is_emergency then 1 else 0 end) as emergency_encounters,
    avg(e.encounter_duration_minutes) as average_length_of_stay_minutes,
    sum(e.total_claim_cost) as total_claim_cost,
    sum(e.payer_coverage) as total_payer_coverage,
    sum(e.patient_responsibility) as total_patient_responsibility
from {{ ref('int_encounters_enriched') }} e
left join {{ ref('stg_careflow__dates') }} d on e.encounter_date_key = d.date_key
group by d.year_month
