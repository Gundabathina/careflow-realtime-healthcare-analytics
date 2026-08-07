select
    organization_key,
    organization_name,
    encounter_date_key,
    encounter_date,
    encounter_class,
    encounter_count,
    unique_patients,
    average_duration_minutes,
    median_duration_minutes,
    inpatient_count,
    emergency_count,
    total_claim_cost,
    payer_coverage,
    patient_responsibility,
    provider_count
from {{ ref('int_organization_activity') }}
