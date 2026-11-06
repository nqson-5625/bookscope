{{ config(materialized='table', tags=['gold']) }}

select m.month_start::date as month_start,
       extract(year from m.month_start)::smallint as year_number,
       extract(month from m.month_start)::smallint as month_number,
       to_char(m.month_start, 'YYYY-MM') as year_month
from generate_series('2023-01-01'::date, '2025-12-01'::date,
                     interval '1 month') as m(month_start)
