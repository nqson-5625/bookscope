{{ config(materialized='view') }}

select
  v.snapshot_id, v.stream, v.scope_id, v.month_start,
  v.contract_version, warning.warning_code, count(*)::bigint as warning_rows
from {{ ref('int_validated_rows') }} as v
cross join lateral unnest(v.warning_codes) as warning(warning_code)
group by 1, 2, 3, 4, 5, 6
