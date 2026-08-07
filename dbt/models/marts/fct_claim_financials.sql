select
    claim_key,
    claim_id,
    patient_key,
    encounter_key,
    provider_key,
    payer_key,
    insurance_information_present,
    outstanding_amount,
    encounter_payer_coverage,
    encounter_patient_responsibility,
    claim_status,
    claim_type,
    service_date_key,
    service_month
from {{ ref('int_claim_financials') }}
