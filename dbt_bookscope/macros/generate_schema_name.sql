{% macro generate_schema_name(custom_schema_name, node) -%}
  {%- set default_schema = target.schema -%}
  {%- if custom_schema_name is none -%}
    {{ default_schema }}
  {%- elif target.name == 'dev' and default_schema == 'silver'
      and custom_schema_name | trim == 'gold'
      and node.package_name == 'dbt_bookscope'
      and node.fqn[1] == 'gold' -%}
    gold
  {%- else -%}
    {{ default_schema }}_{{ custom_schema_name | trim }}
  {%- endif -%}
{%- endmacro %}
