-- Incrementally materialized: unique_key=encounter_key means late-arriving
-- or corrected encounters (existing encounter_key with new data) are
-- upserted via delete+insert, never duplicated. The incremental predicate
-- uses start_timestamp (a real business date) since Gold facts carry no
-- separate load timestamp. Full refresh (`dbt run --full-refresh` or
-- scripts/run_dbt.py full-refresh) rebuilds the whole table from source.
{{
  config(
    materialized='incremental',
    unique_key='encounter_key',
    on_schema_change='sync_all_columns'
  )
}}

select
    encounter_key,
    encounter_id,
    patient_key,
    provider_key,
    organization_key,
    payer_key,
    encounter_date_key,
    encounter_date,
    year_month,
    encounter_class,
    encounter_duration_minutes,
    total_claim_cost,
    payer_coverage,
    patient_responsibility,
    patient_responsibility_is_negative,
    is_inpatient,
    is_emergency,
    start_timestamp,
    stop_timestamp
from {{ ref('int_encounters_enriched') }}

{% if is_incremental() %}
where start_timestamp > (select coalesce(max(start_timestamp), '1900-01-01'::timestamptz) from {{ this }})
{% endif %}
