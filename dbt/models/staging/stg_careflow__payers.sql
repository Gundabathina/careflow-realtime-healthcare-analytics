select
    payer_key,
    payer_id,
    payer_name,
    ownership,
    state_headquartered,
    amount_covered,
    amount_uncovered,
    revenue,
    unique_customers,
    member_months
from {{ source('careflow_dim', 'dim_payer') }}
