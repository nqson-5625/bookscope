# Gold integrity và concurrency — 06/11/2026

**Kết quả: PASS.** Sau khi sửa Published Data Protection và Gold Build
concurrency, `ops.current_publication` trỏ tới
`054b82c4b0f5be802d27f72d6537e480ba09aadef824206e6def46a3ee801fee`.
DQ Gate vẫn PASS 72/72, 0 BLOCK, `source_state_matches=true`. Output digest
`1c588aa7c76d2a09686d7c3d7a2c6c07bb1ab0ce6dcab06408f6f790a3bd3e3a`
khớp trên cả năm version PUBLISHED.

## Published Dimension Protection

[Migration](../sql/gold/001_publications.sql) tạo trigger cho
`gold.dim_month`, `gold.dim_format`, `gold.dim_book_record`. UPDATE/DELETE
một dòng đang được bất kỳ version PUBLISHED nào sử dụng sẽ bị từ chối;
TRUNCATE cũng bị chặn. INSERT dòng mới hợp lệ vẫn được phép. Trigger và
publication dùng cùng advisory transaction lock; khi promotion đang giữ khóa,
thao tác sửa Dimension sẽ thất bại nhanh thay vì chờ rồi ghi đè dữ liệu.

Integration test thực hiện UPDATE và DELETE trên cả ba Dimension, xác nhận
cả sáu lệnh đều bị chặn. Test tính lại digest vật lý cho toàn bộ năm version
PUBLISHED và khớp `ops.data_publications.output_sha256`. Fixture tiếp theo
INSERT tháng 2026-01 và một book record mới, cập nhật/xóa hai dòng chưa được
công bố, rồi rollback; các thao tác hợp lệ đều đạt. `dim_format` chỉ chấp
nhận hai mã EBOOK và PRINT_BOOK, cả hai đã tồn tại theo contract. Migration
chạy lại sau khi đã có dữ liệu PUBLISHED vẫn PASS và trigger tiếp tục hoạt động.

## Concurrent Gold Builds

Mỗi build có `manifest.json`, `run_results.json` và log tại
`dbt_bookscope/target_gold/<build_id>/`; đường dẫn được Git bỏ qua. Runner
chỉ đọc đúng thư mục của build, kiểm tra invocation ID giữa hai artifact,
schema staging của tám model và danh tính 31 data tests/1 unit test. SHA-256
hai artifact và đường dẫn được lưu trong `ops.gold_builds.dbt_results`.

Advisory session lock bao quanh toàn bộ Gold build, từ pin snapshot đến khi
dọn staging. Hai tiến trình cùng được khởi chạy trong integration test:

| Build ID | Kết quả | Bắt đầu / kết thúc (giờ Việt Nam) |
| --- | --- | --- |
| `457bc377-8f05-44e2-813a-7c54803b0f4e` | REUSED | 23:19:20 / 23:20:35 |
| `bfce66b3-6ac9-4b7b-91bd-4f9ef3dbb15c` | REUSED | 23:20:37 / 23:21:53 |

PostgreSQL ghi nhận một tiến trình chờ advisory lock trong lúc tiến trình kia
đang chạy. Hai khoảng build không chồng nhau; invocation ID khác nhau,
`run_results.json` ở hai thư mục riêng và checksum trên đĩa khớp metadata DB.
Cả hai cho cùng `data_version` và output digest, không đổi publication pointer,
không thêm version PUBLISHED và không tăng số dòng Book lineage.

Build đầu sau thay đổi: `fb985abd-7c3b-47f6-9d25-aeaebcc36187`, 8/8 models,
31/31 data tests, 1/1 unit test, `PUBLISHED`; 36 tháng, 72 Overview rows,
31.402 Book Monthly Stats, 36.000 Features, 144 Overview lineage và 34.886
Book lineage. Tổng checkouts hiện hành vẫn là 17.042.420 Overview và
1.495.317 Detail. Xem [hướng dẫn vận hành](gold_analytics_marts.md) và
[Gold Contract](gold_contract.md).
