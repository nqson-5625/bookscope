{{ config(materialized='table', tags=['gold']) }}

with aggregated as (
  select data_version, scope_id, month_start, format_key,
         snapshot_id as source_snapshot_id,
         sum(checkouts::numeric)::bigint as checkouts,
         sum(source_row_count::numeric)::bigint as source_row_count,
         count(*)::bigint as source_group_count
  from {{ ref('overview_source_lineage') }}
  group by data_version, scope_id, month_start, format_key, snapshot_id
), detail as (
  select month_start, format_key,
         sum(checkouts::numeric)::bigint as detail_checkouts,
         count(distinct book_record_key)::integer as active_book_records
  from {{ ref('book_monthly_stats') }}
  group by month_start, format_key
), joined as (
  select a.*, coalesce(d.detail_checkouts, 0)::bigint as detail_checkouts,
         coalesce(d.active_book_records, 0)::integer as active_book_records,
         sum(a.checkouts::numeric) over (partition by a.month_start) as monthly_total,
         lag(a.checkouts) over w as previous_checkouts,
         lag(a.month_start) over w as previous_month,
         lag(a.checkouts, 12) over w as year_previous_checkouts,
         lag(a.month_start, 12) over w as year_previous_month,
         lag(a.month_start, 2) over w as two_months_before,
         count(*) over (partition by a.format_key order by a.month_start rows between 2 preceding and current row) as rolling_count,
         sum(a.checkouts::numeric) over (partition by a.format_key order by a.month_start rows between 2 preceding and current row) as rolling_total
  from aggregated a left join detail d using (month_start, format_key)
  window w as (partition by a.format_key order by a.month_start)
), calculated as (
  select j.*,
         case when monthly_total > 0 then checkouts::numeric / monthly_total end as format_share,
         case when checkouts > 0 then detail_checkouts::numeric / checkouts end as cohort_coverage,
         case when previous_month = (month_start - interval '1 month')::date
                       and previous_checkouts > 0
              then (checkouts::numeric - previous_checkouts) / previous_checkouts end as mom_change,
         case when year_previous_month = (month_start - interval '12 months')::date
                       and year_previous_checkouts > 0
              then (checkouts::numeric - year_previous_checkouts) / year_previous_checkouts end as yoy_change,
         case when rolling_count = 3
                       and two_months_before = (month_start - interval '2 months')::date
              then rolling_total::bigint end as rolling_3m_sum,
         case when rolling_count = 3
                       and two_months_before = (month_start - interval '2 months')::date
              then rolling_total / 3 end as rolling_3m_avg
  from joined j
)
select data_version, scope_id, month_start, format_key, source_snapshot_id,
       checkouts, source_row_count, source_group_count,
       detail_checkouts, active_book_records, cohort_coverage,
       format_share, mom_change, yoy_change, rolling_3m_sum, rolling_3m_avg,
       jsonb_build_object(
         'format_share', case when monthly_total > 0 then 'OK' else 'ZERO_DENOMINATOR' end,
         'cohort_coverage', case when checkouts > 0 then 'OK' else 'ZERO_DENOMINATOR' end,
         'mom_change', case
           when previous_month is null or previous_month <> (month_start - interval '1 month')::date then 'INSUFFICIENT_HISTORY'
           when previous_checkouts = 0 and checkouts > 0 then 'NEW_ACTIVITY'
           when previous_checkouts = 0 then 'NO_ACTIVITY' else 'OK' end,
         'yoy_change', case
           when year_previous_month is null or year_previous_month <> (month_start - interval '12 months')::date then 'INSUFFICIENT_HISTORY'
           when year_previous_checkouts = 0 and checkouts > 0 then 'NEW_ACTIVITY'
           when year_previous_checkouts = 0 then 'NO_ACTIVITY' else 'OK' end,
         'rolling_3m', case when rolling_3m_sum is null then 'INSUFFICIENT_HISTORY' else 'OK' end
       ) as metric_status
from calculated
