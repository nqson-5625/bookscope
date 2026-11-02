# project_foundation — thiết kế tuần 1

## Trạng thái và nguồn

Tài liệu gốc: [BookScope.pdf](BookScope.pdf), mục 2–7, 11 và phụ lục A. Nguồn chính là [City of Seattle, Checkouts by Title](https://catalog.data.gov/dataset/checkouts-by-title), ID `tmmm-ytt6`, dữ liệu đếm lượt mượn theo tháng của tài liệu vật lý và điện tử. Dữ liệu này không theo dõi người mượn. Theo [Socrata SODA3](https://dev.socrata.com/docs/queries/), truy vấn dùng POST `/api/v3/views/tmmm-ytt6/query.json` và app token.

**G0: đạt để bắt đầu `source_ingestion` vào 02/11/2026.** Truy vấn SODA3 bằng app token đã lấy mẫu thật từ 2023-01, 2024-06, 2025-01. File profile đầy đủ tại `data/profiles/` được bỏ qua bởi Git; trích mẫu 15 dòng thật và manifest tại `data/fixtures/` được giữ trong repo. Mapping được kiểm chứng trên ba tháng khảo sát, còn source ingestion phải kiểm tra mã mới trên mọi tháng 2023–2025 trước khi gọi tổng 36 tháng là đầy đủ.

## Câu hỏi và phạm vi

MVP xử lý kỳ `2023-01` đến `2025-12` (36 tháng lịch), không suy rộng sang 2005–nay. Hai scope được tách:

| scope_id | Đơn vị phân tích | Quy tắc công bố |
| --- | --- | --- |
| `ALL_ELIGIBLE_AGG` | Tháng × nhóm định dạng, tổng từ mọi dòng đủ điều kiện theo `book_material_type_map` | Chỉ gọi là tổng toàn bộ sách đủ điều kiện khi query nguồn bao trọn kỳ và đối soát thành công. |
| `SELECTED_DETAIL` | Bản ghi danh mục theo tháng trong cohort cố định | Xếp hạng chỉ trong cohort, luôn hiển thị số bản ghi và coverage; không gọi là Top toàn thư viện. |

Mapping `MaterialType` phiên bản `v1_2026_11_02`: `BOOK`, `REGPRINT`, `LARGEPRINT` → `PRINT_BOOK`; `EBOOK`, `Ebook` → `EBOOK`. Dữ liệu thật tháng 2024-06 dùng `Ebook` thay cho `EBOOK`. `REGPRINT` và `LARGEPRINT` có bản ghi sách in trong mẫu live. `AUDIOBOOK`/`Audiobook` và các mã khác tạm loại vì phạm vi MVP là sách đọc; `ATLAS` cũng không thuộc phạm vi này. Đây là **định nghĩa phạm vi có chủ đích**, không phải khẳng định bao phủ mọi tài liệu có thể đọc. Mã nguồn mới hoặc mã thay đổi trong các tháng chưa khảo sát phải được báo cáo và duyệt trước khi tổng hợp. `UsageClass` và `CheckoutType` được giữ nguyên để kiểm tra format và grain.

Tiêu chí `SELECTED_DETAIL` để khống chế máy 8GB: chọn tối đa 1.000 tuple `(title, creator, materialtype, checkouttype)` có tổng lượt mượn năm 2023 cao nhất trong dữ liệu hợp lệ; hòa điểm thì sắp xếp tuple theo thứ tự từ điển. Sau đó lấy toàn bộ các tháng 2023–2025 của tuple đã chọn. Đây là cohort theo bộ metadata nguồn, không mặc định một tuple là một tác phẩm hay một ấn bản duy nhất. Profile live đã xác nhận có tuple nhiều dòng và có `creator` thiếu trong mẫu; độ bao phủ theo tháng và biến thể title cần đo khi tải lịch sử ở G1. Vì cohort được chọn bằng dữ liệu 2023, backtest chỉ đánh giá tại thời điểm từ `2023-12` trở đi; không dùng cùng cohort để nhận định về quyết định ở các tháng trước 2023-12.

## Quy trình khảo sát

CLI trong `src/modules/project_foundation` lấy tối đa 100 dòng chung và 30 dòng cho **mỗi mã sách** tại từng tháng khảo sát để kiểm tra schema, giá trị thiếu và giá trị vô hiệu. Truy vấn phân phối riêng đếm **toàn bộ dòng theo `materialtype × usageclass` trong từng tháng khảo sát**; truy vấn grain ghi các tuple có nhiều dòng nguồn. Mẫu được sắp xếp để tái lập, **không phải mẫu ngẫu nhiên**: tỷ lệ missing và phân phối từ mẫu không phải ước lượng cho tổng thể. Các truy vấn mẫu có thể giao nhau nên `exact_duplicate_rows` của mẫu cũng không phải số dòng nguồn trùng trong tổng thể. `sample_sha256` hash nội dung chuẩn hóa, còn `sample_file_sha256` hash byte của tệp JSONL. Nếu cần đo missingness cho tổng thể, thêm truy vấn tổng hợp nguồn cho từng trường hoặc lấy mẫu ngẫu nhiên có thiết kế được công bố.

Gate G0 chỉ được đánh dấu đạt khi: (1) có file sample live và profile cùng query/checksum; (2) xác nhận schema thực, `MaterialType` và điều kiện sách; (3) thống kê số dòng/checkout theo định dạng trên ít nhất ba tháng khảo sát và ghi nhận missingness; (4) định nghĩa cohort và coverage khả thi với ngân sách tài nguyên; (5) xác minh grain nguồn và chính sách identity/dedup ở mức đủ để viết Silver. Không lấy `ISBN` làm khóa duy nhất; `Subjects` là metadata nhiều giá trị và không được join trực tiếp trước khi cộng checkouts.

## Kết quả khảo sát live và quyết định G0

Truy vấn aggregate nguồn trả về số dòng và tổng lượt mượn theo **phạm vi mapping v1**, không phải chỉ số từ 660 dòng mẫu. Mỗi ô là `số dòng / lượt mượn`:

| Tháng | `PRINT_BOOK` (`BOOK`, `REGPRINT`, `LARGEPRINT`) | `EBOOK` (`EBOOK`, `Ebook`) |
| --- | ---: | ---: |
| 2023-01 | 91.820 / 272.572 | 70.982 / 252.564 |
| 2024-06 | 55.729 / 91.630 | 54.715 / 145.090 |
| 2025-01 | 89.937 / 241.803 | 79.686 / 272.579 |

Mẫu phân tầng gồm 100 dòng chung và 30 dòng cho mỗi mã sách có mặt ở từng tháng, tổng 660 dòng. Mẫu có 28 dòng thiếu `creator`, 20 thiếu `isbn`, 3 thiếu `publicationyear`, 2 thiếu `subjects`; không có giá trị year/month/checkouts/title vô hiệu trong mẫu. Đây chỉ là số đếm của **mẫu có thứ tự**, không suy rộng thành tỷ lệ thiếu toàn bộ nguồn. Truy vấn grain ở cả ba tháng tìm thấy nhiều dòng cùng tuple; ở 2025-01, một tuple `EBOOK` có tới 10 dòng. Chính sách Silver: giữ mọi dòng nguồn và không dedup bằng tuple, title hoặc ISBN; phép gộp theo cohort chỉ thực hiện sau khi đối soát tổng.

Truy vấn nhóm toàn năm 2023 lấy được đúng 1.000 tuple trong khoảng 10 giây khi khảo sát. Tổng 772.632 lượt mượn của cohort chiếm **12,918%** trong 5.981.040 lượt mượn của 1.929.681 dòng thuộc phạm vi mapping v1 năm 2023; tuple cuối có 348 lượt. Cohort vì vậy khả thi về cỡ dữ liệu nhưng có độ phủ thấp và chỉ hỗ trợ nhận định trong chính cohort. Độ phủ cho 2024–2025 sẽ do `source_ingestion` đo sau khi tải đầy đủ lịch sử. G0 đạt vì đã xác minh nguồn, schema, mã sách, grain và phép chọn cohort; G1 vẫn phải chứng minh phân trang, đầy đủ 36 tháng, replay và đối soát.

Bằng chứng máy đọc được: `data/profiles/foundation_profile.json` chứa query, phân phối, probe trùng tuple, số liệu cohort và hash; `data/profiles/foundation_sample.jsonl` và `foundation_cohort_2023.jsonl` là dữ liệu live đầy đủ của bước khảo sát. `data/fixtures/foundation_live_excerpt.jsonl` cùng manifest là trích mẫu nhỏ được lưu trong repo. Giữ cả ba tệp `data/profiles/` với checksum nếu nộp hồ sơ nghiệm thu riêng.

## Kiến trúc triển khai

```mermaid
flowchart LR
  A[Seattle Open Data SODA3] --> B[Python ingestion]
  B --> C[Raw JSONL.gz + manifest SHA-256]
  C --> D[PostgreSQL bronze JSONB]
  D --> E[dbt silver + quarantine]
  E --> F[dbt gold marts]
  F --> G[Python trend / incident / scoring / backtest]
  G --> H[Quality gate + versioned publish]
  H --> I[Streamlit 4 trang, read-only]
```

Python runner điều phối tuần tự, có run log và retry hữu hạn. Raw khác Bronze: Raw là snapshot bất biến trên filesystem; Bronze là dòng nguồn trong PostgreSQL. Chỉ snapshot COMPLETE với hash và row_count hợp lệ được load. dbt xử lý SQL sau Bronze. Gold và DS mang `scope_id`, `data_version` và phiên bản quy tắc; dashboard chỉ đọc publication đã qua gate. Khi nguồn sửa tháng cũ, tạo snapshot mới và **thay thế** tháng/scope đã tải đủ, không cộng dồn. Sự cố SPL 25/05/2024 được ghi cờ bối cảnh; không suy diễn mọi biến động 05–07/2024 là thay đổi nhu cầu.

## Việc cần kiểm chứng trong `source_ingestion` (G1)

- Mapping v1 có bao quát đúng phạm vi sách đã định nghĩa ở **mọi tháng** 2023–2025 không; mã lạ phải được ghi log và chặn công bố cho đến khi được duyệt.
- Khóa nguồn có đủ phân biệt catalog record/edition không; nếu không, giữ `source_row_number` và một key kỹ thuật, không gộp tùy tiện.
- Truy vấn overview có thể đối soát tổng nguồn theo tháng × format và tải đủ 36 tháng trong giới hạn API không.
- Cohort 1.000 tuple đã chọn có thể truy hồi lịch sử 2023–2025 ổn định không; đo coverage theo tháng và thu nhỏ cohort nếu giới hạn API bắt buộc, nhưng ghi rõ thay đổi.

## Tài liệu tham khảo

- [City of Seattle / Data.gov: Checkouts by Title](https://catalog.data.gov/dataset/checkouts-by-title)
- [Socrata: Queries using SODA3](https://dev.socrata.com/docs/queries/)
- [University of Washington: mô tả dữ liệu checkout](https://essays.whats-seattle-reading.com/data.html)
- [Gupta, Christensen & Walsh: nghiên cứu về độ tin cậy dữ liệu](https://doi.org/10.5334/johd.332)
