{#
  Slowly-changing payer ownership. Check strategy: no reliable
  updated_at column on dim_payer.
#}
{% snapshot snap_payer_attributes %}
{{
    config(
        target_schema='careflow_dbt_snapshots',
        unique_key='payer_key',
        strategy='check',
        check_cols=['ownership'],
    )
}}
select
    payer_key,
    payer_id,
    ownership
from {{ source('careflow_dim', 'dim_payer') }}
{% endsnapshot %}
