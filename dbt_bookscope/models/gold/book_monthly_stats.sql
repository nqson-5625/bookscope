{{ config(materialized='table', tags=['gold']) }}

select data_version, scope_id, book_record_key, month_start, format_key,
       snapshot_id as source_snapshot_id,
       sum(checkouts::numeric)::bigint as checkouts,
       count(*)::bigint as source_record_count
from {{ ref('book_source_lineage') }}
group by data_version, scope_id, book_record_key, month_start, format_key, snapshot_id
