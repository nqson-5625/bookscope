# PostgreSQL cho BookScope

Docker Compose chạy PostgreSQL 16 trên `127.0.0.1:5433` mặc định. Dữ liệu nằm
trong named volume `bookscope_postgres_data`; `docker compose down` không xóa
volume. Mật khẩu local nằm trong `.env` (Git bỏ qua), được tạo bằng:

```powershell
python scripts/init_db_env.py
docker compose config --quiet
docker compose up -d --wait db
docker compose ps
```

Nếu dùng tên database, user hoặc port khác, sửa `BOOKSCOPE_DB_NAME`,
`BOOKSCOPE_DB_USER`, `BOOKSCOPE_DB_PORT` trong `.env` trước lần chạy đầu.
Biến `BOOKSCOPE_DB_PASSWORD` là bắt buộc. Mặc định có thể kết nối từ máy host
bằng `postgresql://bookscope:<mật-khẩu-trong-.env>@127.0.0.1:5433/bookscope`.
Container có healthcheck `pg_isready`; module tương lai có thể đặt
`depends_on: {db: {condition: service_healthy}}`.

## Bảng và khóa

File [DDL nền tảng](../sql/init/001_bronze_foundation.sql) tạo `ops`, `bronze`,
`silver`, `gold`; `silver` hiện do dbt tạo các model transformation, còn `gold`
để cho bước tiếp theo. View đối soát nằm trong
[SQL kế tiếp](../sql/init/002_checkout_reconciliation.sql). Các bảng hiện có:

| Bảng | Grain/khóa | Trách nhiệm |
| --- | --- | --- |
| `ops.pipeline_runs` | Một `run_id` | Trạng thái và thời gian chạy pipeline |
| `ops.data_snapshots` | Một `snapshot_id` | Danh mục snapshot Raw `COMPLETE` đã xác minh; manifest path/hash, kỳ, scope, số dòng |
| `bronze.load_batches` | Một lần thử nạp `batch_id` | Theo dõi STARTED/COMPLETE/FAILED; tối đa một batch COMPLETE mỗi snapshot |
| `bronze.checkout_records` | `(snapshot_id, source_row_number)` | Payload JSONB gốc và đường dẫn part; FK bảo đảm batch thuộc cùng snapshot |

`bronze.snapshot_load_reconciliation` chỉ cộng các bản ghi thuộc batch
`COMPLETE`, đối chiếu số dòng với manifest và `inserted_rows` của batch, rồi
đối chiếu tổng `checkouts`. `non_complete_batch_rows` báo số bản ghi thuộc batch
`STARTED` hoặc `FAILED`; nếu khác 0 thì gate thất bại. Giá trị `checkouts` thiếu
hoặc không phải số nguyên không âm cũng làm gate thất bại. Với
`monthly_overview`, view cộng `row_count` trong payload của các nhóm Bronze và
so với `source_row_count` trong manifest; giá trị thiếu hoặc không hợp lệ làm
`source_row_count_matches = false`. `row_count` của manifest vẫn là số nhóm Raw
cần nạp Bronze. Chỉ khi `ready_for_transform = true` mới chuyển snapshot sang
Silver.

Kiểm tra nhanh trên cấu hình mặc định:

```powershell
docker compose exec -T db psql -U bookscope -d bookscope -c '\dt bronze.*'
docker compose exec -T db psql -U bookscope -d bookscope -c 'SELECT * FROM bronze.snapshot_load_reconciliation LIMIT 5;'
Get-Content sql/checks/bronze_smoke.sql -Raw | docker compose exec -T db psql -U bookscope -d bookscope
```

DDL trong `/docker-entrypoint-initdb.d` chỉ chạy khi volume PostgreSQL còn
trống. Khi thay đổi schema sau này, tạo migration mới và áp dụng lên database
đang tồn tại; khởi động lại container không tự áp dụng lại file init. Không
dùng `docker compose down -v` nếu cần giữ dữ liệu.

Sau khi Silver được build, `python -m modules.data_quality run` áp dụng
[migration DQ](../sql/data_quality/001_results.sql) và tạo `ops.dq_runs`,
`ops.dq_results`, `ops.dq_source_revisions`, `ops.dq_gate` cùng hai view
`silver.dq_approved_*`. Trigger trên các bảng nguồn tăng revision khi có ghi;
gate DQ so với revision đã kiểm để chặn dữ liệu thay đổi trước khi Gold sử dụng.

Với volume đã tạo trước khi có gate `checkouts`, áp dụng file SQL mới mà không
xóa dữ liệu:

```powershell
Get-Content sql/init/002_checkout_reconciliation.sql -Raw | docker compose exec -T db psql -v ON_ERROR_STOP=1 -U bookscope -d bookscope
```

`bronze_loading` đăng ký manifest `COMPLETE` trong `ops.data_snapshots`, tạo
batch, ghi các bản ghi từ `raw_snapshot.iter_rows` và chỉ commit batch `COMPLETE`
cùng dữ liệu khi view đối soát đạt. Lần nạp lỗi được rollback và ghi batch
`FAILED` riêng. Retry không nhân đôi dòng vì batch `COMPLETE` đạt gate được tái
sử dụng và primary key `(snapshot_id, source_row_number)` chặn trùng. Xem
[hướng dẫn nạp Bronze](bronze_loading.md).
