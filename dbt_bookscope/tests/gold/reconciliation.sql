{{ config(tags=['gold']) }}

with o_source as (
  select snapshot_id, month_start, format_key,
         sum(checkouts::numeric) as checkouts,
         sum(source_row_count::numeric) as source_rows,
         count(*) as groups
  from {{ ref('overview_source_lineage') }}
  group by 1,2,3
), o_fact as (
  select source_snapshot_id as snapshot_id, month_start, format_key,
         checkouts::numeric, source_row_count::numeric as source_rows,
         source_group_count as groups
  from {{ ref('monthly_overview') }}
), b_source as (
  select snapshot_id, book_record_key, month_start, format_key,
         sum(checkouts::numeric) as checkouts, count(*) as rows
  from {{ ref('book_source_lineage') }} group by 1,2,3,4
), b_fact as (
  select source_snapshot_id as snapshot_id, book_record_key, month_start,
         format_key, checkouts::numeric, source_record_count as rows
  from {{ ref('book_monthly_stats') }}
)
select 'overview' as relation_name from o_source s full join o_fact f using (snapshot_id,month_start,format_key)
where s.checkouts is distinct from f.checkouts
   or s.source_rows is distinct from f.source_rows
   or s.groups is distinct from f.groups
union all
select 'book' from b_source s full join b_fact f using (snapshot_id,book_record_key,month_start,format_key)
where s.checkouts is distinct from f.checkouts or s.rows is distinct from f.rows
