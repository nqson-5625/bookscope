# Rà soát MaterialType cho source_ingestion

Ngày rà soát: 02/11/2026. Dữ liệu: truy vấn nhóm toàn bộ 36 tháng 2023-01 đến 2025-12 từ Seattle Open Data `tmmm-ytt6`; báo cáo máy đọc được ở `data/raw/seattle_checkouts/ingestion_audit.json` (không đưa vào Git). Bản chốt quyết định nằm trong `configs/source_ingestion.json`, `material_review_version=v1_2026_11_02_exact_codes`.

## Quyết định phạm vi

MVP **chỉ gồm đúng năm mã** trong `book_material_type_map`: `BOOK`, `REGPRINT`, `LARGEPRINT`, `EBOOK`, `Ebook`. Trong dữ liệu 36 tháng, năm mã này chiếm 5.745.441 dòng nguồn và 17.042.420 lượt mượn. Hai cách viết ebook cùng vào nhóm `EBOOK`; khi truy hồi cohort dùng alias có phiên bản `Ebook → EBOOK`, còn Raw giữ mã gốc.

45 mã khác quan sát được nằm trong `reviewed_excluded_material_types`. Chúng chiếm 2.304.272 dòng và 9.988.539 lượt mượn. **Loại trừ là quyết định phạm vi theo mã chính xác, không phải kết luận rằng mọi dòng của các mã này đều không phải sách.** Một số mã có nhiều dạng tài liệu hoặc không đủ thông tin để xác nhận book-only. Mã kết hợp như `BOOK, ER` và `ER, REGPRINT` cũng bị loại khỏi phạm vi phiên bản này; không tách một phần checkouts của cùng dòng nguồn để tránh cộng sai.

| Nhóm cần chú ý | Bằng chứng từ truy vấn 36 tháng | Quyết định |
| --- | ---: | --- |
| `AUDIOBOOK`, `Audiobook` | 7.914.811 lượt mượn | Ngoài phạm vi sách đọc dạng in/ebook của MVP. |
| `VIDEODISC` | 1.434.789 lượt mượn | Ngoài phạm vi. |
| `SOUNDDISC` | 452.883 lượt mượn | Ngoài phạm vi. |
| `MIXED` | 129.779 lượt mượn | Mã tổng hợp; mẫu nguồn có thiết bị như hotspot/headphone/laptop. Loại khỏi phép tính book-only. |
| `CR`, `ER`, `UNSPECIFIED` | 5.222 lượt mượn cộng lại | Mẫu nguồn có cả tiêu đề giống sách; không đồng nhất với năm mã đã xác minh, nên loại khỏi phạm vi MVP và ghi rõ khả năng thiếu phủ. |
| `BOOK, ER`, `ER, REGPRINT`, `REGPRINT, VIDEOREC` | 63 lượt mượn cộng lại | Mã kết hợp; không tự quy toàn bộ checkouts cho sách. |

Các mã còn lại gồm bản đồ/atlas, hình ảnh, nhạc/âm thanh/video và mã kết hợp. Danh sách đầy đủ theo đúng chữ hoa/thường nằm trong [cấu hình ingestion](../configs/source_ingestion.json). Mẫu ba dòng cho từng mã mơ hồ được truy vấn để xem title, subjects, usageclass và checkouttype; đó là ví dụ có thứ tự, không phải ước lượng tỷ lệ sách trong từng mã.

Nếu muốn mở rộng định nghĩa sách sang các mã như `CR`, `ER`, `UNSPECIFIED` hoặc mã kết hợp, cần lập quy tắc mới có bằng chứng ở cấp dòng, tăng phiên bản mapping, chọn lại cohort và tải/đối soát lại tất cả các tháng. Mã nguồn mới xuất hiện trong lần chạy sau vẫn bị audit gắn `UNREVIEWED` và chặn publication.
