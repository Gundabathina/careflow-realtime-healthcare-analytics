select
    immunization_key,
    patient_key,
    patient_key_is_missing,
    encounter_key,
    immunization_date_key,
    code,
    description,
    base_cost
from {{ source('careflow_fact', 'fact_immunization') }}
