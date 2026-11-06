{{ config(materialized='table', tags=['gold']) }}

with selected as (
  select d.* from {{ source('silver', 'dq_approved_detail_records') }} d
  join ops.gold_build_pins p on p.snapshot_id = d.snapshot_id
    and p.stream = 'book_details' and p.scope_id = d.scope_id
    and p.month_start = d.month_start
  where p.build_id = '{{ var("gold_build_id", "00000000-0000-0000-0000-000000000000") }}'::uuid
), keyed as (
  select {{ gold_book_record_key('title_raw', 'creator_raw', 'materialtype_raw', 'checkouttype_raw') }} as book_record_key,
         '{{ var("gold_identity_version") }}'::text as identity_version,
         title_raw, creator_raw,
         case when materialtype_raw = 'Ebook' then 'EBOOK' else materialtype_raw end as materialtype_cohort,
         checkouttype_raw, format_key,
         '{{ var("gold_cohort_sha256", "0000000000000000000000000000000000000000000000000000000000000000") }}'::text as cohort_sha256,
         btrim(title_raw) as title_display
  from selected
)
select distinct * from keyed
