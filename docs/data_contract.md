# Hợp đồng dữ liệu sơ bộ

Hợp đồng này cụ thể hóa mục 3, 5–7 của [thiết kế](BookScope.pdf). Schema bên dưới đã được quan sát trong mẫu live 2023-01, 2024-06, 2025-01; các tháng khác cần kiểm tra trong ingestion. Dataset `tmmm-ytt6` dùng tên trường API viết thường.

| Field nguồn | Kiểu dự kiến | Quy tắc | Vai trò |
| --- | --- | --- | --- |
| `usageclass` | text | Giữ nguyên; mapping Physical/Digital sau profile | format/context |
| `checkouttype` | text | Giữ nguyên; có thể là phần của identity | grain |
| `materialtype` | text | Dùng mapping có phiên bản: BOOK/REGPRINT/LARGEPRINT → PRINT_BOOK; EBOOK/Ebook → EBOOK; mã khác cần rà soát trước khi bổ sung | eligibility |
| `checkoutyear`, `checkoutmonth` | integer dạng chuỗi/số | Năm trong phạm vi, tháng 1–12; tạo `month_start` | time |
| `checkouts` | integer dạng chuỗi/số | Không âm; zero khác missing | measure |
| `title` | text | Không rỗng với detail; giữ nguyên và có search field riêng | identity/display |
| `creator` | text nullable | Missing được đo và ghi cờ; không tự gán tác giả | identity/context |
| `isbn` | text nullable | Có thể nhiều ISBN hoặc thiếu; không dùng làm khóa toàn cục | metadata |
| `subjects` | text nullable | Có thể nhiều giá trị; không join trước khi aggregate | metadata |
| `publisher`, `publicationyear` | text nullable | Giữ nguyên chuỗi nguồn | metadata |

| Lớp | Grain và khóa | Bắt buộc/đối soát | Truy vết |
| --- | --- | --- | --- |
| Raw | Một snapshot theo stream × tháng × scope; nhiều part | JSONL.gz đọc được; SHA-256 và số dòng khớp; trạng thái COMPLETE | `snapshot_id`, query, source URL, extracted_at, code/config version, page log |
| Bronze | Một dòng nguồn × snapshot; unique `(snapshot_id, source_row_number)` | Payload JSONB và số dòng bằng Raw | `raw_path`, `snapshot_id`, `source_row_number` |
| Silver | Một dòng nguồn hợp lệ sau parse/dedup; quarantine riêng | Month/checkouts hợp lệ; rule identity/dedup versioned; reject có reason | source key, snapshot, scope, mapping version |
| Gold overview | `month_start × format_key × scope_id × data_version` | Không trùng key; tổng checkouts đối soát theo đúng scope | publication/data version, source snapshot |
| Gold detail | `book_record_key × month_start × format_key × scope_id × data_version` (thêm channel nếu profile chứng minh cần) | Không trùng grain; không nhân bản do Subjects | source identity, mapping version |
| DS | Book/key × evaluation month × scope × data/model version | Score có components, evidence, reliability, reason/limitation codes | Gold version và incident policy |

`ALL_ELIGIBLE_AGG` và `SELECTED_DETAIL` không được cộng hoặc so sánh trực tiếp như cùng một tổng thể. `row_count` và tổng checkouts phải đối soát từng tháng và định dạng; reject được báo riêng. Sự thiếu ISBN/Subjects là WARN, lỗi checkouts/grain/checksum/reconciliation là BLOCK. Gold chỉ publish sau khi quality gate toàn pipeline đạt. Khi snapshot mới thay tháng cũ, record hiệu lực được chọn theo **snapshot COMPLETE mới nhất cho tháng/scope đã tải đủ**; publication trước vẫn truy xuất được.

Profile live ngày 02/11/2026 cho thấy cùng tuple `(title, creator, materialtype, checkouttype)` có thể có nhiều dòng nguồn trong một tháng (ví dụ `Pride and Prejudice` có 8 dòng `EBOOK` vào 2025-01). Tuple này dùng để chọn cohort, **không phải khóa duy nhất của Raw/Bronze và không phải bằng chứng rằng các dòng là bản ghi trùng cần xóa**. Giữ từng dòng cùng `source_row_number`; Silver chỉ gộp lượt mượn sau khi xác định rõ grain và không dùng `ISBN` hoặc `Subjects` làm dedup key. Mapping chỉ được kiểm chứng trên ba tháng khảo sát; ingestion phải phát hiện mã mới ở các tháng khác.

Khi truy hồi cohort qua các tháng, ingestion áp dụng alias có phiên bản `Ebook → EBOOK` vì nguồn dùng hai cách viết ở những tháng khác nhau. Alias chỉ dùng để đối chiếu tuple đã chọn; Raw luôn giữ `materialtype` gốc và báo số dòng/lượt mượn được ghép bằng alias. Các mã in khác như `BOOK`, `REGPRINT`, `LARGEPRINT` vẫn là các giá trị phân biệt trong tuple cohort.
