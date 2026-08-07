select
    date_key,
    full_date,
    day,
    day_name,
    week_of_year,
    month,
    month_name,
    quarter,
    year,
    year_month,
    is_weekend
from {{ source('careflow_dim', 'dim_date') }}
