{#
  The list of column names that must never appear in any public-facing
  model. Shared by dbt/tests/no_restricted_pii_in_public_models.sql and
  documented here as the single source of truth.
#}
{% macro restricted_pii_columns() %}
    {{ return(['ssn', 'passport', 'drivers', 'driver_license', 'first', 'middle', 'last',
               'address', 'street_address', 'latitude', 'longitude']) }}
{% endmacro %}

{#
  Returns rows (schema, table, column) for any restricted PII column
  found on the given relation. An empty result means the relation is
  clean. Used by the singular PII test and reusable elsewhere.
#}
{% macro assert_no_restricted_pii_columns(schema_name, table_name) %}
    select table_schema, table_name, column_name
    from information_schema.columns
    where table_schema = '{{ schema_name }}'
      and table_name = '{{ table_name }}'
      and lower(column_name) in ({{ "'" ~ restricted_pii_columns() | join("', '") ~ "'" }})
{% endmacro %}
