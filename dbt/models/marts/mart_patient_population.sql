-- Population-level demographics + clinical activity, built entirely from
-- the PII-safe dim_patient_safe (no patient_id, no lat/lon, no name).
select
    ps.patient_key,
    ps.age_group,
    ps.gender,
    ps.race,
    ps.ethnicity,
    ps.marital_status,
    ps.state,
    ps.county,
    ca.condition_count,
    ca.active_condition_count,
    ca.procedure_count,
    ca.medication_count,
    ca.observation_count,
    ca.immunization_count,
    ca.distinct_encounter_count
from {{ ref('dim_patient_safe') }} ps
left join {{ ref('int_patient_clinical_activity') }} ca on ps.patient_key = ca.patient_key
