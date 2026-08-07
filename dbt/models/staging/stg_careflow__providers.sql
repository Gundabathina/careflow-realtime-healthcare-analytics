select
    provider_key,
    provider_id,
    organization_id,
    provider_name,
    gender,
    speciality,
    city,
    state,
    zip
from {{ source('careflow_dim', 'dim_provider') }}
