# raw_snapshot

`raw_snapshot` quản lý lớp Raw trên filesystem: tạo thư mục theo
`stream × tháng × snapshot_id`, ghi `part-*.jsonl.gz` và manifest, rồi chỉ
chuyển sang `COMPLETE` sau khi kiểm tra checksum và số dòng. API ghi từ chối
sửa snapshot đã `COMPLETE`; lần tải mới tạo snapshot mới. Module này không gọi
SODA3 hay PostgreSQL và giữ nguyên định dạng các snapshot hiện có.

`source_ingestion` phụ trách truy vấn SODA3, phân trang, retry, chọn cohort,
đối soát nguồn và audit mapping. `bronze_loading` sẽ dùng reader offline của
`raw_snapshot` để nạp lại dữ liệu mà không gọi API.

Trong module, `schema.py` kiểm tra giá trị nguồn; `storage.py` ghi file và
manifest; `validation.py` xác minh snapshot; `replay.py` chọn, so sánh và đọc
lại snapshot. `snapshot.py` chỉ giữ các import tương thích với code cũ.

## CLI

Từ gốc repo, sau khi đặt `PYTHONPATH=src`:

```powershell
python -m modules.raw_snapshot verify data/raw/seattle_checkouts/monthly_overview/year=2023/month=01/snapshot=<id>
python -m modules.raw_snapshot select monthly_overview 2023-01
python -m modules.raw_snapshot compare <snapshot_A> <snapshot_B>
```

`verify` kiểm tra trạng thái `COMPLETE`, ID thư mục, đường dẫn part, SHA-256,
số dòng, tổng lượt mượn và đối soát theo từng stream. `select` chọn snapshot
`COMPLETE` mới nhất của một stream/tháng và xác minh trước khi trả về; nếu
snapshot mới nhất hỏng, lệnh báo lỗi thay vì âm thầm dùng bản cũ. `compare`
so sánh tập dòng logic của hai snapshot cùng phạm vi, không phụ thuộc thứ tự
dòng hoặc cách chia part; exit code 3 nghĩa là nội dung khác nhau. Các lệnh
này chạy offline, không cần token.

## API đọc lại cho Bronze

```python
from pathlib import Path
from modules.raw_snapshot import iter_rows, latest_complete_snapshot

path, manifest = latest_complete_snapshot(
    Path("data/raw/seattle_checkouts"), "book_details", "2023-01")
for record in iter_rows(path):
    # record.snapshot_id, record.source_row_number,
    # record.raw_path, record.source_payload
    ...
```

`iter_rows` xác minh toàn bộ snapshot trước khi phát dòng đầu tiên.
`source_row_number` bắt đầu từ 1 và tăng theo thứ tự part trong manifest;
Bronze dùng cặp `(snapshot_id, source_row_number)` làm khóa kỹ thuật. Với
`monthly_overview`, một dòng Raw là một nhóm `materialtype × usageclass`;
`source_row_count` trong manifest là số dòng SODA3 bên dưới nhóm, còn
`row_count` mới là số dòng cần nạp Bronze. Loader phải đối soát số dòng đã nạp
với `manifest["row_count"]`.

Reader giữ nguyên payload nguồn. Quyết định alias và MaterialType nằm trong
manifest/audit, không sửa các dòng Raw.
