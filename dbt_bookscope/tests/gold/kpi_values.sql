{{ config(tags=['gold']) }}

with overview_expected as (
  select o.month_start,o.format_key,
         sum(o.checkouts::numeric) over (partition by o.month_start) as month_total,
         p.checkouts as previous,
         y.checkouts as year_previous,
         (select sum(x.checkouts::numeric) from {{ ref('monthly_overview') }} x
          where x.format_key=o.format_key and x.month_start between
                (o.month_start - interval '2 months')::date and o.month_start) as three_total,
         (select count(*) from {{ ref('monthly_overview') }} x
          where x.format_key=o.format_key and x.month_start between
                (o.month_start - interval '2 months')::date and o.month_start) as three_count
  from {{ ref('monthly_overview') }} o
  left join {{ ref('monthly_overview') }} p on p.format_key=o.format_key
    and p.month_start=(o.month_start - interval '1 month')::date
  left join {{ ref('monthly_overview') }} y on y.format_key=o.format_key
    and y.month_start=(o.month_start - interval '12 months')::date
), overview_bad as (
  select 'overview' as reason from {{ ref('monthly_overview') }} o
  join overview_expected e using (month_start,format_key)
  where (e.month_total > 0 and (o.format_share is null or abs(o.format_share - o.checkouts::numeric/e.month_total) > 0.000000000001))
     or (e.previous > 0 and (o.mom_change is null or abs(o.mom_change - (o.checkouts::numeric-e.previous)/e.previous) > 0.000000000001))
     or (e.year_previous > 0 and (o.yoy_change is null or abs(o.yoy_change - (o.checkouts::numeric-e.year_previous)/e.year_previous) > 0.000000000001))
     or (e.previous is null and o.mom_change is not null)
     or (e.year_previous is null and o.yoy_change is not null)
     or (e.three_count=3 and o.rolling_3m_sum is distinct from e.three_total::bigint)
     or (e.three_count<3 and o.rolling_3m_sum is not null)
), detail_metrics as (
  select month_start,format_key,sum(checkouts::numeric) as checkouts,
         count(distinct book_record_key) as active_books
  from {{ ref('book_monthly_stats') }} group by 1,2
), scope_bad as (
  select 'scope' as reason from {{ ref('monthly_overview') }} o
  left join detail_metrics d using (month_start,format_key)
  where o.detail_checkouts::numeric is distinct from coalesce(d.checkouts,0)
     or o.active_book_records is distinct from coalesce(d.active_books,0)
     or (o.checkouts>0 and (o.cohort_coverage is null
         or abs(o.cohort_coverage-coalesce(d.checkouts,0)/o.checkouts)>0.000000000001))
), feature_expected as (
  select f.book_record_key,f.month_start,f.rolling_3m_sum,f.book_growth_3m,
         count(x.checkouts) filter (where x.month_start >= (f.month_start - interval '2 months')::date) as recent_count,
         sum(x.checkouts::numeric) filter (where x.month_start >= (f.month_start - interval '2 months')::date) as recent_total,
         count(x.checkouts) filter (where x.month_start < (f.month_start - interval '2 months')::date) as previous_count,
         sum(x.checkouts::numeric) filter (where x.month_start < (f.month_start - interval '2 months')::date) as previous_total
  from {{ ref('book_monthly_features') }} f
  left join {{ ref('book_monthly_features') }} x
    on x.book_record_key=f.book_record_key
   and x.month_start between (f.month_start - interval '5 months')::date
                         and f.month_start
  group by f.book_record_key,f.month_start,f.rolling_3m_sum,f.book_growth_3m
), features_bad as (
  select 'features' as reason from feature_expected f
  where (recent_count=3 and rolling_3m_sum is distinct from recent_total::bigint)
     or (recent_count<3 and rolling_3m_sum is not null)
     or (recent_count=3 and previous_count=3 and previous_total>0
         and (book_growth_3m is null or abs(book_growth_3m-(recent_total-previous_total)/previous_total)>0.000000000001))
     or ((recent_count<3 or previous_count<3 or previous_total=0)
         and book_growth_3m is not null)
)
select * from overview_bad union all select * from scope_bad
union all select * from features_bad
