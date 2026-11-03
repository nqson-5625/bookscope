{{ config(materialized='view') }}

with expected as (
  select periods.month_start::date as month_start, streams.stream, streams.scope_id
  from generate_series(
    '{{ var("silver_period_start") }}'::date,
    '{{ var("silver_period_end") }}'::date,
    interval '1 month'
  ) as periods(month_start)
  cross join (values
    ('monthly_overview', 'ALL_ELIGIBLE_AGG'),
    ('book_details', 'SELECTED_DETAIL')
  ) as streams(stream, scope_id)
), ranked as (
  select s.*, row_number() over (
    partition by s.stream, s.scope_id, s.period
    order by s.completed_at desc, s.extracted_at desc, s.snapshot_id desc
  ) as snapshot_rank
  from {{ source('ops', 'data_snapshots') }} as s
  where s.status = 'COMPLETE'
), latest as (
  select * from ranked where snapshot_rank = 1
)
select
  e.month_start,
  e.stream,
  e.scope_id,
  s.snapshot_id,
  s.mapping_version,
  s.manifest_sha256,
  s.manifest_path,
  s.completed_at,
  s.extracted_at,
  s.row_count as expected_rows,
  s.source_row_count as expected_source_row_count,
  s.checkouts as expected_checkouts,
  r.loaded_rows,
  r.loaded_checkouts,
  r.loaded_source_row_count,
  r.ready_for_transform,
  case
    when s.snapshot_id is null then 'MISSING_SNAPSHOT'
    when s.mapping_version <> '{{ var("silver_mapping_version") }}' then 'VERSION_MISMATCH'
    when r.ready_for_transform is distinct from true then 'SNAPSHOT_NOT_READY'
    else 'READY'
  end as selection_status
from expected as e
left join latest as s
  on s.period = e.month_start and s.stream = e.stream and s.scope_id = e.scope_id
left join {{ source('bronze', 'snapshot_load_reconciliation') }} as r
  on r.snapshot_id = s.snapshot_id
