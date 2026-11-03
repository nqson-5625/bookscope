# dbt cho BookScope

Project nằm trong `dbt_bookscope/`; profile PostgreSQL nằm cùng project và
dùng biến `BOOKSCOPE_DB_*` của `.env` ở gốc repo. Mật khẩu không được ghi trong
Git. Adapter hiện được kiểm chứng: `dbt-core 1.11.10` + `dbt-postgres 1.10.0`.
`requirements.txt` giới hạn hai dòng phiên bản tương thích.

Từ thư mục gốc repo, khi PostgreSQL đã chạy và `.env` có
`BOOKSCOPE_DB_PASSWORD`:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/run_dbt.py debug
.\.venv\Scripts\python.exe scripts/run_dbt.py parse
.\.venv\Scripts\python.exe scripts/run_dbt.py ls --resource-type source
```

`scripts/run_dbt.py` nạp biến còn thiếu từ `.env`, ưu tiên biến môi trường
đang đặt và truyền thư mục project/profile cho dbt. Mọi lệnh dbt khác cũng
chạy qua wrapper này, ví dụ `python scripts/run_dbt.py test --select source:*`.
Nếu dùng dbt trực tiếp, cần tự export biến `.env` rồi truyền `--project-dir`
và `--profiles-dir` sau lệnh dbt.

`models/sources.yml` khai báo `ops.data_snapshots`,
`bronze.checkout_records`, `bronze.load_batches` và
`bronze.snapshot_load_reconciliation`. Model dòng hợp lệ trong `models/silver/`
chỉ đọc snapshot mới nhất đã qua gate; model quality ghi cả snapshot bị chặn
theo [Silver contract](silver_contract.md).
Chạy `python -m modules.silver_transformation run` để kiểm tra Raw audit và
manifest đăng ký trước `dbt build`; xem [hướng dẫn Silver](silver_transformation.md).
`dbt debug` xác nhận cấu hình/kết nối, `dbt parse` và `dbt ls` xác nhận source.

Sau `silver_transformation run`, chạy
`python -m modules.data_quality run` để lưu lịch sử dbt tests, đối chiếu
Raw–Bronze–Silver theo định dạng và mở gate DQ. Xem
[hướng dẫn Data Quality](data_quality.md).

Đầu vào `gold_analytics_marts` đã chuẩn bị tại `models/gold/` và
[`dbt_project.yml`](../dbt_bookscope/dbt_project.yml): local `dev` ánh xạ
model Gold vào schema `gold` qua `generate_schema_name`, Silver giữ `silver`;
CI giữ tiền tố target schema. DQ approved views được khai báo là dbt sources;
Gold dùng `source('silver', 'dq_approved_overview_groups')`,
`source('silver', 'dq_approved_detail_records')` và
`source('ops', 'dq_gate')`.
Xem [Gold Entry Gate](gold_entry_gate_2026-11-04.md). Trước khi có model Gold,
dbt báo một unused config path `models.dbt_bookscope.gold` như dự kiến.

Ngày 03/11/2026 trên PostgreSQL local: `dbt debug` trả `All checks passed!`,
adapter `postgres=1.10.0`, `Connection test: OK connection ok`. Không có thao
tác sửa dữ liệu khi chạy debug/parse/ls. `dbt parse` thành công, `dbt ls`
nhận diện 4 sources, và `dbt test --select 'source:*'` đạt 14/14 tests.
