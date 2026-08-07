select
    pr.provider_key,
    pr.provider_name,
    pr.speciality,
    d.year_month,
    count(distinct e.encounter_key) as encounter_count,
    count(distinct e.patient_key) as unique_patients,
    count(distinct proc.procedure_event_key) as procedure_count,
    avg(e.encounter_duration_minutes) as average_encounter_duration_minutes,
    sum(e.total_claim_cost) as total_claim_cost
from {{ ref('stg_careflow__providers') }} pr
left join {{ ref('stg_careflow__encounters') }} e on pr.provider_key = e.provider_key
left join {{ ref('stg_careflow__dates') }} d on e.encounter_date_key = d.date_key
left join {{ ref('stg_careflow__procedures') }} proc on proc.encounter_key = e.encounter_key
group by pr.provider_key, pr.provider_name, pr.speciality, d.year_month
