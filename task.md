# Comprehensive Implementation Plan: Local Data Stack & AI Agent Integration (`super_dplayergod`)

## 1. Project Overview & Architecture

Dự án **`super_dplayergod`** thiết lập một môi trường phân tích dữ liệu local chuẩn Enterprise tích hợp trí tuệ nhân tạo (AI Agent). Hệ thống xử lý bài toán **Resource Management & Employee Allocation Analysis** với mô hình dữ liệu **Daily FTE (Full-time Equivalent)**.

### 1.1 Technology Stack

- **Database Layer:** PostgreSQL 15 (Quản lý lưu trữ Data Warehouse / Fact tables).
- **BI & Visualization:** Apache Superset (Local Container, kết nối trực tiếp PostgreSQL).
- **DB Client:** DBeaver (Kết nối quản trị, truy vấn SQL thủ công).
- **Data Processing / Pipeline:** Python 3.10+, Pandas, SQLAlchemy (Sinh và chuẩn hóa Mock Data).
- **AI & Agent Skills:** Claude Code CLI (headless, `claude -p`) chạy trong service `claude_gateway`, tự gọi MCP tools để truy vấn SQL và tạo dataset/chart/dashboard trong Superset.

---

## 2. Directory Structure

```text
super_dplayergod/
├── docker-compose.yml          # Cấu hình khởi tạo PostgreSQL & Apache Superset
├── init-db/
│   └── 01_init_data.sql       # SQL DDL khởi tạo schema và index
├── generate_data.py            # Script Python sinh dữ liệu giả theo logic Daily FTE
├── requirements.txt            # Python dependencies (pandas, sqlalchemy, psycopg2-binary)
├── plan.md                     # File kế hoạch tổng thể triển khai task
└── agent_skills/
    └── mock_data_docs.md      # Tài liệu lưu trữ skill & cấu trúc mock data cho AI Agent
```

---

## 3. Implementation Roadmap

### Phase 1: Environment & Infrastructure Setup
- [x] Thiết lập cấu trúc thư mục dự án `super_dplayergod/`.
- [x] Xây dựng file `docker-compose.yml` gồm 2 dịch vụ: PostgreSQL (port 5432) và Superset (port 8088).
- [x] Tạo file `init-db/01_init_data.sql` để auto-run DDL khi PostgreSQL container khởi chạy lần đầu.

### Phase 2: Data Modeling & Pipeline
- [x] Thống nhất mô hình dữ liệu: Daily Allocation Rate (phân bổ số HC tháng cho tổng số ngày đi làm `working_is_business_day = 1`).
- [x] Viết script `generate_data.py` tự động tính số ngày đi làm trong tháng và chia nhỏ chỉ số `project_allocated_hc`.
- [x] Nạp dữ liệu vào bảng `fact_employee_allocation` trong PostgreSQL.

### Phase 3: Client Connection & BI Setup
- [x] DBeaver: Tạo kết nối PostgreSQL thành công (host: `localhost`, port: `5432`, db: `super_db`).
- [x] Apache Superset: Đăng nhập giao diện admin tại `http://localhost:8088`.
- [x] Tạo Database Connection tới container Postgres: `postgresql://super_user:super_pass@postgres:5432/super_db`.
- [x] Tạo Dataset từ bảng `fact_employee_allocation`.
- [x] Dựng các Chart/Dashboard cơ bản (ví dụ: Total Allocated HC by Project/Department).

### Phase 4: AI Agent Skills & Automation
- [] Lưu trữ tài liệu `mock_data_docs` vào AI Agent Skills để Agent có thể tra cứu schema và logic tính toán.
- [ ] Mở rộng các Agent Skills cho việc tự động tạo query SQL báo cáo và kiểm tra chất lượng dữ liệu (Data Quality Check).

### Phase 5: Minimal Rollout (Claude Code CLI headless + MCP + Superset)
- [x] Mở rộng MCP server (`mcp_server.py`) với tool đọc (`list_datasets`, `describe_table`, `run_sql_readonly`) và tool ghi gọi Superset REST API (`create_dataset`, `create_chart`, `create_dashboard`).
- [x] Tạo service `claude_gateway` chạy Claude Code CLI headless (`claude -p`, tự gọi MCP tools), thay thế hoàn toàn Agent Gateway rule-based cũ.
- [x] Mở cổng Gateway nội bộ Docker network tại `claude_gateway:8090` (không publish ra host), qua Docker Compose.
- [x] Loại bỏ `codex.mcp.json` và mọi phụ thuộc Codex CLI phía host; MCP config giờ tự render bên trong container `claude_gateway`.
- [x] Cập nhật tài liệu chạy nhanh end-to-end trong `ROLLOUT.md` cho luồng Claude Code Gateway.

---

## 4. Key Logic & Business Rules

### 4.1 Allocation Calculation Logic (Daily FTE)

$$
\text{project\_allocated\_hc (ngày)} =
\begin{cases}
\frac{\text{Monthly Allocated HC}}{\text{Tổng số ngày làm việc trong tháng}}, & \text{nếu } \text{working\_is\_business\_day} = 1 \\
0, & \text{nếu } \text{working\_is\_business\_day} = 0
\end{cases}
$$

### 4.2 Rule cộng dồn chỉ số (Aggregation)

Do chỉ số đã được dàn đều theo ngày làm việc, người dùng có thể sử dụng trực tiếp hàm `SUM(project_allocated_hc)` ở mọi cấp độ thời gian (Ngày, Tuần, Tháng, Quý, Năm) mà không lo bị trùng lặp dữ liệu (double-counting).
