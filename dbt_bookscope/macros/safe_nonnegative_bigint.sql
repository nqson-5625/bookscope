{% macro safe_nonnegative_bigint(value) -%}
(
  case
    when {{ value }} ~ '^[0-9]+$'
      and (
        length(coalesce(nullif(ltrim({{ value }}, '0'), ''), '0')) < 19
        or (
          length(coalesce(nullif(ltrim({{ value }}, '0'), ''), '0')) = 19
          and coalesce(nullif(ltrim({{ value }}, '0'), ''), '0') <= '9223372036854775807'
        )
      )
    then coalesce(nullif(ltrim({{ value }}, '0'), ''), '0')::bigint
  end
)
{%- endmacro %}
