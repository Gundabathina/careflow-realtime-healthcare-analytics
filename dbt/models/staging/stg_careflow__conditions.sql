select
    condition_event_key,
    patient_key,
    patient_key_is_missing,
    encounter_key,
    condition_key,
    condition_key_is_missing,
    start_date_key,
    stop_date_key,
    is_active,
    condition_duration_days
from {{ source('careflow_fact', 'fact_condition') }}
