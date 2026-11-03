# BookScope

Hệ thống batch phân tích lượt mượn sách của Seattle Public Library. [Thiết kế kỹ thuật](docs/BookScope.pdf) quy định luồng: Seattle Open Data → Python ingestion → Raw JSONL.gz → PostgreSQL Bronze → dbt Silver/Gold → Python Data Science → Streamlit. Repo hiện có `project_foundation`, `source_ingestion`, `raw_snapshot`, `bronze_loading`, `silver_transformation` và hạ tầng PostgreSQL; Gold và các lớp sau chưa được xây dựng.

## Chạy project_foundation

Yêu cầu Python 3.11+. Cài dependency và tạo file `.env` từ `.env.example`, sau đó điền `SOCRATA_APP_TOKEN`:

```powershell
python -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
# Mở .env và điền SOCRATA_APP_TOKEN
$env:PYTHONPATH = "src"
python -m modules.project_foundation
```

Lệnh truy vấn 3 tháng khảo sát, ghi `data/profiles/foundation_sample.jsonl`, `foundation_cohort_2023.jsonl` và `foundation_profile.json`. Ba tệp này được bỏ qua bởi Git vì dữ liệu thật có thể đổi; giữ bản cùng checksum khi làm bằng chứng nghiệm thu. Trích mẫu live 15 dòng và manifest ở `data/fixtures/` được lưu trong repo. Có thể profile lại offline:

```powershell
$env:PYTHONPATH = "src"
python -m modules.project_foundation --input data/profiles/foundation_sample.jsonl
```

Không đưa app token vào Git. G0 đã đạt cho việc bắt đầu `source_ingestion` trên ba tháng khảo sát; G1 phải kiểm tra đủ 36 tháng, phân trang và đối soát. Xem [tài liệu module](docs/project_foundation.md) về bằng chứng, phạm vi và giới hạn của mẫu; xem [hợp đồng dữ liệu](docs/data_contract.md) và [ERD sơ bộ](docs/erd.md).

## Chạy source_ingestion

Sau khi cấu hình `.env` và `PYTHONPATH` như trên:

```powershell
python -m modules.source_ingestion run --start 2023-01 --end 2023-01 --stream both
python -m modules.source_ingestion run --stream both --resume --title-batch-size 500
python -m modules.source_ingestion audit
python -m modules.raw_snapshot select monthly_overview 2023-01
```

Lệnh `run` mặc định tải cả 36 tháng; thêm `--resume` để tái dùng snapshot hợp lệ. Audit nguồn 02/11/2026 đã `PASS` trên 36 tháng, sau khi xử lý alias `Ebook → EBOOK` và rà soát các mã ngoài phạm vi; G1 toàn pipeline còn phụ thuộc Bronze. Xem [hướng dẫn ingestion](docs/source_ingestion.md), [hướng dẫn Raw snapshot](docs/raw_snapshot.md) và [quyết định phạm vi MaterialType](docs/material_type_review.md).

## PostgreSQL local

Khởi tạo PostgreSQL và các bảng `ops`/`bronze` bằng Docker Compose:

```powershell
python scripts/init_db_env.py
docker compose up -d --wait db
```

Xem [thiết kế bảng và cách kiểm tra database](docs/database.md).

## Nạp Bronze

Sau khi cài `requirements.txt`, tạo `.env` và khởi động PostgreSQL:

```powershell
$env:PYTHONPATH = "src"
$snapshot = python -m modules.raw_snapshot select monthly_overview 2023-01 | ConvertFrom-Json
python -m modules.bronze_loading load $snapshot.path
python -m modules.bronze_loading range --start 2023-01 --end 2023-01 --stream both
```

Lệnh `range` chọn snapshot Raw `COMPLETE` mới nhất cho từng stream/tháng; chạy lại
sẽ báo `REUSED` khi batch đã nạp và đối soát đạt. Xem [hướng dẫn nạp Bronze](docs/bronze_loading.md).

Kiểm thử:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

## Silver contract và dbt

[Silver Data Contract và Logical Schema](docs/silver_contract.md) chốt grain,
khóa, cột chuẩn hóa, snapshot hiệu lực, validation và quarantine cho hai
stream. Project [`dbt_bookscope`](dbt_bookscope/dbt_project.yml) đã khai báo
Bronze sources và kết nối PostgreSQL local. Sau khi cài dependencies vào
`.venv` và cấu hình `.env`:

```powershell
.\.venv\Scripts\python.exe scripts/run_dbt.py debug
.\.venv\Scripts\python.exe scripts/run_dbt.py ls --resource-type source
```

Chạy đầy đủ Raw audit, đối chiếu registry và dbt Silver:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m modules.silver_transformation run
.\.venv\Scripts\python.exe -m modules.silver_transformation status
```

Xem [hướng dẫn dbt](docs/dbt_setup.md) và [tài liệu Silver](docs/silver_transformation.md).
