# Silver Data Contract và Logical Schema — v1_2026_11_03

Trạng thái: **chốt cho triển khai `silver_transformation`**. Phạm vi là 36 tháng
2023-01–2025-12, hai stream đã nghiệm thu Bronze. Đây là hợp đồng logic;
`silver` hiện chưa có model hoặc bảng vật lý. Mọi thay đổi grain, mapping hay
quy tắc reject phải tăng `contract_version` và chạy lại quality gate.

## Phạm vi, grain và khóa

| Quan hệ logic | Grain | Khóa duy nhất | Vai trò |
| --- | --- | --- | --- |
| `silver.overview_groups` | Một nhóm nguồn `materialtype × usageclass` trong một snapshot `monthly_overview` | `(snapshot_id, source_row_number)`; kiểm tra thêm `(snapshot_id, materialtype_raw, usageclass_raw)` | Tổng `checkouts` và `source_row_count` của toàn bộ sách đủ điều kiện, scope `ALL_ELIGIBLE_AGG` |
| `silver.detail_records` | Một dòng nguồn trong một snapshot `book_details` | `(snapshot_id, source_row_number)`; kiểm tra thêm `(snapshot_id, source_id)` với `source_id = payload[':id']` | Dòng detail của cohort cố định, scope `SELECTED_DETAIL` |
| `silver.quarantine_records` | Một lỗi trên một dòng Bronze theo phiên bản rule | `(snapshot_id, source_row_number, rule_code, contract_version)` | Lưu mọi dòng bị từ chối, nhiều reason nếu cần |
| `silver.snapshot_quality` | Một snapshot theo contract version | `(snapshot_id, contract_version)` | Số dòng hợp lệ/reject, tổng và kết quả gate |

Không dùng `title`, `creator`, `materialtype`, `checkouttype`, ISBN hay Subjects
để xóa trùng detail. Nhiều dòng cùng tuple là hợp lệ; `:id` chỉ là khóa kiểm
tra trong snapshot, chưa chứng minh là định danh sách/ấn bản ổn định qua tháng.
`book_record_key` và phép gộp cohort thuộc bước downstream sau khi profile
identity riêng. Không cộng hai scope hoặc dùng tổng detail làm tổng thư viện.

## Cột chuẩn hóa

Mọi dòng Silver hợp lệ có `snapshot_id` (text), `source_row_number` (bigint),
`stream`, `scope_id`, `month_start` (date, ngày 1), `raw_path`,
`materialtype_raw` (text giữ nguyên), `format_key` (`PRINT_BOOK`/`EBOOK`),
`usageclass_raw` (text giữ nguyên), `usage_class` (text sau trim, không đổi
hoa/thường), `checkouts` (bigint không âm), `mapping_version` lấy từ
`ops.data_snapshots`, và `contract_version = v1_2026_11_03`. `month_start` lấy
từ `ops.data_snapshots.period`, không suy từ ngày chạy dbt.

| Quan hệ | Cột thêm | Quy tắc |
| --- | --- | --- |
| `overview_groups` | `source_row_count` bigint | Parse `payload.row_count`, phải > 0; đây là **số dòng nguồn bên dưới nhóm**, không phải số dòng Bronze. |
| `detail_records` | `source_id`, `title_raw`, `title_display`, `title_search`, `creator_raw`, `creator_display`, `checkouttype_raw`, `checkout_type`, `isbn_raw`, `subjects_raw`, `publisher_raw`, `publication_year_raw` | `source_id` lấy từ `:id`; `title_display`/`creator_display` trim; `title_search` lower + gộp khoảng trắng để tìm kiếm, tuyệt đối không làm khóa; các metadata khác giữ chuỗi nguồn, missing thành NULL. `checkout_type` trim nhưng giữ nguyên hoa/thường. |

`materialtype_raw` chỉ nhận năm mã chính xác trong
`configs/project_foundation.json`: `BOOK`, `REGPRINT`, `LARGEPRINT` →
`PRINT_BOOK`; `EBOOK`, `Ebook` → `EBOOK`. Mã `Ebook` có thể dùng alias `EBOOK`
để **đối chiếu cohort**, song giá trị nguồn vẫn được giữ. Không phân tách mã
kết hợp và không tự thêm mã khác. `mapping_version` hiện là `v1_2026_11_02`;
cohort matching là `v2_ebook_case_alias`. Khi version ở registry khác version
được duyệt, chặn snapshot cho đến khi cập nhật rule và audit.

## Chọn snapshot hiệu lực

1. Xét riêng từng `(stream, scope_id, period)`; stream/scope phải là
   `monthly_overview/ALL_ELIGIBLE_AGG` hoặc `book_details/SELECTED_DETAIL`.
2. Snapshot ứng viên phải có `status = COMPLETE`, đúng mapping version và
   `bronze.snapshot_load_reconciliation.ready_for_transform = true`. Gate này
   xác nhận batch COMPLETE, số dòng, tổng checkouts và `source_row_count` của
   overview; kiểm tra hash manifest vẫn thuộc Raw audit.
3. Chọn **một** snapshot mới nhất theo `completed_at DESC, extracted_at DESC,
   snapshot_id DESC`. Nếu snapshot COMPLETE mới hơn chưa qua gate, đánh dấu
   tháng/scope bị chặn, không âm thầm quay về bản cũ để publish. Một lần chạy
   phải ghi bộ 72 `snapshot_id` đã chọn để tái lập; bản công bố cũ giữ bộ ID cũ.
4. Không trộn snapshot của hai tháng hoặc hai scope, không cộng snapshot cũ và
   mới. Detail phải được đối chiếu với đúng cohort checksum/alias trong Raw
   audit. Registry PostgreSQL hiện chưa chứa cohort checksum, nên gate publish
   sau này phải đọc bằng chứng Raw hoặc lưu provenance đó trước khi công bố.

## Validation và quality gate

| Rule | Mức | Xử lý |
| --- | --- | --- |
| `SNAPSHOT_NOT_READY`, `VERSION_MISMATCH`, thiếu tháng/scope | BLOCK snapshot | Không đọc sang Silver/publish; ghi `snapshot_quality`. |
| `INVALID_PERIOD`, `INVALID_CHECKOUTS`, `BIGINT_OVERFLOW`, `UNKNOWN_MATERIAL`, `BLANK_USAGE_CLASS` | BLOCK row và snapshot | Đưa dòng vào quarantine, kèm mã rule và payload; không cộng dòng lỗi. |
| `INVALID_SOURCE_ROW_COUNT`, `DUPLICATE_OVERVIEW_GROUP` | BLOCK row và snapshot | Overview cần `row_count > 0`, mỗi nhóm nguồn duy nhất trong snapshot. |
| `DETAIL_PERIOD_MISMATCH`, `MISSING_SOURCE_ID`, `DUPLICATE_SOURCE_ID`, `BLANK_TITLE`, `BLANK_CHECKOUT_TYPE` | BLOCK row và snapshot | Detail phải có year/month đúng `period`, ID và title/checkout type có nội dung; giữ tất cả dòng khác nhau cùng tuple. |
| `MISSING_CREATOR`, `MISSING_ISBN`, `MISSING_SUBJECTS`, `MISSING_PUBLISHER`, `MISSING_PUBLICATION_YEAR` | WARN | Giữ dòng với NULL; ghi số lượng, không tự điền metadata. |
| Sai số dòng/tổng `checkouts`/tổng `source_row_count` với manifest, hoặc trùng khóa Silver | BLOCK snapshot | Đối soát **theo snapshot** và theo `month_start × format_key` trong cùng scope; không che sai lệch bằng cách gộp khác scope. |

Chỉ parse số nguyên thập phân không dấu, không âm, nằm trong `bigint`; `0`
checkouts hợp lệ và khác NULL. Không ép giá trị sai thành 0. Overview không có
`checkoutyear/checkoutmonth` trong payload nên kỳ lấy từ registry; detail phải
kiểm tra hai cột nguồn khớp kỳ đó. Mọi dòng Bronze của snapshot được phân
loại vào valid hoặc quarantine, không mất dòng. Gate pass khi reject = 0,
không có lỗi cấp snapshot, các khóa duy nhất và tổng khớp Bronze/manifest.
WARN được thống kê riêng, không làm mất dòng. `silver_transformation` phải
hiện thực các rule này bằng model và dbt tests trước khi Gold dùng dữ liệu.

## Quarantine và truy vết

Mỗi bản ghi quarantine giữ `snapshot_id`, `source_row_number`, `stream`,
`scope_id`, `month_start`, `rule_code`, `contract_version`, `raw_path`,
`source_payload` JSONB, `observed_value` (nếu có) và thời điểm chạy. Không
sửa/xóa Bronze. Quarantine phải idempotent theo khóa logic; khi sửa rule, tạo
version mới và chạy lại snapshot. `snapshot_quality` ghi số Bronze, valid,
reject, số WARN, tổng checkout/source-row ở từng phía, `mapping_version`,
`contract_version`, trạng thái PASS/BLOCK và lý do. Mọi reject dù chỉ một dòng
đều chặn công bố của snapshot; không dùng tổng thiếu hụt làm số liệu Gold.

Các source dbt tương ứng đã khai báo tại
[`dbt_bookscope/models/sources.yml`](../dbt_bookscope/models/sources.yml).

Kiểm tra PostgreSQL local ngày 03/11/2026: 36/36 snapshot mỗi stream có
`ready_for_transform = true` và `mapping_version = v1_2026_11_02`;
detail không có title/checkout type/usage class rỗng hay `:id` trùng trong
snapshot; overview không có `row_count <= 0` hay nhóm nguồn trùng.
