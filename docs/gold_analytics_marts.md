# Gold Analytics Marts — vận hành và publication

Module triển khai tám dbt models, một dbt unit test, 31 dbt data tests và
Python runner. Phạm vi hiện hành: 36 tháng 01/2023–12/2025, hai stream với
72 snapshot đã DQ phê duyệt. [Gold Contract](gold_contract.md) quy định grain,
identity và chính sách version; [G3](gold_g3_acceptance_2026-11-04.md) ghi
kết quả nghiệm thu ban đầu. Bằng chứng sửa Dimension và build đồng thời ở
[Gold integrity acceptance](gold_integrity_2026-11-06.md).

## Chạy từ thư mục gốc repo

Yêu cầu PostgreSQL đang hoạt động, `.env` có `BOOKSCOPE_DB_PASSWORD`, dbt và
Python dependencies từ `requirements.txt`. Trước Gold, chạy Silver và DQ khi
nguồn đã thay đổi. DQ gate phải PASS 72/72, 0 BLOCK, source state khớp.

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -B -m modules.gold_analytics_marts migrate
.\.venv\Scripts\python.exe -B -m modules.gold_analytics_marts status
.\.venv\Scripts\python.exe -B -m modules.gold_analytics_marts build
.\.venv\Scripts\python.exe -B -m modules.gold_analytics_marts replay
```

`migrate` có thể chạy lại. `build` pin 72 snapshot và DQ run trong
`ops.gold_builds`/`ops.gold_build_pins`, xác minh 72 manifest cùng cohort hash,
chạy dbt trong `gold_stage_<build_id>`, kiểm tra đủ danh tính 8 model, 31 data
tests và 1 unit test, đối soát lại với hai view Silver DQ-approved, rồi tính
output digest. Trong transaction, runner khóa revision nguồn/DQ run, kiểm tra
pin lần cuối, copy staging sang các bảng `gold` versioned, so lại digest,
đánh dấu PUBLISHED và đổi `ops.current_publication`. Lỗi rollback con trỏ;
staging được dọn sau khi chạy.

Mỗi build lưu `manifest.json`, `run_results.json` và log dưới
`dbt_bookscope/target_gold/<build_id>/`; runner chỉ đọc artifact của đúng
`build_id`, kiểm tra invocation ID và schema staging rồi lưu checksum artifact
vào `ops.gold_builds.dbt_results`. Một khóa PostgreSQL giữ toàn bộ Gold build
theo thứ tự, tránh hai dbt Gold tiến trình cùng dùng RAM trên máy 8 GB.
Artifact được giữ để tra cứu; thư mục này được Git bỏ qua.

Ba Dimension được bảo vệ bằng trigger: UPDATE/DELETE dòng đã được version
PUBLISHED dùng sẽ thất bại. Có thể INSERT dòng mới hợp lệ; `dim_format` chỉ
chấp nhận `EBOOK` và `PRINT_BOOK` nên không có mã thứ ba hợp lệ theo contract.

`replay` thực hiện lại toàn bộ build và yêu cầu nhận `REUSED` với cùng
`data_version` đang công bố; không đổi con trỏ hoặc nhân đôi hàng. Khi logic
hoặc snapshot thay đổi hợp lệ, chạy `build` để tạo version mới. Các version
PUBLISHED cũ vẫn ở database. `status` chỉ đọc gate, con trỏ và số version.
Không dùng dbt build riêng lẻ để công bố dữ liệu; macro đặt lệnh thủ công vào
`gold_sandbox` và không chạy publication gate.

## Truy vấn versioned

```sql
SELECT data_version, published_at, output_sha256
FROM ops.data_publications ORDER BY published_at DESC;

SELECT * FROM gold.current_monthly_overview
WHERE month_start = DATE '2025-12-01';

SELECT count(*), sum(checkouts) FROM gold.book_monthly_stats
WHERE data_version = (SELECT data_version FROM ops.current_publication);

SELECT build_id, status, dbt_results ->> 'test_count' AS data_tests,
       dbt_results ->> 'unit_test_count' AS unit_tests,
       output_sha256, error_text
FROM ops.gold_builds ORDER BY started_at DESC;
```

`gold.current_*` có ba view cho Overview, Book Monthly Stats và Features.
Lineage và snapshot pin truy vấn theo `data_version` để tái lập bản đã công bố.
`book_record_key` là khóa tuple cohort, không phải tác phẩm hoặc ấn bản thư
mục. `metric_status` JSONB ghi trạng thái riêng cho từng KPI; tháng không có
dòng Detail mang `observed=false`, `checkouts=NULL` trong Features.

## Kiểm thử

```powershell
$env:PYTHONPATH = 'src'
$env:BOOKSCOPE_TEST_DB = '1'
.\.venv\Scripts\python.exe -B -m unittest tests.test_gold_analytics_marts -v
```

Integration tests dùng transaction rollback cho fixture DQ BLOCK, snapshot
mới, pin thiếu, trigger bảo vệ bản đã công bố và hai vị trí lỗi promotion.
Test kiểm tra UPDATE/DELETE trên cả ba Dimension và INSERT bản ghi mới;
hai Gold build khởi chạy đồng thời phải được thực hiện tuần tự, có artifact
riêng và trả cùng version khi replay. Test replay chạy dbt Gold thêm một lần
trên dữ liệu thật. Kết quả dbt/test,
digest và validation được lưu trong `ops.gold_builds`; dữ liệu versioned và
`ops.publication_snapshots` giữ provenance lâu dài.
