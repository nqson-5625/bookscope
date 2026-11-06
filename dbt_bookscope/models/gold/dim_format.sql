{{ config(materialized='table', tags=['gold']) }}

select format_key, format_label
from (values ('EBOOK'::text, 'Ebook'::text),
             ('PRINT_BOOK'::text, 'Print book'::text)) as f(format_key, format_label)
