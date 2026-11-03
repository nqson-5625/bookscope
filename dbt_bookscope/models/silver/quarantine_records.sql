{{ config(materialized='incremental', incremental_strategy='delete+insert',
          unique_key=['snapshot_id', 'source_row_number', 'rule_code', 'contract_version']) }}

select
  v.snapshot_id, v.source_row_number, v.stream, v.scope_id, v.month_start,
  reason.rule_code, v.contract_version, v.mapping_version, v.raw_path,
  v.source_payload,
  case reason.rule_code
    when 'INVALID_CHECKOUTS' then v.checkouts_raw
    when 'BIGINT_OVERFLOW' then v.checkouts_raw
    when 'UNKNOWN_MATERIAL' then v.materialtype_raw
    when 'BLANK_USAGE_CLASS' then v.usageclass_raw
    when 'INVALID_SOURCE_ROW_COUNT' then v.source_row_count_raw
    when 'DETAIL_PERIOD_MISMATCH' then concat_ws('-', v.checkoutyear_raw, v.checkoutmonth_raw)
    when 'MISSING_SOURCE_ID' then v.source_id
    when 'DUPLICATE_SOURCE_ID' then v.source_id
    when 'BLANK_TITLE' then v.title_raw
    when 'BLANK_CHECKOUT_TYPE' then v.checkouttype_raw
  end as observed_value,
  '{{ run_started_at.isoformat() }}'::timestamptz as observed_at
from {{ ref('int_validated_rows') }} as v
cross join lateral unnest(v.block_reasons) as reason(rule_code)
