{{ config(tags=['gold']) }}

with expected as (
  select
    (select count(*) from ops.gold_build_pins
     where build_id = '{{ var("gold_build_id", "00000000-0000-0000-0000-000000000000") }}'::uuid) as pins,
    (select count(*) from {{ ref('dim_book_record') }}) as books
), actual as (
  select (select count(*) from {{ ref('dim_month') }}) as months,
         (select count(*) from {{ ref('dim_format') }}) as formats,
         (select count(*) from {{ ref('monthly_overview') }}) as overview_rows,
         (select count(*) from {{ ref('book_monthly_features') }}) as feature_rows,
         (select count(*) from {{ ref('book_monthly_stats') }}) as book_rows,
         (select count(*) from {{ ref('overview_source_lineage') }}) as overview_lineage,
         (select count(*) from {{ ref('book_source_lineage') }}) as book_lineage
)
select * from expected cross join actual
where pins <> 72 or months <> 36 or formats <> 2 or books <> 1000
   or overview_rows <> 72 or feature_rows <> books * 36
   or book_rows = 0 or overview_lineage = 0 or book_lineage = 0
