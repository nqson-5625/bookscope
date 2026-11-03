# silver_transformation

Module dùng dbt trên PostgreSQL để chuẩn hóa hai stream Bronze theo
[Silver Data Contract](silver_contract.md). Dữ liệu 2023-01–2025-12 được xử lý
riêng theo `ALL_ELIGIBLE_AGG` và `SELECTED_DETAIL`; không xóa trùng các dòng
detail cùng tuple metadata.

## Chạy

Từ gốc repo, sau khi [cài dbt](dbt_setup.md), khởi động PostgreSQL, nạp đủ
Bronze và giữ Raw snapshot/cohort tại `data/raw/`:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m modules.silver_transformation run
.\.venv\Scripts\python.exe -m modules.silver_transformation status
```

`run` xác minh offline toàn bộ Raw snapshot, cohort và mapping; đối chiếu 72
`snapshot_id` mới nhất, `ready_for_transform` và SHA-256 manifest đã đăng ký
trong PostgreSQL; sau đó chạy `dbt build`. Exit code khác 0 là gate thất bại.
`status` trả JSON tổng hàng, lượt mượn, reject/WARN và trạng thái gate.
Lệnh `python scripts/run_dbt.py build` chạy trực tiếp dbt nhưng không thực hiện
bước đối chiếu Raw với registry của CLI này.

## Model và truy vết

| Model | Trách nhiệm |
| --- | --- |
| `format_map`, `int_snapshot_selection`, `int_validated_rows` | Mapping năm mã, chọn snapshot mới nhất theo tháng/scope, parse số an toàn và gắn reason/WARN cho từng dòng. |
| `overview_groups`, `detail_records` | Dòng hợp lệ, khóa `(snapshot_id, source_row_number, contract_version)`; incremental để giữ snapshot/version cũ. |
| `quarantine_records` | Một dòng mỗi blocking reason, giữ JSONB và `raw_path`, incremental theo khóa logic. |
| `warning_counts`, `format_reconciliation`, `snapshot_quality` | Đếm WARN, đối soát theo snapshot × format và tổng với manifest, ghi PASS/BLOCK. |
| `run_snapshot_selection` | Lưu 72 snapshot ID/trạng thái cho mỗi dbt invocation để tái lập. |
| `pipeline_gate`, `active_overview_groups`, `active_detail_records` | Chỉ mở dữ liệu hiện hành khi mọi tháng/scope đều PASS; snapshot mới chưa sẵn sàng làm gate BLOCK. |

`snapshot_quality` và các bảng dữ liệu giữ bản ghi theo contract version.
`run_snapshot_selection` là lịch sử append theo `invocation_id`. Các view
`active_*` chỉ dùng snapshot mới nhất, contract version hiện hành và gate PASS.
Gold sau này cần đọc các view này cùng bằng chứng publication/data version;
chưa có Gold trong repo.

## Nghiệm thu local 03/11/2026

`python -m modules.silver_transformation run` trả Raw audit PASS và dbt build
49/49 PASS. `pipeline_gate` PASS 72/72 snapshot, 0 reject; 144 nhóm overview có
17.042.420 checkouts và 5.745.441 dòng nguồn, 34.886 dòng detail có 1.495.317
checkouts. 480 dòng detail có ít nhất một WARN metadata; WARN không loại dòng.
Kiểm thử giới hạn `bigint` và kiểm thử giao dịch PostgreSQL xác nhận unknown material/title rỗng nhận
blocking reason, và snapshot mới nhất chưa ready làm active views rỗng:

```powershell
$env:PYTHONPATH = "src"
$env:BOOKSCOPE_TEST_DB = "1"
.\.venv\Scripts\python.exe -m unittest tests/test_silver_transformation.py -v
```
