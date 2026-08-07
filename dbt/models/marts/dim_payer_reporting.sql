select
    payer_key,
    payer_name,
    ownership,
    state_headquartered,
    revenue,
    unique_customers,
    member_months
from {{ ref('stg_careflow__payers') }}
