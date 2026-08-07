{#
  Schema naming macro (overrides dbt's default).

  Without this override, a model configured with `schema: careflow_dbt_mart`
  would be materialized into `<target_schema>_careflow_dbt_mart` (dbt's
  default behavior concatenates the target schema as a prefix). CareFlow
  wants exact, predictable schema names -- careflow_dbt_staging,
  careflow_dbt_intermediate, careflow_dbt_mart, careflow_dbt_seeds,
  careflow_dbt_snapshots -- regardless of which target/profile is active.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
