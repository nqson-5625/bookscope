# Nghiệm thu nạp Bronze — 03/11/2026

Môi trường: PostgreSQL 16 trong Docker Compose local. Phạm vi: các snapshot Raw
`COMPLETE` mới nhất của 36 tháng 2023-01 đến 2025-12 cho hai stream.

## Kết quả

Raw audit chạy lại đạt `PASS`, 36 tháng, 0 vấn đề. Kiểm thử Python đạt 24/24,
gồm kiểm thử tích hợp PostgreSQL. Lệnh
`python -m modules.bronze_loading range --stream both` nạp thành công 72
snapshot; lần chạy lại trả `REUSED` cho cả 72.

| Stream | Tháng | Snapshot | Dòng manifest = Bronze | Checkouts manifest = Bronze | Số dòng nguồn manifest = Bronze | Qua gate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `monthly_overview` | 36 | 36 | 144 = 144 | 17.042.420 = 17.042.420 | 5.745.441 = 5.745.441 | 36/36 |
| `book_details` | 36 | 36 | 34.886 = 34.886 | 1.495.317 = 1.495.317 | Không áp dụng | 36/36 |

PostgreSQL có 35.030 bản ghi Bronze và 72 batch `COMPLETE`; không có batch
`STARTED` hoặc `FAILED`, bản ghi thuộc batch chưa hoàn tất, hay snapshot bị gate
chặn. Mỗi `snapshot_id` trong PostgreSQL khớp ID mới nhất theo tháng và stream
trong Raw audit; SHA-256 của cả 72 manifest trên đĩa khớp giá trị đã đăng ký.

Hai stream có phạm vi dữ liệu khác nhau, nên tổng `checkouts` của chúng được
đối chiếu riêng. Nghiệm thu này xác nhận lớp Bronze; các lớp Silver, Gold và
publication nằm ngoài phạm vi kiểm tra.
