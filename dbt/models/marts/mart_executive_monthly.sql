select
    m.year_month,
    m.total_patients_served,
    m.total_encounters,
    m.inpatient_encounters,
    m.emergency_encounters,
    m.average_length_of_stay_minutes,
    m.total_claim_cost,
    m.total_payer_coverage,
    m.total_patient_responsibility,
    coalesce(r.readmission_count_30_day, 0) as readmission_count_30_day,
    {{ safe_divide('r.readmission_count_30_day', 'r.qualifying_count') }} as readmission_rate_30_day
from {{ ref('int_monthly_healthcare_metrics') }} m
left join (
    select
        d.year_month,
        count(*) filter (where re.readmitted_within_30_days) as readmission_count_30_day,
        count(*) as qualifying_count
    from {{ ref('int_readmission_events') }} re
    left join {{ ref('stg_careflow__dates') }} d
        on re.index_discharge_timestamp::date = d.full_date::date
    group by d.year_month
) r on m.year_month = r.year_month
