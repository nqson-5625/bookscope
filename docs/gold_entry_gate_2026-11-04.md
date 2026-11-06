# Gold Entry Gate — 04/11/2026

Đây là quyết định **trước khi triển khai**; kết quả Gold sau triển khai ở
[nghiệm thu G3](gold_g3_acceptance_2026-11-04.md).

**Quyết định: PASS để bắt đầu triển khai `gold_analytics_marts`.** Đây là
nghiệm thu đầu vào và thiết kế. Chưa có Gold fact/model, chưa đạt G3, chưa có
publication hoặc chuyển con trỏ công bố.

| Điều kiện | Bằng chứng hiện tại | Kết quả |
| --- | --- | --- |
| 1. DQ FULL PASS | DQ run sau lần `dbt build` mới nhất: `ops.dq_gate` PASS 72/72, 0 BLOCK, `source_state_matches=true`; 1.412 kết quả gồm 36 dbt tests PASS, 180 WARN metadata. Kiểm thử DQ trong giao dịch đạt 13/13, gate vẫn PASS và không tạo file ở bước nghiệm thu DQ. | PASS |
| 2. Gold profiling | [Profile](gold_profile_2026-11-04.md): overview 17.042.420 checkouts; detail 1.495.317; 36 tháng × 2 format mỗi scope; 1.000 tuple cohort khớp Raw; nhiều dòng/tuple–tháng và 0 source ID ổn định xuyên tháng. | PASS |
| 3. KPI specification | [KPI Spec](gold_kpi_spec.md) chốt công thức, scope/grain, zero/NULL, tháng thiếu, mẫu số 0, rolling calendar và earliest backtest. | PASS |
| 4. Gold data contract | [Gold Contract](gold_contract.md) chốt grain, khóa, phép gộp, validation và lineage cho fact/dimension/feature. | PASS |
| 5. Logical schema & ERD | Gold Contract có cột/kiểu logic, FK và Mermaid ERD; `book_record_key` là SHA-256 tuple cohort v1, 1.000/1.000 hash khác nhau, giới hạn ngữ nghĩa nêu rõ. | PASS |
| 6. Versioning & publication | Gold Contract quy định digest đầu ra, `data_version` deterministic, pin 72 snapshot, staging, replay và pointer promotion nguyên tử sau gate. | PASS cho thiết kế |
| 7. Gold test plan | [Test Plan](gold_test_plan.md) gồm reconciliation, uniqueness, KPI, identity, replay/idempotency, lỗi gate và publication fail-safe. | PASS cho thiết kế |
| 8. dbt Gold readiness | PostgreSQL có schema `gold`, user có USAGE/CREATE; hai view DQ và `ops.dq_gate` hiện diện. `dbt parse` đạt; macro schema trả `silver` cho Silver, `gold` cho Gold local dev, `ci_alice_gold` cho CI; Silver `dbt build` sau cấu hình đạt 49/49. Gold folder và dbt sources đã khai báo. | PASS |

`dbt parse/build` hiện báo `models.dbt_bookscope.gold` chưa dùng vì chưa có
SQL model Gold. Cảnh báo này sẽ hết khi model đầu tiên được thêm; không làm
thay đổi schema Silver hoặc kết quả Silver hiện hành. Cấu hình schema custom
của dbt mặc định sẽ nối `target.schema` với tên custom
([dbt Custom schemas](https://docs.getdbt.com/docs/build/custom-schemas)),
nên macro local là cần thiết để đặt model Gold vào đúng schema `gold`.

## Điều kiện mở gate tiếp theo (G3)

Gold models và runner phải được triển khai theo các hợp đồng trên; chạy đầy
đủ Gold tests với dữ liệu thật và fixture lỗi, chứng minh replay cùng digest,
kiểm tra DQ/source state lần cuối trước promotion, rồi mới công bố version.
Không coi trạng thái PASS đầu vào này là phê duyệt xuất bản Gold.
