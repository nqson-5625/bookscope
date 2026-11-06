{{ config(materialized='table', tags=['gold']) }}

select '{{ var("gold_stage_token", "0000000000000000000000000000000000000000000000000000000000000000") }}'::text as data_version,
       d.snapshot_id, d.source_row_number, d.scope_id, d.month_start,
       d.format_key,
       {{ gold_book_record_key('d.title_raw', 'd.creator_raw', 'd.materialtype_raw', 'd.checkouttype_raw') }} as book_record_key,
       d.checkouts
from {{ source('silver', 'dq_approved_detail_records') }} d
join ops.gold_build_pins p on p.snapshot_id = d.snapshot_id
  and p.stream = 'book_details' and p.scope_id = d.scope_id
  and p.month_start = d.month_start
where p.build_id = '{{ var("gold_build_id", "00000000-0000-0000-0000-000000000000") }}'::uuid
