-- Per-patient encounter sequencing via window functions: previous/next
-- encounter, days since/to, sequence number, first/latest flags.
with base as (
    select
        encounter_key,
        patient_key,
        encounter_class,
        start_timestamp,
        stop_timestamp
    from {{ ref('stg_careflow__encounters') }}
    where start_timestamp is not null
),
sequenced as (
    select
        *,
        lag(encounter_key) over (partition by patient_key order by start_timestamp) as previous_encounter_key,
        lag(stop_timestamp) over (partition by patient_key order by start_timestamp) as previous_discharge,
        lead(encounter_key) over (partition by patient_key order by start_timestamp) as next_encounter_key,
        lead(start_timestamp) over (partition by patient_key order by start_timestamp) as next_encounter_timestamp,
        row_number() over (partition by patient_key order by start_timestamp) as encounter_sequence_number,
        count(*) over (partition by patient_key) as total_encounters_for_patient
    from base
)
select
    encounter_key,
    patient_key,
    encounter_class,
    start_timestamp,
    stop_timestamp,
    previous_encounter_key,
    previous_discharge,
    next_encounter_key,
    next_encounter_timestamp,
    -- Negative values here mean overlapping/back-to-back encounters in the
    -- source data (the next encounter started before the previous one's
    -- recorded discharge) -- "days between encounters" is not a meaningful
    -- concept there, so it is left null rather than negative (same
    -- treatment as int_readmission_events.days_to_readmission).
    case when start_timestamp >= previous_discharge
         then extract(epoch from (start_timestamp - previous_discharge)) / 86400.0
    end as days_since_previous_encounter,
    case when next_encounter_timestamp >= coalesce(stop_timestamp, start_timestamp)
         then extract(epoch from (next_encounter_timestamp - coalesce(stop_timestamp, start_timestamp))) / 86400.0
    end as days_to_next_encounter,
    encounter_sequence_number,
    (encounter_sequence_number = 1) as is_first_encounter,
    (encounter_sequence_number = total_encounters_for_patient) as is_latest_encounter
from sequenced
