{{ config(tags=['gold']) }}

select 'overview' as relation_name from {{ ref('monthly_overview') }}
group by data_version, scope_id, month_start, format_key having count(*) > 1
union all
select 'book' from {{ ref('book_monthly_stats') }}
group by data_version, scope_id, book_record_key, month_start, format_key having count(*) > 1
union all
select 'feature' from {{ ref('book_monthly_features') }}
group by data_version, book_record_key, month_start having count(*) > 1
union all
select 'overview_lineage' from {{ ref('overview_source_lineage') }}
group by data_version, snapshot_id, source_row_number having count(*) > 1
union all
select 'book_lineage' from {{ ref('book_source_lineage') }}
group by data_version, snapshot_id, source_row_number having count(*) > 1
