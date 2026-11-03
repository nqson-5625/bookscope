{{ config(materialized='view') }}

select g.*
from {{ ref('overview_groups') }} as g
join {{ ref('int_snapshot_selection') }} as s
  on s.snapshot_id = g.snapshot_id and s.selection_status = 'READY'
join {{ ref('snapshot_quality') }} as q
  on q.snapshot_id = g.snapshot_id and q.contract_version = g.contract_version
cross join {{ ref('pipeline_gate') }} as gate
where g.contract_version = '{{ var("silver_contract_version") }}'
  and q.status = 'PASS' and gate.status = 'PASS'
