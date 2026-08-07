{#
  Slowly-changing organization utilization/revenue attributes. Check
  strategy: no reliable updated_at column on dim_organization.
#}
{% snapshot snap_organization_attributes %}
{{
    config(
        target_schema='careflow_dbt_snapshots',
        unique_key='organization_key',
        strategy='check',
        check_cols=['utilization', 'revenue'],
    )
}}
select
    organization_key,
    organization_id,
    utilization,
    revenue
from {{ source('careflow_dim', 'dim_organization') }}
{% endsnapshot %}
