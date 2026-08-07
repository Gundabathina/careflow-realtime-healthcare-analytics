-- Per-patient clinical activity rollup. Each source is pre-aggregated to
-- one row per patient BEFORE joining -- joining the six raw one-to-many
-- fact tables directly (conditions x procedures x medications x
-- observations x immunizations x encounters) would multiply their row
-- counts together per patient (a patient with 500 observations and 50
-- encounters alone produces 25,000 joined rows before any other table is
-- even considered), which is both wrong and prohibitively slow.
with conditions_agg as (
    select
        patient_key,
        count(distinct condition_event_key) as condition_count,
        count(distinct case when is_active then condition_event_key end) as active_condition_count
    from {{ ref('stg_careflow__conditions') }}
    group by patient_key
),
procedures_agg as (
    select patient_key, count(distinct procedure_event_key) as procedure_count
    from {{ ref('stg_careflow__procedures') }}
    group by patient_key
),
medications_agg as (
    select patient_key, count(distinct medication_event_key) as medication_count
    from {{ ref('stg_careflow__medications') }}
    group by patient_key
),
observations_agg as (
    select patient_key, count(distinct observation_key) as observation_count
    from {{ ref('stg_careflow__observations') }}
    group by patient_key
),
immunizations_agg as (
    select patient_key, count(distinct immunization_key) as immunization_count
    from {{ ref('stg_careflow__immunizations') }}
    group by patient_key
),
encounters_agg as (
    select patient_key, count(distinct encounter_key) as distinct_encounter_count
    from {{ ref('stg_careflow__encounters') }}
    group by patient_key
)
select
    p.patient_key,
    coalesce(c.condition_count, 0) as condition_count,
    coalesce(c.active_condition_count, 0) as active_condition_count,
    coalesce(pr.procedure_count, 0) as procedure_count,
    coalesce(m.medication_count, 0) as medication_count,
    coalesce(ob.observation_count, 0) as observation_count,
    coalesce(im.immunization_count, 0) as immunization_count,
    coalesce(e.distinct_encounter_count, 0) as distinct_encounter_count
from {{ ref('stg_careflow__patients') }} p
left join conditions_agg c on p.patient_key = c.patient_key
left join procedures_agg pr on p.patient_key = pr.patient_key
left join medications_agg m on p.patient_key = m.patient_key
left join observations_agg ob on p.patient_key = ob.patient_key
left join immunizations_agg im on p.patient_key = im.patient_key
left join encounters_agg e on p.patient_key = e.patient_key
