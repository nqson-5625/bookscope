{{ config(materialized='incremental', incremental_strategy='delete+insert',
          unique_key=['invocation_id', 'stream', 'scope_id', 'month_start']) }}

select
  '{{ invocation_id }}'::text as invocation_id,
  '{{ var("silver_contract_version") }}'::text as contract_version,
  '{{ var("silver_mapping_version") }}'::text as required_mapping_version,
  '{{ run_started_at.isoformat() }}'::timestamptz as selected_at,
  stream, scope_id, month_start, snapshot_id, mapping_version,
  manifest_sha256, selection_status
from {{ ref('int_snapshot_selection') }}
