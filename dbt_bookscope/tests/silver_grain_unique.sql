with duplicates as (
  select 'overview_source_key' as issue, snapshot_id, source_row_number::text as key_value
  from {{ ref('overview_groups') }}
  group by snapshot_id, source_row_number, contract_version
  having count(*) > 1
  union all
  select 'overview_group', snapshot_id, materialtype_raw || '|' || usageclass_raw
  from {{ ref('overview_groups') }}
  group by snapshot_id, materialtype_raw, usageclass_raw, contract_version
  having count(*) > 1
  union all
  select 'detail_source_key', snapshot_id, source_row_number::text
  from {{ ref('detail_records') }}
  group by snapshot_id, source_row_number, contract_version
  having count(*) > 1
  union all
  select 'detail_source_id', snapshot_id, source_id
  from {{ ref('detail_records') }}
  group by snapshot_id, source_id, contract_version
  having count(*) > 1
  union all
  select 'quarantine_key', snapshot_id, source_row_number::text || '|' || rule_code
  from {{ ref('quarantine_records') }}
  group by snapshot_id, source_row_number, rule_code, contract_version
  having count(*) > 1
  union all
  select 'snapshot_quality_key', snapshot_id, contract_version
  from {{ ref('snapshot_quality') }}
  group by snapshot_id, contract_version
  having count(*) > 1
)
select * from duplicates
