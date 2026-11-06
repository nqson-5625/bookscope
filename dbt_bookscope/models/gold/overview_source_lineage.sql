{{ config(materialized='table', tags=['gold']) }}

select '{{ var("gold_stage_token", "0000000000000000000000000000000000000000000000000000000000000000") }}'::text as data_version,
       o.snapshot_id, o.source_row_number, o.scope_id, o.month_start,
       o.format_key, o.checkouts, o.source_row_count
from {{ source('silver', 'dq_approved_overview_groups') }} o
join ops.gold_build_pins p on p.snapshot_id = o.snapshot_id
  and p.stream = 'monthly_overview' and p.scope_id = o.scope_id
  and p.month_start = o.month_start
where p.build_id = '{{ var("gold_build_id", "00000000-0000-0000-0000-000000000000") }}'::uuid
