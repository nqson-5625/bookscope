# Nghiệm thu Gold G3 — 04/11/2026

Báo cáo này ghi trạng thái tại ngày 04/11/2026. Sau khi bổ sung bảo vệ
Dimension và an toàn build đồng thời ngày 06/11/2026, version hiện hành và
kết quả kiểm chứng nằm ở [Gold integrity acceptance](gold_integrity_2026-11-06.md).

**Quyết định: FULL PASS cho module `gold_analytics_marts` trên bộ dữ liệu
hiện hành.** Quyết định áp dụng cho `data_version`
`08c8578f45653cc70a31ac5347ae1b907c2b218b528d2d66744f092bed266cb8`,
pin 72 snapshot với `selection_sha256`
`31c8a897c222466ab7cdfedcfeb923492bd696e15fb2b443d137d49ebc6f0d13`.
DQ run nguồn: `c8fd09ee-e121-48aa-8446-be91b86f7351`. Output SHA-256:
`1c588aa7c76d2a09686d7c3d7a2c6c07bb1ab0ce6dcab06408f6f790a3bd3e3a`.
Snapshot/logic mới phải qua gate và tạo hoặc tái sử dụng version theo
[publication policy](gold_contract.md).

| Giai đoạn / checklist | Bằng chứng nghiệm thu | Kết quả |
| --- | --- | --- |
| A. Môi trường và đầu vào (1–5) | Trước khi tạo file Gold, `ops.dq_gate` PASS 72/72, 0 BLOCK, `source_state_matches=true`; Overview chỉ có `ALL_ELIGIBLE_AGG`, Detail chỉ có `SELECTED_DETAIL`. 72 snapshot ID/manifest và DQ run đã được ghi trong `ops.gold_build_pins`; schema `gold` và quyền USAGE/CREATE đạt. Macro dbt thực tế đặt staging riêng; Silver/DQ gate vẫn PASS sau build. | PASS |
| B. Physical DB (6–10) | [Migration](../sql/gold/001_publications.sql) chạy trên PostgreSQL 16.15 và chạy lại đạt sau khi đã có các version PUBLISHED; 8 bảng Gold, bảng build/pin/publication, PK/FK/unique/index/trigger và ba current views. dbt dùng table trong schema staging; published rows có trigger chống sửa. SQL smoke và test rollback đạt. | PASS |
| C. Dimension (11–15) | `dim_month` 36 dòng, `dim_format` 2, `dim_book_record` 1.000 khóa duy nhất. Hash SHA-256 tuple v1 được đối chiếu; NULL khác chuỗi rỗng, BOOK khác REGPRINT, Ebook alias EBOOK. | PASS |
| D. Fact (16–20) | Mỗi version: `monthly_overview` 72 dòng, `book_monthly_stats` 31.402 dòng. Overview giữ riêng `source_row_count`/`source_group_count`; Detail dùng SUM và COUNT trên mọi dòng nguồn cùng tuple–tháng. PK/scope tests đạt. | PASS |
| E. KPI và Feature (21–30) | `format_share`, MoM, YoY, rolling 3m, `book_growth_3m`, active records, coverage và `metric_status` JSONB đã tạo. Feature 36.000 dòng; dbt unit fixture chứng minh tháng thiếu `observed=false`/NULL khác dòng 0. Python reference so toàn bộ KPI hiện hành. Gold không tự điều chỉnh sự cố SPL; backtest dùng cohort 2023 phải bắt đầu từ 2023-12 theo KPI Spec. | PASS |
| F. Lineage/reconciliation (31–35) | 144 Overview lineage và 34.886 Detail lineage. SQL dbt test đối chiếu từng dòng với Silver DQ-approved; Python kiểm lại Silver→lineage→fact theo snapshot/tháng/format/khóa. | PASS |
| G. Version/publication (36–43) | Runner pin DQ/source state, build staging, đòi đủ test IDs, tính output/code/version hash, khóa source revision và DQ run, so lại digest sau copy rồi đổi pointer trong transaction. Lỗi trước/sau copy rollback. Replay `REUSED`; version cũ còn truy vấn được. | PASS |
| H. Tests (44–52) | dbt build cuối: 8/8 models, 31/31 data tests, 1/1 unit test. Python/PostgreSQL: 13/13 tests trong full suite. Fixture DQ BLOCK, snapshot mới, pin thiếu, source change sau pin, version collision, published mutation và hai failpoint promotion bị chặn, dùng transaction rollback. | PASS |
| I. Dữ liệu thật/G3 (53–62) | 36 tháng và 72 snapshot, tổng Overview 17.042.420, Detail 1.495.317 checkouts; đối soát động theo scope/grain đạt. Version hiện tại truy vấn qua `gold.current_*`; replay giữ cùng version/digest/số dòng, pointer ổn định. Benchmark và tài liệu dưới đây. | PASS |

## Bằng chứng chạy và provenance

- DQ gate hiện hành: PASS, 72/72, 0 BLOCK, 180 WARN metadata,
  `source_state_matches=true`. WARN không loại dòng hay sửa checkouts.
- Build hiện hành: `build_id=4d2dedb0-8f80-49d1-9d58-ff93c2c3a535`,
  8 models, 31 data tests, 1 unit test, `outcome=PUBLISHED`.
  `ops.gold_builds.dbt_results` lưu invocation ID, danh tính và trạng thái
  từng model/test; `validation_results` lưu số dòng và kết quả reconciliation.
- Replay cuối: `build_id=3d043eec-24ad-43ab-9429-502749943efd`,
  `outcome=REUSED`, cùng `data_version` và output digest. Không tăng 34.886
  dòng Book lineage của version hiện tại và không đổi con trỏ.
- Bốn phiên bản PUBLISHED được giữ trong DB do mã triển khai/test thay đổi
  trong quá trình hoàn thiện; cả bốn có cùng output digest trên cùng snapshot.
  Hai build thất bại trong giai đoạn phát triển vẫn được ghi `FAILED`, không
  đổi pointer. Mỗi phiên bản còn 36.000 Features và 34.886 Book lineage.
- `ops.publication_snapshots` lưu đúng 72 pin cho version hiện tại.
  `ops.current_publication` trỏ tới version trên; các view `gold.current_*`
  chỉ hiển thị version này.

## Benchmark máy 8 GB RAM

Build đầy đủ cuối có lấy mẫu bộ nhớ mỗi 0,5 giây: 109,727 giây tổng, trong
đó dbt 20,575 giây; database 425.131.031 byte ngay sau build. Máy có
8.241.897.472 byte RAM vật lý; trong 218 mẫu, RAM khả dụng thấp nhất
307.671.040 byte và tải RAM toàn hệ thống cao nhất 96%. Pipeline hoàn tất
không phát sinh lỗi bộ nhớ hoặc thêm công nghệ xử lý. Phép đo RAM là **toàn
hệ thống**, bao gồm ứng dụng khác; không thể quy toàn bộ 96% cho Gold. Biên
RAM còn mỏng khi máy chạy nhiều ứng dụng. Docker daemon từ môi trường chạy
này từ chối truy cập nên không có số RAM riêng cho container PostgreSQL.
Database sau nghiệm thu là 421.026.839 byte; dung lượng biến động khi staging
được dọn.

## Kiểm chứng lại

Theo [hướng dẫn Gold](gold_analytics_marts.md), chạy `status`, `replay` và
`BOOKSCOPE_TEST_DB=1` với `tests.test_gold_analytics_marts`. Kiểm tra
`ops.gold_builds`, `ops.data_publications`, `ops.publication_snapshots` và
`gold.current_*`. Các baseline cố định ở trên áp dụng cho snapshot hiện tại;
nghiệm thu version mới phải đối soát động với Silver đã được DQ duyệt.
