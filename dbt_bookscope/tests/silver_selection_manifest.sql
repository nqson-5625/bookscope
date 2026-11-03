with current_run as (
  select * from {{ ref('run_snapshot_selection') }}
  where invocation_id = (
    select invocation_id from {{ ref('run_snapshot_selection') }}
    order by selected_at desc, invocation_id desc limit 1
  )
), expected as (
  select stream, scope_id, month_start, snapshot_id, selection_status
  from {{ ref('int_snapshot_selection') }}
)
select 'missing_or_changed' as issue, e.stream, e.month_start
from expected as e
left join current_run as r
  on r.stream = e.stream and r.scope_id = e.scope_id and r.month_start = e.month_start
where r.invocation_id is null or r.snapshot_id is distinct from e.snapshot_id
  or r.selection_status <> e.selection_status
union all
select 'extra_or_duplicate', r.stream, r.month_start
from current_run as r
left join expected as e
  on e.stream = r.stream and e.scope_id = r.scope_id and e.month_start = r.month_start
group by r.stream, r.scope_id, r.month_start
having count(*) <> 1 or count(e.stream) <> 1
