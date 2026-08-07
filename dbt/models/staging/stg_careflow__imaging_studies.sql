-- Grain is the composite (study_id, series_uid, instance_uid), matching
-- imaging_study_key -- study_id alone is NOT row-level unique (Phase 2F
-- / 3A finding, carried through to the warehouse; see sources.yml).
select
    imaging_study_key,
    study_id,
    series_uid,
    instance_uid,
    patient_key,
    patient_key_is_missing,
    encounter_key,
    study_date_key,
    bodysite_code,
    modality_code,
    sop_code,
    procedure_code
from {{ source('careflow_fact', 'fact_imaging_study') }}
