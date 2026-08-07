-- Claim-level financial measures. fact_claim has no native payer_coverage
-- / patient_responsibility columns, so those are surfaced from the linked
-- encounter (clearly labeled, never fabricated) only when encounter_key
-- resolves. insurance_information_present reflects whether payer_key
-- resolved -- no relationship is invented when it did not.
select
    c.claim_key,
    c.claim_id,
    c.patient_key,
    c.encounter_key,
    c.provider_key,
    c.payer_key,
    (c.payer_key is not null) as insurance_information_present,
    c.outstanding_amount,
    e.payer_coverage as encounter_payer_coverage,
    e.patient_responsibility as encounter_patient_responsibility,
    c.claim_status,
    c.claim_type,
    c.service_date_key,
    to_char({{ date_key_to_date('c.service_date_key') }}, 'YYYY-MM') as service_month
from {{ ref('stg_careflow__claims') }} c
left join {{ ref('stg_careflow__encounters') }} e on c.encounter_key = e.encounter_key
