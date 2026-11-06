{{ config(tags=['gold']) }}

with overview_expected as (
  select s.snapshot_id,s.source_row_number,s.scope_id,s.month_start,
         s.format_key,s.checkouts,s.source_row_count
  from {{ source('silver','dq_approved_overview_groups') }} s
  join ops.gold_build_pins p on p.snapshot_id=s.snapshot_id
    and p.stream='monthly_overview' and p.month_start=s.month_start
  where p.build_id='{{ var("gold_build_id", "00000000-0000-0000-0000-000000000000") }}'::uuid
), overview_diff as (
  (select * from overview_expected except all
   select snapshot_id,source_row_number,scope_id,month_start,format_key,
          checkouts,source_row_count from {{ ref('overview_source_lineage') }})
  union all
  (select snapshot_id,source_row_number,scope_id,month_start,format_key,
          checkouts,source_row_count from {{ ref('overview_source_lineage') }}
   except all select * from overview_expected)
), detail_expected as (
  select s.snapshot_id,s.source_row_number,s.scope_id,s.month_start,
         s.format_key,
         {{ gold_book_record_key('s.title_raw', 's.creator_raw', 's.materialtype_raw', 's.checkouttype_raw') }} as book_record_key,
         s.checkouts
  from {{ source('silver','dq_approved_detail_records') }} s
  join ops.gold_build_pins p on p.snapshot_id=s.snapshot_id
    and p.stream='book_details' and p.month_start=s.month_start
  where p.build_id='{{ var("gold_build_id", "00000000-0000-0000-0000-000000000000") }}'::uuid
), detail_diff as (
  (select * from detail_expected except all
   select snapshot_id,source_row_number,scope_id,month_start,format_key,
          book_record_key,checkouts from {{ ref('book_source_lineage') }})
  union all
  (select snapshot_id,source_row_number,scope_id,month_start,format_key,
          book_record_key,checkouts from {{ ref('book_source_lineage') }}
   except all select * from detail_expected)
)
select 'overview' as relation_name from overview_diff
union all
select 'detail' from detail_diff
