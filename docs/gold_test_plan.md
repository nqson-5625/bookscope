# Gold Test Plan — v1_2026_11_04

Các kiểm thử đã triển khai trong `dbt_bookscope/models/gold/schema.yml`,
`dbt_bookscope/tests/gold/`, `src/modules/gold_analytics_marts/validation.py`
và `tests/test_gold_analytics_marts.py`. Mọi phép kiểm BLOCK chạy trên cùng
`build_id` và 72 snapshot đã pin trước promotion; khi đủ điều kiện,
`data_version` được tính từ output digest. Query chỉ đọc `silver.dq_approved_*`
khi DQ gate PASS; số cố định
trong [profile](gold_profile_2026-11-04.md) là mốc khảo sát, không thay thế
đối soát động với snapshot/version được chọn.

| Nhóm | Kiểm thử và kết quả mong đợi | Gate |
| --- | --- | --- |
| Input | DQ gate PASS, `source_state_matches=true`, 72 selection/36 tháng, đúng Silver/DQ/cohort version; thay source sau pin làm fail. | BLOCK |
| Overview reconciliation | Theo `month_start × format_key × ALL_ELIGIBLE_AGG`: Gold checkouts = `SUM(Silver.checkouts)`, source rows = `SUM(source_row_count)`, group count = `COUNT(*)`; tổng Gold/lineage cũng khớp. | BLOCK |
| Detail reconciliation | Mỗi dòng Silver detail map đúng một `book_source_lineage`; theo snapshot, tháng, format và `book_record_key`, `SUM(checkouts)`/`COUNT(*)` của Gold bằng Silver. Nhiều dòng cùng tuple phải được cộng, không mất hoặc nhân dòng. | BLOCK |
| Grain/keys | Không trùng PK composite của fact, feature, lineage, `publication_snapshots`; đúng 1.000 book keys hiện hành; FK tới publication, month, format và book. | BLOCK |
| Identity | `Ebook`/`EBOOK` cùng tuple → cùng key; `BOOK` khác `REGPRINT`; NULL creator khác chuỗi rỗng; title khác hoa/trắng giữ key khác; thay `source_id` không đổi key; mỗi hash chỉ một tuple. | BLOCK |
| Scope/format | Không cộng overview và detail như cùng tổng; `cohort_coverage ∈ [0,1]` theo cùng tháng/format; material mapping nhất quán. | BLOCK |
| KPI correctness | Fixture độc lập cho MoM/YoY/MA3/rolling 3m/growth 3m, format share; tỷ trọng hai format cộng thành 1 khi mẫu số > 0; kiểm tra tháng thiếu, zero, mẫu số 0, kỳ cách quãng, năm trước thiếu, nhiều dòng nguồn. | BLOCK |
| Missing vs zero | Fact book không có dòng khi không quan sát; feature calendar spine có `observed=false`, `checkouts=NULL`. Dòng nguồn checkouts 0 vẫn `observed=true`, giá trị 0. | BLOCK |
| Temporal leakage | Không dùng cohort chọn từ cả năm 2023 để đánh giá/backtest trước `2023-12`; kỳ sự cố chỉ gắn cờ theo policy, không tự sửa số nguồn. | BLOCK |
| Replay/idempotency | Hai build cùng selection/rules/output cho cùng `data_version`, cùng digest và cùng số dòng; không duplicate fact/lineage; version cũ còn nguyên. | BLOCK |
| Publication | Snapshot mới chưa DQ duyệt, DQ revision đổi, thiếu một trong 72 snapshot, Gold test fail hoặc digest collision: không đổi `current_publication`; PUBLISHED cũ vẫn đọc được. | BLOCK |
| Published Dimension | UPDATE/DELETE mọi dòng `dim_month`, `dim_format`, `dim_book_record` đã được version PUBLISHED dùng phải bị chặn; INSERT dòng mới hợp lệ được phép. TRUNCATE bị chặn. | BLOCK |
| Concurrent builds | Hai Gold build cùng khởi chạy được thực hiện tuần tự; mỗi build có `manifest.json`/`run_results.json` riêng, invocation ID và artifact checksum đúng `build_id`; version cũ và pointer không đổi khi replay. | BLOCK |

Fixture lỗi DB dùng transaction riêng và rollback. dbt build chạy 8 models,
31 schema/singular tests; runner kiểm tra đủ danh tính model/test trong
`run_results.json` và `manifest.json`, đối soát lại với Silver, tính digest
trên staging, so với bản versioned sau khi copy và khóa DQ/source state trước
promotion. Bằng chứng dbt, validation, digest và trạng thái từng build được
lưu ở `ops.gold_builds`; test độc lập so Python reference với KPI thực tế.
Xem [nghiệm thu G3](gold_g3_acceptance_2026-11-04.md) cho số liệu của lần chạy.

Tiêu chuẩn nghiệm thu G3: tất cả BLOCK tests PASS trên dữ liệu thật 36 tháng,
fixture lỗi có chủ đích bị chặn, replay cùng version có digest bằng nhau, và
promotion fail-safe trong giao dịch đã được chứng minh. WARN metadata được
báo riêng; không làm đổi checkouts.
