{#
  Reusable deterministic test helper: returns any row of `model` where
  `column_name` is negative. An empty result set means the assertion
  holds. Singular tests select from this macro rather than each
  reimplementing the same "select offending rows" pattern.
#}
{% macro assert_non_negative(model, column_name) %}
    select *
    from {{ model }}
    where {{ column_name }} < 0
{% endmacro %}

{#
  Reusable helper: rows in `model` where `first_flag_column` is true but
  `second_flag_column` is false, i.e. an implication ("A implies B") that
  does not hold. Used to check readmission-window agreement (7-day implies
  14-day implies 30-day).
#}
{% macro assert_implies(model, first_flag_column, second_flag_column) %}
    select *
    from {{ model }}
    where {{ first_flag_column }} = true
      and {{ second_flag_column }} = false
{% endmacro %}
