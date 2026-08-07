select
    provider_key,
    provider_name,
    speciality,
    year_month,
    encounter_count,
    unique_patients,
    procedure_count,
    average_encounter_duration_minutes,
    total_claim_cost
from {{ ref('int_provider_activity') }}
