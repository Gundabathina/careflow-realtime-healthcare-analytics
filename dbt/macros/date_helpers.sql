{% macro date_key_to_date(date_key_column) -%}
    to_date({{ date_key_column }}::text, 'YYYYMMDD')
{%- endmacro %}
