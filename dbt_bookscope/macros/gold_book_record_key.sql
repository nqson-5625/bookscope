{% macro gold_book_record_key(title, creator, material, checkout_type) -%}
encode(sha256(convert_to(jsonb_build_array(
    '{{ var("gold_identity_version") }}', {{ title }}, {{ creator }},
    case when {{ material }} = 'Ebook' then 'EBOOK' else {{ material }} end,
    {{ checkout_type }}
)::text, 'UTF8')), 'hex')
{%- endmacro %}
