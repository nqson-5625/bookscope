{{ config(materialized='view') }}

with source_totals as (
  select
    snapshot_id, stream, scope_id, month_start, format_key,
    count(*)::bigint as bronze_rows,
    coalesce(sum(checkouts), 0) as bronze_checkouts,
    coalesce(sum(source_row_count) filter (where stream = 'monthly_overview'), 0)
      as bronze_source_row_count
  from {{ ref('int_validated_rows') }}
  group by 1, 2, 3, 4, 5
), valid_totals as (
  select snapshot_id, format_key, count(*)::bigint as silver_rows,
    coalesce(sum(checkouts), 0) as silver_checkouts,
    coalesce(sum(source_row_count), 0) as silver_source_row_count
  from {{ ref('overview_groups') }}
  where contract_version = '{{ var("silver_contract_version") }}'
  group by 1, 2
  union all
  select snapshot_id, format_key, count(*)::bigint, coalesce(sum(checkouts), 0),
    0::numeric
  from {{ ref('detail_records') }}
  where contract_version = '{{ var("silver_contract_version") }}'
  group by 1, 2
)
select
  s.snapshot_id, s.stream, s.scope_id, s.month_start, s.format_key,
  s.bronze_rows, coalesce(v.silver_rows, 0) as silver_rows,
  s.bronze_checkouts, coalesce(v.silver_checkouts, 0) as silver_checkouts,
  s.bronze_source_row_count,
  coalesce(v.silver_source_row_count, 0) as silver_source_row_count,
  (s.bronze_rows = coalesce(v.silver_rows, 0)
    and s.bronze_checkouts = coalesce(v.silver_checkouts, 0)
    and s.bronze_source_row_count = coalesce(v.silver_source_row_count, 0))
    as totals_match
from source_totals as s
left join valid_totals as v
  on v.snapshot_id = s.snapshot_id and v.format_key is not distinct from s.format_key
