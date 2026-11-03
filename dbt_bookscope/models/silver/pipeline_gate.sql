{{ config(materialized='view') }}

with counts as (
  select
    count(*)::bigint as expected_period_scopes,
    count(*) filter (
      where s.selection_status = 'READY' and q.status = 'PASS'
    )::bigint as passed_period_scopes,
    count(*) filter (where s.snapshot_id is null)::bigint as missing_snapshots,
    count(*) filter (
      where s.snapshot_id is not null and (s.selection_status <> 'READY' or q.status is distinct from 'PASS')
    )::bigint as blocked_snapshots
  from {{ ref('int_snapshot_selection') }} as s
  left join {{ ref('snapshot_quality') }} as q
    on q.snapshot_id = s.snapshot_id
    and q.contract_version = '{{ var("silver_contract_version") }}'
)
select
  '{{ var("silver_contract_version") }}'::text as contract_version,
  expected_period_scopes, passed_period_scopes, missing_snapshots, blocked_snapshots,
  case
    when expected_period_scopes = (
      select count(*) * 2
      from generate_series(
        '{{ var("silver_period_start") }}'::date,
        '{{ var("silver_period_end") }}'::date,
        interval '1 month'
      ) as periods(month_start)
    ) and expected_period_scopes = passed_period_scopes
    then 'PASS' else 'BLOCK'
  end as status
from counts
