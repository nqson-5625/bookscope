# Gold Test Plan — v1_2026_11_04

Chưa chạy Gold tests vì chưa có Gold model. Khi triển khai, mọi phép kiểm
BLOCK phải chạy trên **cùng `data_version` và 72 snapshot đã pin** trước
promotion. Query chỉ đọc `silver.dq_approved_*` khi DQ gate PASS; số cố định
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

Kiểm thử DB phải dùng transaction/fixture riêng và rollback sau khi gây lỗi.
`dbt build --select path:models/gold` chỉ xác minh model/test Gold; runner còn
phải kiểm tra Raw/DQ pin, output digest và pointer promotion. Khi dbt có Gold
models, thêm schema tests (`not_null`, `unique`, `relationships`,
`accepted_values`) và singular SQL tests cho reconciliation theo scope, key,
calendar và version; chạy pytest/unittest cho hàm hash, digest, replay và
transaction publication. Lưu kết quả theo build/version để audit được lần
publish thực tế, không chỉ log console.

Tiêu chuẩn nghiệm thu G3: tất cả BLOCK tests PASS trên dữ liệu thật 36 tháng,
fixture lỗi có chủ đích bị chặn, replay cùng version có digest bằng nhau, và
promotion fail-safe trong giao dịch đã được chứng minh. WARN metadata được
báo riêng; không làm đổi checkouts.
