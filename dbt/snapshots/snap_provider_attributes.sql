{#
  Slowly-changing provider attributes (organization affiliation,
  speciality). No reliable "updated_at" column exists on dim_provider, so
  the check strategy is used against the specific columns that matter for
  history tracking, rather than every column. Dimensions only -- never
  snapshot facts.
#}
{% snapshot snap_provider_attributes %}
{{
    config(
        target_schema='careflow_dbt_snapshots',
        unique_key='provider_key',
        strategy='check',
        check_cols=['organization_id', 'speciality'],
    )
}}
select
    provider_key,
    provider_id,
    organization_id,
    speciality
from {{ source('careflow_dim', 'dim_provider') }}
{% endsnapshot %}
