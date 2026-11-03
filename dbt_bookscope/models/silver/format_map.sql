{{ config(materialized='table') }}

select materialtype_raw, format_key, '{{ var("silver_mapping_version") }}'::text as mapping_version
from (values
  ('BOOK', 'PRINT_BOOK'),
  ('REGPRINT', 'PRINT_BOOK'),
  ('LARGEPRINT', 'PRINT_BOOK'),
  ('EBOOK', 'EBOOK'),
  ('Ebook', 'EBOOK')
) as mapping(materialtype_raw, format_key)
