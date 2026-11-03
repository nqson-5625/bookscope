{{ config(materialized='view') }}

select d.*
from {{ ref('detail_records') }} as d
join {{ ref('int_snapshot_selection') }} as s
  on s.snapshot_id = d.snapshot_id and s.selection_status = 'READY'
join {{ ref('snapshot_quality') }} as q
  on q.snapshot_id = d.snapshot_id and q.contract_version = d.contract_version
cross join {{ ref('pipeline_gate') }} as gate
where d.contract_version = '{{ var("silver_contract_version") }}'
  and q.status = 'PASS' and gate.status = 'PASS'
