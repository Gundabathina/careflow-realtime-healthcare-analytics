select
    organization_key,
    organization_id,
    organization_name,
    city,
    state,
    zip,
    revenue,
    utilization
from {{ source('careflow_dim', 'dim_organization') }}
