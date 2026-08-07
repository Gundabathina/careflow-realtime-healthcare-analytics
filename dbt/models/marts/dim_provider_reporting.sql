select
    provider_key,
    provider_name,
    speciality,
    organization_id,
    city,
    state,
    zip
from {{ ref('stg_careflow__providers') }}
