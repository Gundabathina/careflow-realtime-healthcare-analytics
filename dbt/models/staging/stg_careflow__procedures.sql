select
    procedure_event_key,
    patient_key,
    patient_key_is_missing,
    encounter_key,
    procedure_key,
    procedure_key_is_missing,
    start_date_key,
    stop_date_key,
    procedure_duration_minutes,
    base_cost,
    reason_code,
    reason_description
from {{ source('careflow_fact', 'fact_procedure') }}
