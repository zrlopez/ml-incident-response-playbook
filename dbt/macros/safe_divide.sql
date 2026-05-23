{% macro safe_divide(numerator, denominator) %}
case when {{ denominator }} = 0 then null else {{ numerator }} / {{ denominator }} end
{% endmacro %}
