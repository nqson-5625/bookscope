{{ config(materialized='view') }}

with source_rows as (
  select
    s.snapshot_id, r.source_row_number, s.stream, s.scope_id,
    s.month_start, s.mapping_version, r.raw_path, r.source_payload,
    r.source_payload ->> 'materialtype' as materialtype_raw,
    r.source_payload ->> 'usageclass' as usageclass_raw,
    r.source_payload ->> 'checkouts' as checkouts_raw,
    r.source_payload ->> 'row_count' as source_row_count_raw,
    r.source_payload ->> 'checkoutyear' as checkoutyear_raw,
    r.source_payload ->> 'checkoutmonth' as checkoutmonth_raw,
    r.source_payload ->> ':id' as source_id,
    r.source_payload ->> 'title' as title_raw,
    case when jsonb_typeof(r.source_payload -> 'creator') = 'string'
      then r.source_payload ->> 'creator' end as creator_raw,
    r.source_payload ->> 'checkouttype' as checkouttype_raw,
    case when jsonb_typeof(r.source_payload -> 'isbn') = 'string'
      then r.source_payload ->> 'isbn' end as isbn_raw,
    case when jsonb_typeof(r.source_payload -> 'subjects') = 'string'
      then r.source_payload ->> 'subjects' end as subjects_raw,
    case when jsonb_typeof(r.source_payload -> 'publisher') = 'string'
      then r.source_payload ->> 'publisher' end as publisher_raw,
    case when jsonb_typeof(r.source_payload -> 'publicationyear') = 'string'
      then r.source_payload ->> 'publicationyear' end as publication_year_raw
  from {{ source('bronze', 'checkout_records') }} as r
  join {{ ref('int_snapshot_selection') }} as s using (snapshot_id)
  where s.selection_status = 'READY'
), parsed as (
  select
    r.*,
    m.format_key,
    {{ safe_nonnegative_bigint('r.checkouts_raw') }} as checkouts,
    {{ safe_nonnegative_bigint('r.source_row_count_raw') }} as source_row_count,
    {{ safe_nonnegative_bigint('r.checkoutyear_raw') }} as checkoutyear,
    {{ safe_nonnegative_bigint('r.checkoutmonth_raw') }} as checkoutmonth,
    count(*) over (
      partition by r.snapshot_id, r.materialtype_raw, r.usageclass_raw
    ) as overview_group_count,
    count(*) over (
      partition by r.snapshot_id, r.source_id
    ) as source_id_count
  from source_rows as r
  left join {{ ref('format_map') }} as m
    on m.materialtype_raw = r.materialtype_raw
    and m.mapping_version = r.mapping_version
)
select
  p.*,
  '{{ var("silver_contract_version") }}'::text as contract_version,
  array_remove(array[
    case when extract(day from p.month_start) <> 1 then 'INVALID_PERIOD' end,
    case when p.checkouts_raw is null or p.checkouts_raw !~ '^[0-9]+$'
      then 'INVALID_CHECKOUTS' end,
    case when p.checkouts_raw ~ '^[0-9]+$' and p.checkouts is null
      then 'BIGINT_OVERFLOW' end,
    case when p.format_key is null then 'UNKNOWN_MATERIAL' end,
    case when jsonb_typeof(p.source_payload -> 'usageclass') is distinct from 'string'
      or nullif(btrim(p.usageclass_raw), '') is null
      then 'BLANK_USAGE_CLASS' end,
    case when p.stream = 'monthly_overview'
      and (p.source_row_count is null or p.source_row_count <= 0)
      then 'INVALID_SOURCE_ROW_COUNT' end,
    case when p.stream = 'monthly_overview' and p.overview_group_count > 1
      then 'DUPLICATE_OVERVIEW_GROUP' end,
    case when p.stream = 'book_details'
      and (p.checkoutyear is distinct from extract(year from p.month_start)::bigint
        or p.checkoutmonth is distinct from extract(month from p.month_start)::bigint)
      then 'DETAIL_PERIOD_MISMATCH' end,
    case when p.stream = 'book_details'
      and (jsonb_typeof(p.source_payload -> ':id') is distinct from 'string'
        or nullif(btrim(p.source_id), '') is null)
      then 'MISSING_SOURCE_ID' end,
    case when p.stream = 'book_details'
      and jsonb_typeof(p.source_payload -> ':id') = 'string'
      and nullif(btrim(p.source_id), '') is not null
      and p.source_id_count > 1 then 'DUPLICATE_SOURCE_ID' end,
    case when p.stream = 'book_details'
      and (jsonb_typeof(p.source_payload -> 'title') is distinct from 'string'
        or nullif(btrim(p.title_raw), '') is null)
      then 'BLANK_TITLE' end,
    case when p.stream = 'book_details'
      and (jsonb_typeof(p.source_payload -> 'checkouttype') is distinct from 'string'
        or nullif(btrim(p.checkouttype_raw), '') is null)
      then 'BLANK_CHECKOUT_TYPE' end
  ], null)::text[] as block_reasons,
  array_remove(array[
    case when p.stream = 'book_details' and nullif(btrim(p.creator_raw), '') is null
      then 'MISSING_CREATOR' end,
    case when p.stream = 'book_details' and nullif(btrim(p.isbn_raw), '') is null
      then 'MISSING_ISBN' end,
    case when p.stream = 'book_details' and nullif(btrim(p.subjects_raw), '') is null
      then 'MISSING_SUBJECTS' end,
    case when p.stream = 'book_details' and nullif(btrim(p.publisher_raw), '') is null
      then 'MISSING_PUBLISHER' end,
    case when p.stream = 'book_details' and nullif(btrim(p.publication_year_raw), '') is null
      then 'MISSING_PUBLICATION_YEAR' end
  ], null)::text[] as warning_codes
from parsed as p
