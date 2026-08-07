-- Staging model for careflow_dim.dim_patient.
-- Latitude/longitude are intentionally excluded here: restricted PII per
-- the public-model rules, even though this is not itself a public model,
-- to keep the exclusion enforced as early in the DAG as possible.
select
    patient_key,
    patient_id,
    birth_date,
    death_date,
    gender,
    race,
    ethnicity,
    marital_status,
    city,
    state,
    county,
    zip,
    income,
    healthcare_expenses,
    healthcare_coverage,
    is_deceased,
    age_at_reference_date,
    age_group,
    source_file,
    source_checksum,
    transformation_timestamp_utc
from {{ source('careflow_dim', 'dim_patient') }}
