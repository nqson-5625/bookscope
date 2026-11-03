# Gold KPI Specification — v1_2026_11_04

Ký hiệu `O(m,f)` là tổng checkouts từ overview đã DQ duyệt ở tháng `m`,
định dạng `f`, scope `ALL_ELIGIBLE_AGG`. `B(k,m)` là tổng checkouts của các
dòng detail đã duyệt mang `book_record_key = k` trong tháng `m`, scope
`SELECTED_DETAIL`; mỗi `k` chỉ thuộc một format. Mỗi KPI luôn gắn
`data_version`, scope và `month_start`.

| KPI | Công thức/nguồn | Grain đầu ra | Điều kiện và giá trị đặc biệt |
| --- | --- | --- | --- |
| `overview_checkouts` | `SUM(silver.dq_approved_overview_groups.checkouts)` | `m × f × ALL_ELIGIBLE_AGG` | Số nguyên không âm; không cộng detail. |
| `overview_source_rows` | `SUM(source_row_count)` của nhóm overview | `m × f × ALL_ELIGIBLE_AGG` | Số dòng SODA3 dưới nhóm, khác `COUNT(*)` nhóm Bronze. |
| `overview_group_count` | `COUNT(*)` nhóm Silver overview | `m × f × ALL_ELIGIBLE_AGG` | Dùng để đối soát grain nguồn. |
| `format_share` | `O(m,f) / SUM_f O(m,f)` | `m × f × ALL_ELIGIBLE_AGG` | Mẫu số 0/thiếu → NULL + `ZERO_DENOMINATOR`/`MISSING_MONTH`; không dùng detail. |
| `book_checkouts` | `B(k,m) = SUM(detail.checkouts)` theo khóa tuple | `k × m × SELECTED_DETAIL` | Giữ từng dòng nguồn khi cộng; không gộp theo title/ISBN. Không có dòng → không quan sát, khác 0. |
| `book_source_rows` | `COUNT(*)` dòng detail đóng góp | `k × m × SELECTED_DETAIL` | Dùng cho lineage và reconciliation. |
| `active_book_records` | `COUNT(DISTINCT k)` có dòng detail tháng `m` | `m × f × SELECTED_DETAIL` | Bao gồm dòng checkouts 0 nếu xuất hiện; là số tuple cohort hoạt động, không phải số tác phẩm thư mục. |
| `cohort_coverage` | `SUM_k B(k,m) / O(m,f)` cùng tháng/format | `m × f`, chỉ số đối chiếu scope | Mẫu số 0 → NULL; ngoài `[0,1]` → BLOCK. Không gọi đây là tỷ trọng đầu sách toàn thư viện. |
| `mom_change` | `(C(m)-C(m−1))/C(m−1)` với cùng `k` hoặc cùng `f`/scope | Grain chuỗi tương ứng | Chỉ tính nếu hai tháng lịch liền nhau quan sát và mẫu số > 0; thiếu → NULL/`INSUFFICIENT_HISTORY`; mẫu số 0 và tháng hiện tại > 0 → `NEW_ACTIVITY`, tỷ lệ NULL. |
| `yoy_change` | `(C(m)-C(m−12))/C(m−12)` | Grain chuỗi tương ứng | Cần đúng tháng năm trước, cùng scope/key; 12 tháng đầu NULL; mẫu số 0 xử lý như MoM. |
| `rolling_3m_sum`, `rolling_3m_avg` | Tổng và trung bình `C(m−2), C(m−1), C(m)` | Grain chuỗi tương ứng | Chỉ tính đủ ba tháng lịch có quan sát và PASS; không lấp khoảng trống bằng 0. Dùng `numeric` cho tổng trung gian. |
| `book_growth_3m` | `(S_recent−S_previous)/S_previous`, mỗi `S` là tổng ba tháng liền kề | `k × evaluation_month × SELECTED_DETAIL` | Cần đủ sáu tháng quan sát; `S_previous=0<S_recent` → `NEW_ACTIVITY` và ratio NULL; cả hai 0 → `NO_ACTIVITY`; thiếu → NULL. |

`C` trong công thức thời gian là `O` hoặc `B` của **cùng grain**. Không
so MoM của một scope với số liệu scope khác. `0` là giá trị quan sát hợp lệ;
NULL hoặc không có dòng là chưa đủ bằng chứng. Phép chia dùng `numeric`,
không chia nguyên; tỷ lệ có thể hiển thị phần trăm nhưng lưu dạng fraction.
Không tự suy diễn tháng nguồn vắng mặt là giảm nhu cầu.

KPI mô tả dùng dữ liệu gốc đã duyệt. Cờ sự cố nguồn và độ nhạy là lớp
`incident_awareness` sau Gold; Gold có thể giữ trường `reliability_status`
ban đầu nhưng không tự gán nguyên nhân. Vì cohort được chọn theo cả năm 2023,
`book_growth_3m` dùng để đánh giá/backtest chỉ từ `2023-12` trở đi, không dùng
kỳ trước để nhận định dự báo không rò rỉ thông tin tương lai.

Kiểm thử tham chiếu bắt buộc: các chuỗi `[10,20,30]`, `[0,5]`, `[NULL,5]`,
tháng cách quãng, 12 tháng trước bằng 0, và nhiều dòng Silver cùng tuple trong
một tháng. Giá trị tính Gold phải đối chiếu bằng SQL/fixture độc lập với model.
