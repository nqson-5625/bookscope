# data_quality

Module kiểm tra bộ 72 snapshot Silver hiệu lực cho 36 tháng 2023-01–2025-12.
Chạy sau [`silver_transformation`](silver_transformation.md) và trước Gold. DQ
đối chiếu từng `month_start × stream × format_key` với manifest Raw, ghi lịch sử
chỉ số vào PostgreSQL và chặn dữ liệu đầu vào Gold khi gate chưa đạt.

## Chạy và xem kết quả

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m modules.silver_transformation run
.\.venv\Scripts\python.exe -m modules.data_quality run
.\.venv\Scripts\python.exe -m modules.data_quality status
```

`run` tự áp dụng migration idempotent
[`sql/data_quality/001_results.sql`](../sql/data_quality/001_results.sql), kiểm
tra offline Raw và registry, đọc `manifest.json` cùng `run_results.json` của
đúng invocation Silver,
đánh giá dữ liệu và lưu một lần chạy vào `ops.dq_runs`, từng phép kiểm vào
`ops.dq_results`. Exit code 0 là PASS; 2 là BLOCK; 1 là lỗi vận hành.
Mã DQ được tách theo bằng chứng Silver, Raw, dbt và chỉ số; `code_sha256`
trong `ops.dq_runs` tính trên toàn bộ các file Python của module cùng migration.

`status` chỉ đọc `ops.dq_gate`. Có thể truy vấn:

```sql
SELECT * FROM ops.dq_gate;
SELECT metric_code, status, count(*)
FROM ops.dq_results
WHERE run_id = (SELECT run_id FROM ops.dq_gate)
GROUP BY 1, 2 ORDER BY 1, 2;
```

Mỗi lần `run` tạo `run_id` mới để giữ lịch sử; trong một lần chạy, mỗi kết quả
có `(run_id, result_no)`. `SNAPSHOT_SELECTION` lưu 72 snapshot ID, trạng thái,
scope và hash manifest. `ops.dq_gate` chỉ PASS khi lần chạy mới nhất PASS,
Silver PASS và lựa chọn snapshot hiện tại khớp hoàn toàn lựa chọn đã kiểm. Nếu
snapshot mới xuất hiện, gate trở thành BLOCK cho đến khi Silver và DQ chạy lại.
Chạy lại Silver cũng làm gate DQ BLOCK cho đến khi kiểm tra lại build mới.

DQ kiểm tra đủ 13 model và 36 test bắt buộc của Silver theo
`dbt_contract.py`, rồi đối chiếu ID với manifest của đúng invocation. Thiếu
model/test, chạy `dbt build` có lọc tài nguyên, trùng kết quả hoặc trạng thái
không đạt đều làm gate BLOCK. Các test Silver mới xuất hiện trong manifest cũng
phải có kết quả tương ứng.

Trigger theo dõi thao tác ghi trên 9 bảng `ops`/Bronze/Silver trong
`ops.dq_source_revisions`. Mỗi DQ run lưu revision, định danh bảng và trạng thái
trigger vào `ops.dq_runs.source_state`; `ops.dq_gate` so sánh với trạng thái
hiện tại. Thay đổi dữ liệu trực tiếp, thay bảng qua full refresh hoặc tắt
trigger đều làm gate BLOCK ngay, kể cả khi snapshot ID và invocation giữ nguyên.
Gold phải chạy DQ lại sau các thay đổi đó.

Gold phải đọc `silver.dq_approved_overview_groups` và
`silver.dq_approved_detail_records`, hoặc kiểm tra `ops.dq_gate.status = 'PASS'`
trong cùng giao dịch. Hai view được tạo bởi migration và trả rỗng khi gate BLOCK.
`silver.active_*` chỉ đại diện cho gate Silver riêng lẻ.

## Chỉ số và quyết định

| Nhóm | Phép kiểm/chỉ số | Quyết định |
| --- | --- | --- |
| Validity | `SNAPSHOT_SELECTION`, `BRONZE_VALIDATION`, `SILVER_SNAPSHOT_QUALITY`, tỷ lệ valid/reject | Lỗi dòng hoặc snapshot: BLOCK; tỷ lệ: CONTEXT |
| Provenance | Raw audit, registry, SHA-256 và identity manifest, đúng dbt build/test invocation | Sai hoặc thiếu: BLOCK |
| Consistency | Số dòng và checkouts Raw–Bronze–Silver theo từng format trong từng snapshot/scope | Sai bất kỳ format: BLOCK, kể cả khi tổng snapshot bù trừ và vẫn khớp |
| Uniqueness | Khóa nguồn/nhóm Silver của contract hiện hành | Trùng: BLOCK |
| Completeness | Số và tỷ lệ thiếu Creator, ISBN, Subjects, Publisher, Publication Year | WARN, vẫn giữ dòng |
| Coverage | 72 month/scope, tỷ lệ checkout detail so overview theo đúng tháng/format | Thiếu tháng: BLOCK; detail vượt overview: BLOCK; tỷ lệ: CONTEXT |
| Freshness | Tháng mới nhất và kỳ cuối cấu hình | CONTEXT; phạm vi MVP cố định kết thúc 2025-12 |
| Lineage | Snapshot ID, contract/mapping/DQ version, hash manifest/config/mã, dbt invocation, thời điểm chạy | Lưu trong `ops.dq_runs` và `ops.dq_results` |

Hai scope không được cộng thành một tổng. WARN metadata được ghi theo từng
snapshot, bao gồm mẫu số và tỷ lệ; không làm rơi bản ghi. Các kiểm tra Gold
grain/đối soát Silver–Gold và published version thuộc module Gold và runner.

## Kiểm thử lỗi có chủ đích

```powershell
$env:PYTHONPATH = "src"
$env:BOOKSCOPE_TEST_DB = "1"
.\.venv\Scripts\python.exe -m unittest tests/test_data_quality.py -v
```

Kiểm thử PostgreSQL thay đổi dữ liệu trong transaction rồi rollback: lệch
format nhưng tổng không đổi; tháng 13, title rỗng, source ID trùng; checkouts
âm/dạng chữ; hash manifest sai; snapshot mới, sửa trực tiếp Bronze/Silver hoặc
tắt trigger làm gate và approved view BLOCK. Replay hai lần với cùng đầu vào
cho cùng kết quả; `run_id` mới giữ lịch sử và khóa `(run_id, result_no)` ngăn
nhân đôi kết quả trong một lần chạy.
Raw audit và kiểm thử Silver/dbt đã kiểm tra file part/hash, grain và giới hạn
`bigint`; DQ lưu kết quả các dbt tests của lần build Silver tương ứng.

Nghiệm thu local 03/11/2026: DQ PASS 72/72, 0 BLOCK, 180 kết quả WARN theo
field/snapshot; 1.412 kết quả gồm 36 dbt tests. Số WARN là số phép đo có thiếu
metadata, không phải số dòng thiếu (xem `silver.warning_counts`).
