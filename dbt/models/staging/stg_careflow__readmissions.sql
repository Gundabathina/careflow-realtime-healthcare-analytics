-- Staged from the Python Gold readmission mart (careflow_mart.mart_readmission),
-- used as the reconciliation baseline for dbt's independently-recomputed
-- int_readmission_events (see dbt/tests/reconcile_readmission_counts.sql).
select
    patient_key,
    index_encounter_key,
    index_encounter_class,
    index_discharge_timestamp,
    next_encounter_key,
    next_encounter_class,
    next_encounter_timestamp,
    days_to_readmission,
    readmitted_within_7_days,
    readmitted_within_14_days,
    readmitted_within_30_days
from {{ source('careflow_mart', 'mart_readmission') }}
