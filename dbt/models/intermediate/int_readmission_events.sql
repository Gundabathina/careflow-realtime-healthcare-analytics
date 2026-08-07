-- Readmission logic recomputed independently in SQL, for reconciliation
-- against the Python Gold mart_readmission (staged as
-- stg_careflow__readmissions). A qualifying readmission is a subsequent
-- inpatient/emergency encounter starting within 30 days after the
-- previous qualifying encounter's discharge. Uses lead() so an encounter
-- can never be both index and next in the same row. A next-encounter
-- timestamp before discharge (overlapping encounters) yields a null
-- days_to_readmission, never a negative number.
{% set qualifying_classes = var('readmission_qualifying_encounter_classes') %}

with qualifying as (
    select
        encounter_key,
        patient_key,
        encounter_class,
        start_timestamp,
        coalesce(stop_timestamp, start_timestamp) as discharge_timestamp
    from {{ ref('stg_careflow__encounters') }}
    where encounter_class in ({{ "'" ~ qualifying_classes | join("', '") ~ "'" }})
      and start_timestamp is not null
),
sequenced as (
    select
        *,
        lead(encounter_key) over (partition by patient_key order by start_timestamp) as next_encounter_key,
        lead(encounter_class) over (partition by patient_key order by start_timestamp) as next_encounter_class,
        lead(start_timestamp) over (partition by patient_key order by start_timestamp) as next_encounter_timestamp
    from qualifying
),
final as (
    select
        patient_key,
        encounter_key as index_encounter_key,
        encounter_class as index_encounter_class,
        discharge_timestamp as index_discharge_timestamp,
        next_encounter_key,
        next_encounter_class,
        next_encounter_timestamp,
        case
            when next_encounter_timestamp is not null and next_encounter_timestamp >= discharge_timestamp
                then extract(epoch from (next_encounter_timestamp - discharge_timestamp)) / 86400.0
            else null
        end as days_to_readmission
    from sequenced
)
select
    patient_key,
    index_encounter_key,
    index_encounter_class,
    index_discharge_timestamp,
    next_encounter_key,
    next_encounter_class,
    next_encounter_timestamp,
    days_to_readmission,
    (days_to_readmission is not null and days_to_readmission <= 7) as readmitted_within_7_days,
    (days_to_readmission is not null and days_to_readmission <= 14) as readmitted_within_14_days,
    (days_to_readmission is not null and days_to_readmission <= 30) as readmitted_within_30_days
from final
