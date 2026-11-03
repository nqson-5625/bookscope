{{ config(materialized='incremental', incremental_strategy='delete+insert',
          unique_key=['snapshot_id', 'source_row_number', 'contract_version']) }}

select
  snapshot_id, source_row_number, stream, scope_id, month_start,
  raw_path, materialtype_raw, format_key, usageclass_raw,
  btrim(usageclass_raw) as usage_class,
  checkouts, mapping_version, contract_version,
  source_id, title_raw, btrim(title_raw) as title_display,
  regexp_replace(lower(btrim(title_raw)), '[[:space:]]+', ' ', 'g') as title_search,
  creator_raw, nullif(btrim(creator_raw), '') as creator_display,
  checkouttype_raw, btrim(checkouttype_raw) as checkout_type,
  case when nullif(btrim(isbn_raw), '') is not null then isbn_raw end as isbn_raw,
  case when nullif(btrim(subjects_raw), '') is not null then subjects_raw end as subjects_raw,
  case when nullif(btrim(publisher_raw), '') is not null then publisher_raw end as publisher_raw,
  case when nullif(btrim(publication_year_raw), '') is not null
    then publication_year_raw end as publication_year_raw
from {{ ref('int_validated_rows') }}
where stream = 'book_details' and cardinality(block_reasons) = 0
