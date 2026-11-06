# Gold dbt models

Tám model dựng dimension, fact, feature và lineage từ hai view
`silver.dq_approved_*` đã DQ duyệt. `ops.gold_build_pins` cố định đúng 72
snapshot cho mỗi build. Các dependency nội bộ dùng `ref()`. Schema tests ở
`schema.yml`; singular tests ở `tests/gold/`.

Trong local `dev`, macro đưa Gold vào schema staging riêng
`gold_stage_<build_id>` khi chạy qua Python runner. dbt chạy thủ công không
truyền biến sẽ dùng `gold_sandbox`; không ghi trực tiếp vào schema publication
`gold`. Runner đối soát, tạo digest và copy dữ liệu vào các bảng versioned của
`gold` trong một transaction. Silver vẫn ở schema `silver`.

Xem [Gold Data Contract](../../../docs/gold_contract.md) và
[hướng dẫn chạy](../../../docs/gold_analytics_marts.md).
