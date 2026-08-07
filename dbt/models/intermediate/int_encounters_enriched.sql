-- Encounters joined with safe patient demographics, provider, organization,
-- payer, and date context. No restricted PII is joined in (patient join
-- uses only age_group/gender from staging, never name/address/lat-lon).
select
    e.encounter_key,
    e.encounter_id,
    e.patient_key,
    p.age_group as patient_age_group,
    p.gender as patient_gender,
    e.provider_key,
    pr.speciality as provider_speciality,
    e.organization_key,
    o.organization_name,
    e.payer_key,
    pay.payer_name,
    e.encounter_class,
    e.encounter_duration_minutes,
    e.total_claim_cost,
    e.payer_coverage,
    e.patient_responsibility,
    e.patient_responsibility_is_negative,
    e.is_inpatient,
    e.is_emergency,
    e.encounter_date_key,
    d.full_date as encounter_date,
    d.year_month,
    e.start_timestamp,
    e.stop_timestamp
from {{ ref('stg_careflow__encounters') }} e
left join {{ ref('stg_careflow__patients') }} p on e.patient_key = p.patient_key
left join {{ ref('stg_careflow__providers') }} pr on e.provider_key = pr.provider_key
left join {{ ref('stg_careflow__organizations') }} o on e.organization_key = o.organization_key
left join {{ ref('stg_careflow__payers') }} pay on e.payer_key = pay.payer_key
left join {{ ref('stg_careflow__dates') }} d on e.encounter_date_key = d.date_key
