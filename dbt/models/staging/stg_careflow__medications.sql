select
    medication_event_key,
    patient_key,
    patient_key_is_missing,
    encounter_key,
    payer_key,
    payer_key_is_missing,
    medication_key,
    medication_key_is_missing,
    start_date_key,
    stop_date_key,
    base_cost,
    payer_coverage,
    total_cost,
    dispenses,
    medication_duration_days,
    is_active
from {{ source('careflow_fact', 'fact_medication') }}
