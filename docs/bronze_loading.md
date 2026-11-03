# Nạp Raw vào PostgreSQL Bronze

`bronze_loading` chỉ nhận snapshot Raw `COMPLETE` đã qua kiểm tra manifest,
checksum các part, số dòng và tổng lượt mượn. Module đăng ký manifest cùng SHA-256
trong `ops.data_snapshots`, rồi tạo batch và ghi nguyên payload JSONB vào
`bronze.checkout_records`. Mỗi dòng giữ `snapshot_id`, thứ tự nguồn
`source_row_number`, `load_batch_id` và `raw_path`.

## Chuẩn bị và chạy

Từ gốc repo, cài dependency, cấu hình `BOOKSCOPE_DB_*` trong `.env` và khởi động
PostgreSQL bằng Docker Compose:

```powershell
python -m pip install -r requirements.txt
docker compose up -d --wait db
$env:PYTHONPATH = "src"
$snapshot = python -m modules.raw_snapshot select monthly_overview 2023-01 | ConvertFrom-Json
python -m modules.bronze_loading load $snapshot.path
python -m modules.bronze_loading range --start 2023-01 --end 2023-12 --stream both
```

`range` mặc định lấy giới hạn tháng từ `configs/project_foundation.json`, chọn
snapshot `COMPLETE` mới nhất theo stream/tháng và dừng khi thiếu hoặc lỗi một
snapshot. `--stream` nhận `overview`, `detail` hoặc `both`; `--chunk-size` đặt số
dòng mỗi lần gửi đến PostgreSQL (mặc định 1000). `--run-id` gắn batch vào một
`ops.pipeline_runs.run_id` đã tồn tại; có thể bỏ qua khi chạy thủ công.

Trạng thái `COMPLETE` và các bản ghi Bronze được commit cùng transaction sau khi
`bronze.snapshot_load_reconciliation.ready_for_transform` đạt. Nếu lỗi, transaction
nạp được rollback và một batch `FAILED` không chứa bản ghi được ghi để truy vết.
Chạy lại cùng snapshot chỉ trả `REUSED` khi batch `COMPLETE` hiện có vẫn đối soát
đạt. Nếu cùng `snapshot_id` nhưng manifest path, SHA-256 hoặc metadata khác,
module từ chối nạp.

Với `monthly_overview`, `row_count` trong manifest là số nhóm Bronze, còn
`source_row_count` là tổng `row_count` của các nhóm. View kiểm tra cả hai, tổng
`checkouts`, `inserted_rows` và việc mọi bản ghi thuộc batch `COMPLETE`. Batch
`FAILED` hoặc `STARTED` có bản ghi cũ làm gate thất bại để tránh dùng nhầm dữ liệu.

Kiểm thử tích hợp trên PostgreSQL Docker (test tự dọn snapshot tạm của mình):

```powershell
$env:PYTHONPATH = "src"
$env:BOOKSCOPE_TEST_DB = "1"
python -m unittest discover -s tests -p test_bronze_loading.py -v
```
