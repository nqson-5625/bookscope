# BookScope

Hệ thống batch phân tích lượt mượn sách của Seattle Public Library. [Thiết kế kỹ thuật](docs/BookScope.pdf) quy định luồng: Seattle Open Data → Python ingestion → Raw JSONL.gz → PostgreSQL Bronze → dbt Silver/Gold → Python Data Science → Streamlit. Repo hiện triển khai module tuần 1 `project_foundation`; các module sau chưa được xây dựng.

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

Kiểm thử:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```
