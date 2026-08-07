{% macro safe_divide(numerator, denominator) -%}
    case
        when {{ denominator }} is null or {{ denominator }} = 0 then null
        else {{ numerator }}::numeric / {{ denominator }}::numeric
    end
{%- endmacro %}

{% macro percentage(numerator, denominator) -%}
    ({{ safe_divide(numerator, denominator) }}) * 100
{%- endmacro %}

{% macro round_currency(column_name) -%}
    round({{ column_name }}::numeric, 2)
{%- endmacro %}
