{{ config(tags=['gold']) }}

with identity_errors as (
  select 'identity' as reason from {{ ref('dim_book_record') }} b
  where b.book_record_key <> {{ gold_book_record_key('b.title_raw', 'b.creator_raw', 'b.materialtype_cohort', 'b.checkouttype_raw') }}
     or (b.materialtype_cohort = 'EBOOK' and b.format_key <> 'EBOOK')
     or (b.materialtype_cohort in ('BOOK','REGPRINT') and b.format_key <> 'PRINT_BOOK')
), feature_errors as (
  select 'feature' as reason from {{ ref('book_monthly_features') }} f
  left join {{ ref('book_monthly_stats') }} s
    on s.book_record_key = f.book_record_key and s.month_start = f.month_start
  where f.observed is distinct from (s.book_record_key is not null)
     or f.checkouts is distinct from s.checkouts
     or f.format_key is distinct from s.format_key and s.book_record_key is not null
), coverage_errors as (
  select 'coverage' as reason from {{ ref('monthly_overview') }}
  where cohort_coverage < 0 or cohort_coverage > 1
     or format_share < 0 or format_share > 1
)
select * from identity_errors union all select * from feature_errors
union all select * from coverage_errors
