{{ config(materialized='incremental', incremental_strategy='delete+insert',
          unique_key=['snapshot_id', 'contract_version']) }}

with source_stats as (
  select
    snapshot_id, count(*)::bigint as bronze_rows,
    coalesce(sum(checkouts), 0) as bronze_checkouts,
    coalesce(sum(source_row_count) filter (where stream = 'monthly_overview'), 0)
      as bronze_source_row_count,
    count(*) filter (where cardinality(warning_codes) > 0)::bigint as warning_rows,
    coalesce(sum(cardinality(warning_codes)), 0)::bigint as warning_reasons
  from {{ ref('int_validated_rows') }}
  group by snapshot_id
), valid_rows as (
  select snapshot_id, source_row_number, checkouts, source_row_count
  from {{ ref('overview_groups') }}
  where contract_version = '{{ var("silver_contract_version") }}'
  union all
  select snapshot_id, source_row_number, checkouts, null::bigint
  from {{ ref('detail_records') }}
  where contract_version = '{{ var("silver_contract_version") }}'
), valid_stats as (
  select snapshot_id, count(*)::bigint as valid_rows,
    count(distinct source_row_number)::bigint as distinct_valid_rows,
    coalesce(sum(checkouts), 0) as valid_checkouts,
    coalesce(sum(source_row_count), 0) as valid_source_row_count
  from valid_rows
  group by snapshot_id
), rejected_stats as (
  select snapshot_id, count(distinct source_row_number)::bigint as rejected_rows,
    count(*)::bigint as rejection_reasons
  from {{ ref('quarantine_records') }}
  where contract_version = '{{ var("silver_contract_version") }}'
  group by snapshot_id
), format_stats as (
  select snapshot_id,
    count(*) filter (where not totals_match)::bigint as mismatched_formats
  from {{ ref('format_reconciliation') }}
  group by snapshot_id
), metrics as (
  select
    s.snapshot_id, s.stream, s.scope_id, s.month_start,
    s.mapping_version, s.manifest_sha256, s.selection_status,
    s.expected_rows, s.expected_checkouts, s.expected_source_row_count,
    s.loaded_rows, s.loaded_checkouts, s.loaded_source_row_count,
    coalesce(b.bronze_rows, 0)::bigint as bronze_rows,
    coalesce(b.bronze_checkouts, 0) as bronze_checkouts,
    coalesce(b.bronze_source_row_count, 0) as bronze_source_row_count,
    coalesce(v.valid_rows, 0)::bigint as valid_rows,
    coalesce(v.distinct_valid_rows, 0)::bigint as distinct_valid_rows,
    coalesce(v.valid_checkouts, 0) as valid_checkouts,
    coalesce(v.valid_source_row_count, 0) as valid_source_row_count,
    coalesce(r.rejected_rows, 0)::bigint as rejected_rows,
    coalesce(r.rejection_reasons, 0)::bigint as rejection_reasons,
    coalesce(b.warning_rows, 0)::bigint as warning_rows,
    coalesce(b.warning_reasons, 0)::bigint as warning_reasons,
    coalesce(f.mismatched_formats, 0)::bigint as mismatched_formats
  from {{ ref('int_snapshot_selection') }} as s
  left join source_stats as b using (snapshot_id)
  left join valid_stats as v using (snapshot_id)
  left join rejected_stats as r using (snapshot_id)
  left join format_stats as f using (snapshot_id)
  where s.snapshot_id is not null
), flagged as (
  select
    m.*,
    array_remove(array[
      case when m.selection_status <> 'READY' then m.selection_status end,
      case when m.selection_status = 'READY' and m.bronze_rows <> m.expected_rows
        then 'BRONZE_ROW_COUNT_MISMATCH' end,
      case when m.selection_status = 'READY' and m.bronze_checkouts <> m.expected_checkouts
        then 'BRONZE_CHECKOUT_MISMATCH' end,
      case when m.selection_status = 'READY' and m.stream = 'monthly_overview'
        and m.bronze_source_row_count <> m.expected_source_row_count
        then 'BRONZE_SOURCE_ROW_COUNT_MISMATCH' end,
      case when m.rejected_rows > 0 then 'REJECTED_ROWS' end,
      case when m.selection_status = 'READY' and
        m.bronze_rows <> m.valid_rows + m.rejected_rows
        then 'ROW_CLASSIFICATION_MISMATCH' end,
      case when m.selection_status = 'READY' and m.valid_rows <> m.distinct_valid_rows
        then 'DUPLICATE_VALID_KEY' end,
      case when m.selection_status = 'READY' and m.valid_rows <> m.expected_rows
        then 'SILVER_ROW_COUNT_MISMATCH' end,
      case when m.selection_status = 'READY' and m.valid_checkouts <> m.expected_checkouts
        then 'SILVER_CHECKOUT_MISMATCH' end,
      case when m.selection_status = 'READY' and m.stream = 'monthly_overview'
        and m.valid_source_row_count <> m.expected_source_row_count
        then 'SILVER_SOURCE_ROW_COUNT_MISMATCH' end,
      case when m.mismatched_formats > 0 then 'FORMAT_RECONCILIATION_MISMATCH' end
    ], null)::text[] as block_reasons
  from metrics as m
)
select
  snapshot_id, stream, scope_id, month_start, mapping_version, manifest_sha256,
  '{{ var("silver_contract_version") }}'::text as contract_version,
  '{{ invocation_id }}'::text as invocation_id,
  '{{ run_started_at.isoformat() }}'::timestamptz as observed_at,
  selection_status, expected_rows, loaded_rows, bronze_rows, valid_rows,
  distinct_valid_rows, rejected_rows, rejection_reasons, warning_rows,
  warning_reasons, expected_checkouts, loaded_checkouts, bronze_checkouts,
  valid_checkouts, expected_source_row_count, loaded_source_row_count,
  bronze_source_row_count, valid_source_row_count, mismatched_formats,
  block_reasons,
  case when cardinality(block_reasons) = 0 then 'PASS' else 'BLOCK' end as status
from flagged
