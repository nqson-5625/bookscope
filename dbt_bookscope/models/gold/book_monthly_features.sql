{{ config(materialized='table', tags=['gold']) }}

with spine as (
  select '{{ var("gold_stage_token", "0000000000000000000000000000000000000000000000000000000000000000") }}'::text as data_version,
         b.book_record_key, b.format_key, m.month_start,
         s.checkouts, (s.book_record_key is not null) as observed
  from {{ ref('dim_book_record') }} b
  cross join {{ ref('dim_month') }} m
  left join {{ ref('book_monthly_stats') }} s
    on s.book_record_key = b.book_record_key and s.month_start = m.month_start
), windows as (
  select p.*,
         lag(checkouts) over w as previous_checkouts,
         lag(checkouts, 12) over w as year_previous_checkouts,
         count(checkouts) over (partition by book_record_key order by month_start rows between 2 preceding and current row) as recent_count,
         sum(checkouts::numeric) over (partition by book_record_key order by month_start rows between 2 preceding and current row) as recent_sum,
         count(checkouts) over (partition by book_record_key order by month_start rows between 5 preceding and 3 preceding) as earlier_count,
         sum(checkouts::numeric) over (partition by book_record_key order by month_start rows between 5 preceding and 3 preceding) as earlier_sum
  from spine p
  window w as (partition by book_record_key order by month_start)
), calculated as (
  select w.*,
         case when recent_count = 3 then recent_sum::bigint end as rolling_3m_sum,
         case when recent_count = 3 then recent_sum / 3 end as rolling_3m_avg,
         case when observed and previous_checkouts > 0
              then (checkouts::numeric - previous_checkouts) / previous_checkouts end as mom_change,
         case when observed and year_previous_checkouts > 0
              then (checkouts::numeric - year_previous_checkouts) / year_previous_checkouts end as yoy_change,
         case when recent_count = 3 and earlier_count = 3 and earlier_sum > 0
              then (recent_sum - earlier_sum) / earlier_sum end as book_growth_3m
  from windows w
)
select data_version, book_record_key, month_start, format_key, observed,
       checkouts, rolling_3m_sum, rolling_3m_avg, mom_change, yoy_change,
       book_growth_3m,
       jsonb_build_object(
         'observation', case when observed then 'OBSERVED' else 'MISSING' end,
         'rolling_3m', case when recent_count = 3 then 'OK' else 'INSUFFICIENT_HISTORY' end,
         'mom_change', case
           when not observed or previous_checkouts is null then 'INSUFFICIENT_HISTORY'
           when previous_checkouts = 0 and checkouts > 0 then 'NEW_ACTIVITY'
           when previous_checkouts = 0 then 'NO_ACTIVITY' else 'OK' end,
         'yoy_change', case
           when not observed or year_previous_checkouts is null then 'INSUFFICIENT_HISTORY'
           when year_previous_checkouts = 0 and checkouts > 0 then 'NEW_ACTIVITY'
           when year_previous_checkouts = 0 then 'NO_ACTIVITY' else 'OK' end,
         'book_growth_3m', case
           when recent_count <> 3 or earlier_count <> 3 then 'INSUFFICIENT_HISTORY'
           when earlier_sum = 0 and recent_sum > 0 then 'NEW_ACTIVITY'
           when earlier_sum = 0 then 'NO_ACTIVITY' else 'OK' end
       ) as metric_status
from calculated
