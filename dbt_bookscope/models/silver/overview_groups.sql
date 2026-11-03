{{ config(materialized='incremental', incremental_strategy='delete+insert',
          unique_key=['snapshot_id', 'source_row_number', 'contract_version']) }}

select
  snapshot_id, source_row_number, stream, scope_id, month_start,
  raw_path, materialtype_raw, format_key, usageclass_raw,
  btrim(usageclass_raw) as usage_class,
  checkouts, source_row_count, mapping_version, contract_version
from {{ ref('int_validated_rows') }}
where stream = 'monthly_overview' and cardinality(block_reasons) = 0
