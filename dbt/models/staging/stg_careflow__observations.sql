select
    observation_key,
    patient_key,
    patient_key_is_missing,
    encounter_key,
    observation_date_key,
    category,
    observation_code,
    description,
    raw_value,
    numeric_value,
    units,
    observation_type
from {{ source('careflow_fact', 'fact_observation') }}
