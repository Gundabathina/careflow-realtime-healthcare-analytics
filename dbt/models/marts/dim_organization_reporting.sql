select
    organization_key,
    organization_name,
    city,
    state,
    zip,
    revenue,
    utilization
from {{ ref('stg_careflow__organizations') }}
