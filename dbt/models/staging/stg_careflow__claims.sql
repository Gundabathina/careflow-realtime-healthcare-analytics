select
    claim_key,
    claim_id,
    patient_key,
    patient_key_is_missing,
    encounter_key,
    provider_key,
    provider_key_is_missing,
    payer_key,
    payer_key_is_missing,
    service_date_key,
    claim_status,
    outstanding_amount,
    claim_type
from {{ source('careflow_fact', 'fact_claim') }}
