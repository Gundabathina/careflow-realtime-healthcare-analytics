-- Readmission events joined with safe patient demographics only
-- (age_group, gender) -- no restricted PII.
select
    re.patient_key,
    p.age_group,
    p.gender,
    re.index_encounter_key,
    re.index_encounter_class,
    re.index_discharge_timestamp,
    re.next_encounter_key,
    re.next_encounter_class,
    re.next_encounter_timestamp,
    re.days_to_readmission,
    re.readmitted_within_7_days,
    re.readmitted_within_14_days,
    re.readmitted_within_30_days
from {{ ref('int_readmission_events') }} re
left join {{ ref('stg_careflow__patients') }} p on re.patient_key = p.patient_key
