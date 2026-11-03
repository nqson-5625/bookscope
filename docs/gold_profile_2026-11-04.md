# Gold entry profiling — 04/11/2026

Nguồn chỉ đọc: `silver.dq_approved_overview_groups`,
`silver.dq_approved_detail_records`, cohort Raw `cohort_2023.jsonl` và
`ops.dq_gate`. Phạm vi cố định 2023-01–2025-12. Mọi số tổng dưới đây được tính
riêng theo scope; detail là cohort được chọn, không đại diện toàn thư viện.

## Nghiệm thu DQ trước profiling

`ops.dq_gate` trả `PASS`, 72/72 cặp tháng–scope khớp, 0 BLOCK và
`source_state_matches = true`. Run hiệu lực có 1.412 kết quả: 1.087 PASS,
145 CONTEXT, 180 WARN; 36 dbt tests, `DBT_BUILD` và
`SOURCE_CHANGE_TRACKING` đều PASS. Kiểm thử DQ trong giao dịch đạt 13/13;
không tạo file trong bước nghiệm thu. Sau kiểm thử gate vẫn PASS và Git sạch.

## Phân phối Silver được duyệt

| Phạm vi | Định dạng | Dòng/nhóm Silver | Checkouts | Dòng nguồn dưới nhóm overview | Tháng có dữ liệu |
| --- | --- | ---: | ---: | ---: | ---: |
| `ALL_ELIGIBLE_AGG` | EBOOK | 36 | 9.004.687 | 2.705.812 | 36 |
| `ALL_ELIGIBLE_AGG` | PRINT_BOOK | 108 | 8.037.733 | 3.039.629 | 36 |
| `SELECTED_DETAIL` | EBOOK | 21.023 | 1.028.659 | — | 36 |
| `SELECTED_DETAIL` | PRINT_BOOK | 13.863 | 466.658 | — | 36 |

Overview có 144 nhóm, 17.042.420 checkouts và 5.745.441 dòng nguồn dưới
nhóm; mỗi tháng có ba nhóm in và một nhóm ebook. Detail có 34.886 dòng,
1.495.317 checkouts, không có dòng checkouts bằng 0 trong tập hiện tại. Cả
hai scope đều có 36 tháng và 72 cặp `month_start × format_key`.

| Chỉ số theo tháng | EBOOK: min / median / max | PRINT_BOOK: min / median / max |
| --- | --- | --- |
| Overview checkouts | 145.090 / 255.017 / 286.052 | 84.113 / 231.574 / 272.572 |
| Detail checkouts | 16.180 / 24.404 / 45.033 | 2.325 / 7.059,5 / 25.864 |
| Detail rows | 462 / — / 639 | 272 / — / 419 |
| Detail / overview checkouts, % | 6,4135 / 11,0677 / 17,9713 | 2,2175 / 3,7521 / 10,0908 |

Detail gồm `BOOK` 13.792 dòng, `REGPRINT` 71 dòng, `EBOOK` 19.329 dòng và
`Ebook` 1.694 dòng; không có `LARGEPRINT` trong cohort hiện tại. Ebook dùng
`usage_class=Digital`/`checkout_type=OverDrive`, nhóm in dùng
`Physical`/`Horizon` trong dữ liệu hiện hành. Không dùng tương quan này làm
quy tắc identity thay cho `materialtype_raw` và `checkouttype_raw`.

Thiếu metadata detail: Creator 254, ISBN 408, Subjects 254, Publisher 218,
Publication Year 218 dòng. Đây là WARN, không loại dòng.

## Grain và định danh cohort

Tuple `(title_raw, creator_raw, materialtype_cohort, checkouttype_raw)` với
`materialtype_cohort = EBOOK` khi nguồn là `Ebook`, còn lại giữ mã nguồn, tạo
đúng **1.000 khóa** trên Silver. Tập này bằng tập 1.000 tuple của file cohort
Raw: 0 thiếu, 0 ngoài cohort. Cách tạo SHA-256 từ
`jsonb_build_array('cohort_tuple_v1', title_raw, creator_raw,
materialtype_cohort, checkouttype_raw)::text` cho 1.000 hash dài 64 ký tự,
không có collision quan sát được.

34.886 dòng detail gộp thành 31.402 nhóm `cohort tuple × month`.
2.781 nhóm có nhiều dòng nguồn (6.265 dòng, tối đa 8 dòng/nhóm): Gold cần
`SUM(checkouts)`, không `DISTINCT` hoặc lấy một dòng đại diện. Toàn bộ
34.886 `source_id` là khác nhau và không có ID lặp qua tháng, nên `:id` chỉ
phục vụ truy vết dòng trong snapshot. 521 cặp `(title_raw, creator_raw)` có
hơn một biến thể material/checkout type. Nếu chuẩn hóa title và creator để
làm khóa, sáu nhóm hiện tại sẽ gộp hai tuple nguồn khác nhau. Vì vậy khóa Gold
giữ nguyên title/creator nguồn, giữ NULL creator khác chuỗi rỗng, và chỉ dùng
alias `Ebook → EBOOK` đã được duyệt cho cohort.

Trong 1.000 tuple, 511 có mặt đủ 36 tháng; 64 có mặt dưới 12 tháng; số tháng
ít nhất là 2. Dòng thiếu ở một tháng là **không quan sát được**, không tự gán
checkouts bằng 0. `book_record_key` theo tuple cohort là định danh cho đối
tượng phân tích này, chưa chứng minh một tác phẩm/ấn bản thư mục ổn định.

## Cách tái lập

Chỉ đọc hai view `dq_approved_*` khi `ops.dq_gate.status = 'PASS'`. Group theo
`month_start × format_key` riêng từng scope; so 1.000 tuple Silver với
`data/raw/seattle_checkouts/cohort_2023.jsonl` sau alias. Kiểm tra
`count(distinct (title_raw, creator_raw, materialtype_cohort,
checkouttype_raw))`, số nhóm theo tuple–tháng, số dòng nguồn và uniqueness
SHA-256 trước mỗi thay đổi rule identity.
