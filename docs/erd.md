# ERD logic tổng quan

Grain, khóa và quan hệ logic Silver được chốt tại
[Silver Data Contract](silver_contract.md); sơ đồ dưới đây thể hiện các lớp
Gold/DS ở mức tổng quan và chưa phải DDL.

Theo mục 5 của [BookScope.pdf](BookScope.pdf). Grain Silver đã chốt.
Profile identity đã hoàn tất ngày 04/11/2026; [Gold Data Contract và ERD](gold_contract.md)
là hợp đồng hiện hành cho khóa tuple cohort, bảng Gold, FK và publication.
DDL thực tế nằm ở `sql/gold/001_publications.sql`, kết quả ở
[nghiệm thu G3](gold_g3_acceptance_2026-11-04.md).
Sơ đồ tổng quan bên dưới chỉ giữ vai trò bối cảnh kiến trúc.

```mermaid
erDiagram
  OPS_PIPELINE_RUNS ||--o{ OPS_DATA_SNAPSHOTS : creates
  OPS_DATA_SNAPSHOTS ||--o{ BRONZE_CHECKOUT_RECORDS : contains
  OPS_PIPELINE_RUNS ||--o{ OPS_DQ_RESULTS : records
  OPS_DATA_PUBLICATIONS ||--o{ GOLD_MONTHLY_OVERVIEW : versions
  OPS_DATA_PUBLICATIONS ||--o{ GOLD_BOOK_MONTHLY_STATS : versions
  GOLD_BOOK_MONTHLY_STATS }o--|| DIM_BOOK_RECORD : identifies
  GOLD_BOOK_MONTHLY_STATS }o--|| DIM_MONTH : occurs_in
  GOLD_BOOK_MONTHLY_STATS }o--|| DIM_FORMAT : classifies
  GOLD_BOOK_MONTHLY_STATS ||--o{ GOLD_DECISION_SCORES : supports
  OPS_DATA_INCIDENTS }o--o{ DIM_MONTH : flags

  OPS_PIPELINE_RUNS {
    string run_id PK
    string status
  }
  OPS_DATA_SNAPSHOTS {
    string snapshot_id PK
    string scope_id
    string status
    string manifest_path
  }
  BRONZE_CHECKOUT_RECORDS {
    string snapshot_id PK
    int source_row_number PK
    jsonb source_payload
  }
  OPS_DQ_RESULTS {
    string run_id FK
    string rule_id
    string severity
  }
  OPS_DATA_PUBLICATIONS {
    string data_version PK
    string scope_id
    string status
  }
  DIM_BOOK_RECORD {
    string book_record_key PK
    string source_identity
    string title
    string creator
  }
  DIM_MONTH {
    date month_start PK
  }
  DIM_FORMAT {
    string format_key PK
    string materialtype
    string usageclass
  }
  GOLD_MONTHLY_OVERVIEW {
    string data_version PK
    string scope_id PK
    date month_start PK
    string format_key PK
    bigint checkouts
  }
  GOLD_BOOK_MONTHLY_STATS {
    string data_version PK
    string scope_id PK
    string book_record_key PK
    date month_start PK
    string format_key PK
    bigint checkouts
  }
  GOLD_DECISION_SCORES {
    string data_version FK
    string book_record_key FK
    date evaluation_month
    string score_version
    string reliability_status
  }
  OPS_DATA_INCIDENTS {
    string incident_id PK
    date start_month
    date end_month
  }
```

`silver.overview_groups`, `silver.detail_records`, `silver.quarantine_records`
và `silver.snapshot_quality` nằm giữa Bronze và Gold. Chưa vẽ quan hệ FK
cho `book_record_key` trong sơ đồ cũ. Quan hệ FK và identity version đã được
chốt ở [Gold Data Contract](gold_contract.md). Sơ đồ trên không phải DDL để
chạy trực tiếp.
