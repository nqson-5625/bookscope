# source_ingestion

Module này đọc SODA3 bằng POST với `SOCRATA_APP_TOKEN`, chọn cohort cố định theo tổng lượt mượn năm 2023, rồi tải hai luồng theo tháng 2023–2025. Việc ghi và xác minh snapshot do [`raw_snapshot`](raw_snapshot.md) đảm nhiệm; mỗi lần tải tạo thư mục mới và không ghi đè snapshot cũ.

Code được chia theo trách nhiệm: `client.py` gọi SODA3, `selection.py` quản lý truy vấn/cohort, `overview.py` và `detail.py` trích xuất từng luồng, `extract.py` điều phối một tháng, `audit.py` kiểm tra toàn dải. `ingest.py` chỉ giữ các import tương thích với code cũ.

## Chạy

Từ thư mục gốc repo, dùng Python 3.11+:

```powershell
python -m pip install -r requirements.txt
$env:PYTHONPATH = "src"
# Tạo .env từ .env.example và điền SOCRATA_APP_TOKEN trước khi gọi API
python -m modules.source_ingestion cohort
python -m modules.source_ingestion run --start 2023-01 --end 2023-01 --stream both
python -m modules.source_ingestion run --stream both --resume --title-batch-size 500
python -m modules.source_ingestion audit
```

`run` mặc định tải đủ 36 tháng. Có thể chỉ định `--start`, `--end`, `--stream overview|detail|both`, `--page-size`, `--title-batch-size`. `--resume` tái dùng snapshot COMPLETE đã xác minh với cùng quy tắc trích xuất; bỏ cờ này để tạo snapshot mới khi cần kiểm tra nguồn cập nhật ngược. Chạy `python -m modules.raw_snapshot verify <thư_mục_snapshot>` để xác minh một snapshot không cần Internet hay token. `audit` kiểm tra snapshot COMPLETE mới nhất cho cả hai luồng ở từng tháng, hash/tổng dòng, mapping và từng dòng detail thuộc cohort theo chính sách alias; báo số tuple cohort xuất hiện/thiếu theo tháng. Quyết định loại trừ mã được áp dụng theo cấu hình hiện tại để có thể rà soát lại mà không sửa snapshot Raw. Kết quả nằm trong `data/raw/seattle_checkouts/ingestion_audit.json`. Exit code 2 nghĩa là còn vấn đề chặn phần ingestion của G1.

`python -m modules.raw_snapshot compare <snapshot_A> <snapshot_B>` xác minh và so sánh nội dung logic của hai snapshot cùng tháng/scope, bỏ qua khác biệt thứ tự dòng và cách chia part. Exit code 0 là cùng nội dung; 3 là khác nội dung và cần xem nguồn có cập nhật ngược hay không. Lệnh này chạy offline.

## Dữ liệu và giới hạn

- `monthly_overview`: file `part-*.jsonl.gz` gồm các nhóm nguồn `materialtype × usageclass` thuộc mapping v1. `row_count` trong manifest là số nhóm Raw; `source_row_count` là số bản ghi nguồn đủ điều kiện. `source_totals` được truy vấn độc lập và đối soát với tổng nhóm. `format_totals` cho `PRINT_BOOK` và `EBOOK`; `source_material_distribution` giữ đủ nhóm kể cả mã ngoài mapping để rà soát.
- `book_details`: cohort là tối đa 1.000 tuple `(title, creator, materialtype, checkouttype)` xếp theo tổng checkouts năm 2023. Mỗi tháng truy vấn theo từng nhóm title, phân trang bằng `ORDER BY :id`, đối soát số dòng và checkouts của mỗi nhóm title với nguồn, rồi chỉ lưu những dòng khớp cohort. Quy tắc matching `v2_ebook_case_alias` coi `Ebook` và `EBOOK` là cùng mã khi đối chiếu cohort; giá trị nguồn trong Raw vẫn giữ nguyên. Đây chỉ là alias của mã định dạng, không chứng minh các dòng là cùng ấn bản. Các dòng trùng tuple vẫn được giữ nguyên. `:id` là ID nội bộ nguồn; thứ tự dòng qua các part là `source_row_number` 1-based cho loader Bronze sau này. `coverage_percent` và `coverage_by_format_percent` đo độ phủ checkouts của cohort so với tổng đủ điều kiện.
- Manifest ghi `snapshot_id`, URL, query, phiên bản mapping/config/matching, log trang, hash SHA-256 và số dòng từng part. Chỉ ghi `COMPLETE` sau khi tự xác minh checksum, số dòng và đối soát. Lỗi tải hoặc đối soát để lại manifest `FAILED`. Tháng cũ có thể tải lại thành snapshot mới; downstream chọn snapshot COMPLETE mới nhất theo tháng và scope, không cộng dồn.
- `reviewed_excluded_material_types` trong `configs/source_ingestion.json` chứa 45 mã đã được rà soát là **ngoài phạm vi năm mã chính xác của MVP**; một số mã này vẫn có thể chứa sách. Mã mới được liệt kê ở `unreviewed_material_types`; `ingestion_audit.json` tổng hợp số tháng, dòng và lượt mượn theo từng mã tại `material_type_review`. Cần rà soát từng mã trước khi thêm vào mapping hoặc danh sách loại trừ. `mapping_review_status` trong snapshot phản ánh chính sách tại lúc tải; trạng thái hiện hành để công bố là `current_mapping_review_status` trong audit, được tính từ cấu hình duyệt mới nhất. Xem [quyết định và giới hạn phạm vi](material_type_review.md).

Snapshot và cohort trong `data/raw/` không đưa vào Git; giữ bản sao và checksum khi nộp bằng chứng. `source_ingestion` chưa nạp PostgreSQL hoặc publish Gold: đó là trách nhiệm các module sau. API có thể sửa dữ liệu trong lúc đang phân trang; thứ tự `:id`, tổng nguồn và ID biên trang giúp phát hiện nhiều sai lệch nhưng không đảm bảo snapshot giao dịch của phía Socrata. Tải lại kỳ bị lệch.

## Bằng chứng live 02/11/2026

Đã tải 36/36 snapshot overview và 36/36 snapshot detail. Audit offline hiện `PASS` cho phần ingestion: checksum, số dòng, tổng checkouts, mapping, cohort và coverage theo tháng/định dạng đều khớp. Cohort 2023 có 1.000 tuple, checksum trùng bằng chứng khảo sát (`74c597...`). Tháng 2023-01 có 162.802 dòng nguồn đủ điều kiện, 525.136 lượt mượn và 734 dòng detail; thử trang 100 dòng cho ra 8 trang/8 part và vẫn đối soát đúng. Coverage tháng dao động 4,5481%–14,1074% (trung vị 7,9669%); các xếp hạng detail chỉ có giá trị trong cohort.

Hai snapshot detail 2023-01 dùng kích thước trang khác nhau đã cho cùng `logical_sha256=423234ac...` (734 dòng, 59.411 lượt mượn), chứng minh replay logic cho kỳ thử nghiệm. Hash file nén từng part có thể khác do cách chia trang.

Nguồn dùng `Ebook` thay `EBOOK` vào 2024-05, 2024-06 và 2025-08. Alias có phiên bản khôi phục độ phủ ebook lần lượt 11,2936%, 11,1517%, 6,9309% trong ba tháng đó; manifest ghi số dòng/checkout được ghép bằng alias. Snapshot nằm trong `data/raw/` cục bộ, không nằm trong Git. `PASS` ở đây xác nhận module nguồn và snapshot Raw; G1 toàn pipeline vẫn cần `bronze_loading` và đối soát ở Bronze.

Trong 1.000 tuple cohort, audit thấy 669 tuple ở 2023-01, 852 ở 2024-06 và 863 ở 2025-08; số thiếu phản ánh tuple nguồn không xuất hiện đúng metadata trong tháng, không tự suy ra tài liệu không được mượn. Cần xem chi tiết khi phân tích lịch sử và biến thể title.

Xem [hợp đồng dữ liệu](data_contract.md), [khảo sát nguồn](project_foundation.md), và [SODA3 Query API](https://dev.socrata.com/docs/queries/).
