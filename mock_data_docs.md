# Mock Data Documentation

## 1. Mục tiêu

Tài liệu này mô tả cấu trúc dữ liệu mock cho bảng `fact_employee_allocation`, phục vụ cho việc sinh dữ liệu, kiểm tra schema, và hỗ trợ AI Agent hiểu đúng các thuộc tính cần dùng trong báo cáo và phân tích nhân sự.

---

## 2. Dữ liệu mẫu theo từng ngày làm việc

Một dòng dữ liệu đại diện cho một nhân viên được phân bổ vào một dự án trong một ngày làm việc cụ thể. Cấu trúc JSON mẫu như sau:

```python
{
    "working_date": "2025-01-01",
    "project_allocated_hc": 0.0455,
    "current_row_indicator": "Y",
    "employee_full_name": "Nguyen Van A",
    "employee_id": "EMP-001",
    "employee_email": "nguyenvana@company.com",
    "employee_role": "Senior Analyst",
    "employee_level": "L3",
    "project_name": "Project Alpha",
    "organization_name": "Digital Transformation",
    "organization_code": "DT-01",
    "working_month_year": "01/2025",
    "working_quarter": "Q1",
    "working_year": "2025",
    "working_is_business_day": 1,
    "d_working_date_key": 20250101
}
```

---

## 3. Danh sách thuộc tính (Attributes)

| Thuộc tính | Kiểu dữ liệu | Mô tả |
| --- | --- | --- |
| `working_date` | string | Ngày làm việc theo định dạng `YYYY-MM-DD`. |
| `project_allocated_hc` | float | Số lượng FTE hoặc phân bổ theo ngày sau khi chia đều theo số ngày làm việc trong tháng. |
| `current_row_indicator` | string | Dấu hiệu bản ghi đang active. Giá trị mặc định là `Y`. |
| `employee_full_name` | string | Họ tên nhân viên. |
| `employee_id` | string | Mã nhân viên duy nhất. |
| `employee_email` | string | Email nhân viên. |
| `employee_role` | string | Vai trò công việc / chức danh. |
| `employee_level` | string | Cấp bậc nhân viên (ví dụ: L1, L2, L3). |
| `project_name` | string | Tên dự án được phân bổ nhân sự. |
| `organization_name` | string | Tên đơn vị / phòng ban. |
| `organization_code` | string | Mã đơn vị / tổ chức. |
| `working_month_year` | string | Tháng làm việc theo định dạng `MM/YYYY`. |
| `working_quarter` | string | Quý làm việc, ví dụ `Q1`, `Q2`, `Q3`, `Q4`. |
| `working_year` | string | Năm làm việc dạng text, ví dụ `2025`. |
| `working_is_business_day` | int | Chỉ báo ngày làm việc thực tế: `1` nếu là ngày làm việc, `0` nếu không phải. |
| `d_working_date_key` | int | Khóa ngày làm việc dạng `YYYYMMDD`, dùng cho lọc và nhóm theo ngày. |

---

## 4. Business Notes

- `project_allocated_hc` được tính theo logic Daily FTE, phân bổ theo số ngày làm việc thực tế của tháng.
- `working_is_business_day` giúp lọc ngày làm việc và loại bỏ các ngày nghỉ / cuối tuần.
- `d_working_date_key` hỗ trợ join nhanh với các dimension date hoặc xử lý phân tích theo ngày/tháng/quý/năm.
- `current_row_indicator = "Y"` biểu thị bản ghi hiện tại, phù hợp cho kiểm tra dữ liệu và tiếp cận AI Agent.

---

## 5. Cách sinh dữ liệu gợi ý

```python
row = {
    "working_date": dt.strftime("%Y-%m-%d"),
    "project_allocated_hc": round(daily_hc, 4),
    "current_row_indicator": "Y",
    "employee_full_name": emp["employee_full_name"],
    "employee_id": emp["employee_id"],
    "employee_email": emp["employee_email"],
    "employee_role": emp["employee_role"],
    "employee_level": emp["employee_level"],
    "project_name": emp["project_name"],
    "organization_name": emp["organization_name"],
    "organization_code": emp["organization_code"],
    "working_month_year": dt.strftime("%m/%Y"),
    "working_quarter": f"Q{dt.quarter}",
    "working_year": str(dt.year),
    "working_is_business_day": is_biz_day,
    "d_working_date_key": int(dt.strftime("%Y%m%d"))
}
```

---

## 6. Ghi chú cho AI Agent

AI Agent nên coi `fact_employee_allocation` là bảng fact ở mức ngày, với mỗi bản ghi tương ứng một nhân viên được phân bổ vào một dự án trong một ngày làm việc. Khi tạo query báo cáo, nên nhóm theo các thuộc tính `working_date`, `working_month_year`, `working_quarter`, `working_year`, `project_name`, và `organization_name` để dựng dashboard và phân tích xu hướng.
