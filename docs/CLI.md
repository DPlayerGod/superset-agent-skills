# CLI - Các lệnh Docker thường dùng

Stack gồm 3 container (xem `docker-compose.yml`):

| Service | Container | Cổng | Ghi chú |
|---|---|---|---|
| `postgres` | `super_postgres` | 5432 | DB `super_db`, user `super_user` / `super_pass` |
| `superset` | `super_superset` | 8088 | admin / admin |
| `claude_gateway` | `super_claude_gateway` | 8090 (nội bộ) | cần `CLAUDE_CODE_OAUTH_TOKEN` |

> **PowerShell (Windows):** PowerShell 5.1 không hỗ trợ toán tử `<` để đẩy file vào stdin.
> Mọi lệnh có `< file` bên dưới đều kèm biến thể `Get-Content ... | docker exec -i ...`.
> Chạy tất cả từ thư mục gốc của repo.

---

## 1. Vòng đời stack

```bash
# Bật toàn bộ stack (nền)
docker compose up -d

# Bật + build lại image khi code đã đổi
docker compose up -d --build

# Chỉ bật/build lại một service
docker compose up -d claude_gateway
docker compose up -d --build claude_gateway

# Trạng thái + log
docker compose ps
docker compose logs -f claude_gateway
docker compose logs -f --tail 100 superset

# Khởi động lại / dừng
docker compose restart claude_gateway
docker compose stop
docker compose down          # xoá container, GIỮ dữ liệu (volume)
docker compose down -v       # xoá container + volume (mất sạch DB)
```

---

## 2. Truy vấn Postgres

```bash
# Đếm số dòng
docker exec super_postgres psql -U super_user -d super_db -c "SELECT COUNT(*) FROM fact_employee_allocation;"

# Kiểm tra khoảng thời gian dữ liệu
docker exec super_postgres psql -U super_user -d super_db -c "SELECT COUNT(*), MIN(working_date), MAX(working_date) FROM fact_employee_allocation;"

# Liệt kê bảng
docker exec super_postgres psql -U super_user -d super_db -c "\dt"

# Mở psql tương tác
docker exec -it super_postgres psql -U super_user -d super_db
```

---

## 3. Nạp lại bảng `fact_employee_allocation`

Chỉ động vào một bảng, không đụng tới Superset metadata.

**Bash / Git Bash:**

```bash
# 1. Xoá bảng cũ
docker exec -i super_postgres psql -U super_user -d super_db -c "DROP TABLE IF EXISTS fact_employee_allocation;"

# 2. Tạo lại bảng + index từ script gốc
docker exec -i super_postgres psql -U super_user -d super_db < init-db/01_init_data.sql

# 3. Nạp dữ liệu từ CSV (~46k dòng)
docker exec -i super_postgres psql -U super_user -d super_db -c "\copy fact_employee_allocation FROM STDIN WITH (FORMAT csv, HEADER true)" < fact_employee_allocation.csv

# 4. Kiểm tra lại
docker exec super_postgres psql -U super_user -d super_db -c "SELECT COUNT(*), MIN(working_date), MAX(working_date) FROM fact_employee_allocation;"
```

**PowerShell:**

```powershell
docker exec -i super_postgres psql -U super_user -d super_db -c "DROP TABLE IF EXISTS fact_employee_allocation;"

Get-Content init-db/01_init_data.sql | docker exec -i super_postgres psql -U super_user -d super_db

Get-Content fact_employee_allocation.csv | docker exec -i super_postgres psql -U super_user -d super_db -c "\copy fact_employee_allocation FROM STDIN WITH (FORMAT csv, HEADER true)"

docker exec super_postgres psql -U super_user -d super_db -c "SELECT COUNT(*), MIN(working_date), MAX(working_date) FROM fact_employee_allocation;"
```

---

## 4. Reset sạch hoàn toàn

Dựng lại từ số 0 - Postgres sẽ tự chạy lại `init-db/` khi volume trống.
Lưu ý: mất luôn dashboard/chart đã tạo trong Superset.

```bash
docker compose down -v
docker compose up -d
```

---

## 5. Superset & Claude Gateway

```bash
# Shell trong container
docker exec -it super_superset bash
docker exec -it super_claude_gateway bash

# Kiểm tra plugin skills của gateway
docker compose exec -T claude_gateway claude plugin validate /app/claude_gateway/skills_plugin

# Đặt lại mật khẩu admin Superset
docker exec -it super_superset superset fab reset-password --username admin --password admin

# Health check gateway (từ trong network)
docker exec super_superset curl -s http://claude_gateway:8090/health
```
