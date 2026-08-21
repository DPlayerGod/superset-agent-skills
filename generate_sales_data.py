import random
import datetime
import calendar
import os
import pandas as pd
from sqlalchemy import create_engine, text

# Set random seed for reproducibility
random.seed(42)

# --- 1. Master Data Definitions ---

ENTERPRISE_CUSTOMERS = [
    {"name": "Tập đoàn Vingroup", "industry": "Sản xuất & Bất động sản", "tier": "Enterprise", "region": "Miền Bắc", "city": "Hà Nội"},
    {"name": "Tập đoàn FPT (FPT Software)", "industry": "Viễn thông & CNTT", "tier": "Enterprise", "region": "Miền Bắc", "city": "Hà Nội"},
    {"name": "Tập đoàn Công nghiệp - Viễn thông Quân đội (Viettel)", "industry": "Viễn thông & CNTT", "tier": "Enterprise", "region": "Miền Bắc", "city": "Hà Nội"},
    {"name": "Ngân hàng TMCP Kỹ Thương Việt Nam (Techcombank)", "industry": "Ngân hàng & Tài chính", "tier": "Enterprise", "region": "Miền Bắc", "city": "Hà Nội"},
    {"name": "Ngân hàng TMCP Ngoại thương Việt Nam (Vietcombank)", "industry": "Ngân hàng & Tài chính", "tier": "Enterprise", "region": "Miền Bắc", "city": "Hà Nội"},
    {"name": "Ngân hàng TMCP Quân đội (MBBank)", "industry": "Ngân hàng & Tài chính", "tier": "Enterprise", "region": "Miền Bắc", "city": "Hà Nội"},
    {"name": "Ngân hàng TMCP Việt Nam Thịnh Vượng (VPBank)", "industry": "Ngân hàng & Tài chính", "tier": "Enterprise", "region": "Miền Bắc", "city": "Hà Nội"},
    {"name": "Ngân hàng TMCP Sài Gòn Thương Tín (Sacombank)", "industry": "Ngân hàng & Tài chính", "tier": "Enterprise", "region": "Miền Nam", "city": "TP. Hồ Chí Minh"},
    {"name": "Ngân hàng TMCP Á Châu (ACB)", "industry": "Ngân hàng & Tài chính", "tier": "Enterprise", "region": "Miền Nam", "city": "TP. Hồ Chí Minh"},
    {"name": "Tập đoàn Masan", "industry": "Bán lẻ & Thương mại điện tử", "tier": "Enterprise", "region": "Miền Nam", "city": "TP. Hồ Chí Minh"},
    {"name": "Công ty Cổ phần Sữa Việt Nam (Vinamilk)", "industry": "Sản xuất & Bất động sản", "tier": "Enterprise", "region": "Miền Nam", "city": "TP. Hồ Chí Minh"},
    {"name": "Công ty Cổ phần Đầu tư Thế Giới Di Động (MWG)", "industry": "Bán lẻ & Thương mại điện tử", "tier": "Enterprise", "region": "Miền Nam", "city": "TP. Hồ Chí Minh"},
    {"name": "Công ty Cổ phần VNG", "industry": "Viễn thông & CNTT", "tier": "Enterprise", "region": "Miền Nam", "city": "TP. Hồ Chí Minh"},
    {"name": "Công ty Cổ phần Dịch vụ Di động Trực tuyến (MoMo)", "industry": "Ngân hàng & Tài chính", "tier": "Enterprise", "region": "Miền Nam", "city": "TP. Hồ Chí Minh"},
    {"name": "Tiki Corporation", "industry": "Bán lẻ & Thương mại điện tử", "tier": "Mid-Market", "region": "Miền Nam", "city": "TP. Hồ Chí Minh"},
    {"name": "Shopee Việt Nam", "industry": "Bán lẻ & Thương mại điện tử", "tier": "Enterprise", "region": "Miền Nam", "city": "TP. Hồ Chí Minh"},
    {"name": "Giao Hàng Tiết Kiệm (GHTK)", "industry": "Logistics & Vận tải", "tier": "Enterprise", "region": "Miền Bắc", "city": "Hà Nội"},
    {"name": "Giao Hàng Nhanh (GHN)", "industry": "Logistics & Vận tải", "tier": "Mid-Market", "region": "Miền Nam", "city": "TP. Hồ Chí Minh"},
    {"name": "Hãng hàng không Vietjet Air", "industry": "Logistics & Vận tải", "tier": "Enterprise", "region": "Miền Nam", "city": "TP. Hồ Chí Minh"},
    {"name": "Tổng Công ty Hàng không Việt Nam (Vietnam Airlines)", "industry": "Logistics & Vận tải", "tier": "Enterprise", "region": "Miền Bắc", "city": "Hà Nội"},
    {"name": "Công ty Cổ phần Tập đoàn Hòa Phát", "industry": "Sản xuất & Bất động sản", "tier": "Enterprise", "region": "Miền Bắc", "city": "Hà Nội"},
    {"name": "Tập đoàn Sun Group", "industry": "Sản xuất & Bất động sản", "tier": "Enterprise", "region": "Miền Trung", "city": "Đà Nẵng"},
    {"name": "Tập đoàn Đất Xanh", "industry": "Sản xuất & Bất động sản", "tier": "Mid-Market", "region": "Miền Nam", "city": "TP. Hồ Chí Minh"},
    {"name": "Tổng Công ty Viễn thông MobiFone", "industry": "Viễn thông & CNTT", "tier": "Enterprise", "region": "Miền Bắc", "city": "Hà Nội"},
    {"name": "Tập đoàn Bưu chính Viễn thông Việt Nam (VNPT)", "industry": "Viễn thông & CNTT", "tier": "Enterprise", "region": "Miền Bắc", "city": "Hà Nội"},
    {"name": "Bệnh viện Đa khoa Quốc tế Vinmec", "industry": "Y tế & Giáo dục", "tier": "Enterprise", "region": "Miền Bắc", "city": "Hà Nội"},
    {"name": "Hệ thống Trường Quốc tế Vinschool", "industry": "Y tế & Giáo dục", "tier": "Mid-Market", "region": "Miền Bắc", "city": "Hà Nội"},
    {"name": "Đại học FPT", "industry": "Y tế & Giáo dục", "tier": "Mid-Market", "region": "Miền Bắc", "city": "Hà Nội"},
    {"name": "Bệnh viện Hoàn Mỹ Sài Gòn", "industry": "Y tế & Giáo dục", "tier": "Mid-Market", "region": "Miền Nam", "city": "TP. Hồ Chí Minh"},
    {"name": "Công ty Cổ phần Dược Hậu Giang", "industry": "Y tế & Giáo dục", "tier": "Enterprise", "region": "Miền Nam", "city": "Cần Thơ"},
    {"name": "Công ty TNHH Phần mềm CMC", "industry": "Viễn thông & CNTT", "tier": "Mid-Market", "region": "Miền Bắc", "city": "Hà Nội"},
    {"name": "Công ty Cổ phần Tập đoàn Hanaka", "industry": "Sản xuất & Bất động sản", "tier": "SMB", "region": "Miền Bắc", "city": "Hải Phòng"},
    {"name": "Công ty Cổ phần Thép Nam Kim", "industry": "Sản xuất & Bất động sản", "tier": "Mid-Market", "region": "Miền Nam", "city": "Bình Dương"},
    {"name": "Công ty Cổ phần Gỗ An Cường", "industry": "Sản xuất & Bất động sản", "tier": "Mid-Market", "region": "Miền Nam", "city": "Bình Dương"},
    {"name": "Công ty Cổ phần LogiNext Việt Nam", "industry": "Logistics & Vận tải", "tier": "SMB", "region": "Miền Trung", "city": "Đà Nẵng"},
    {"name": "Công ty TNHH Truyền thông Sài Gòn Mới", "industry": "Bán lẻ & Thương mại điện tử", "tier": "SMB", "region": "Miền Nam", "city": "TP. Hồ Chí Minh"},
    {"name": "Công ty TNHH Tư vấn & Giải pháp Số Tân Phát", "industry": "Viễn thông & CNTT", "tier": "SMB", "region": "Miền Bắc", "city": "Hà Nội"},
    {"name": "Công ty Cổ phần Xây dựng Coteccons", "industry": "Sản xuất & Bất động sản", "tier": "Enterprise", "region": "Miền Nam", "city": "TP. Hồ Chí Minh"},
    {"name": "Công ty Cổ phần Đầu tư Hạ tầng FECON", "industry": "Sản xuất & Bất động sản", "tier": "Mid-Market", "region": "Miền Bắc", "city": "Hà Nội"},
    {"name": "Ngân hàng Thương mại Cổ phần Quốc Tế Việt Nam (VIB)", "industry": "Ngân hàng & Tài chính", "tier": "Enterprise", "region": "Miền Bắc", "city": "Hà Nội"},
]

PRODUCTS = [
    {"id": "PROD-001", "name": "Enterprise Cloud & AI Infra", "category": "Enterprise Cloud & SaaS", "price": 120000000.0, "cycle": "Hàng tháng"},
    {"id": "PROD-002", "name": "AI Chatbot & Virtual Assistant Suite", "category": "AI & Data Solutions", "price": 85000000.0, "cycle": "Hàng tháng"},
    {"id": "PROD-003", "name": "Big Data & Business Intelligence Platform", "category": "AI & Data Solutions", "price": 150000000.0, "cycle": "Hàng tháng"},
    {"id": "PROD-004", "name": "Next-Gen Cyber Security & SOC Managed", "category": "Cyber Security", "price": 95000000.0, "cycle": "Hàng tháng"},
    {"id": "PROD-005", "name": "Enterprise Resource Planning (ERP) Module", "category": "ERP & Business Applications", "price": 220000000.0, "cycle": "Hàng tháng"},
    {"id": "PROD-006", "name": "Customer 360 & Omnichannel CRM", "category": "ERP & Business Applications", "price": 75000000.0, "cycle": "Hàng tháng"},
    {"id": "PROD-007", "name": "Core Banking & FinTech Connector API", "category": "Enterprise Cloud & SaaS", "price": 320000000.0, "cycle": "Hàng tháng"},
    {"id": "PROD-008", "name": "DevOps & Cloud Migration Consulting", "category": "Professional IT Services", "price": 180000000.0, "cycle": "Một lần"},
    {"id": "PROD-009", "name": "24/7 Premium IT Infrastructure Support", "category": "Professional IT Services", "price": 55000000.0, "cycle": "Hàng tháng"},
    {"id": "PROD-010", "name": "Smart Logistics & IoT Fleet Tracking", "category": "AI & Data Solutions", "price": 65000000.0, "cycle": "Hàng tháng"},
    {"id": "PROD-011", "name": "API Management & Microservices Gateway", "category": "Enterprise Cloud & SaaS", "price": 110000000.0, "cycle": "Hàng tháng"},
    {"id": "PROD-012", "name": "Identity & Access Management (IAM/SSO)", "category": "Cyber Security", "price": 70000000.0, "cycle": "Hàng tháng"},
]

SALES_REPS = [
    {"name": "Nguyen Hoang Long", "region": "Miền Bắc"},
    {"name": "Tran Thi Mai", "region": "Miền Bắc"},
    {"name": "Pham Quoc Bao", "region": "Miền Bắc"},
    {"name": "Le Van Dat", "region": "Miền Nam"},
    {"name": "Dang Thanh Huong", "region": "Miền Nam"},
    {"name": "Vo Minh Tri", "region": "Miền Nam"},
    {"name": "Bui Thu Trang", "region": "Miền Trung"},
    {"name": "Do Quang Huy", "region": "Miền Trung"}
]

PAYMENT_METHODS = ["Chuyển khoản doanh nghiệp", "Thanh toán theo mốc", "Thư tín dụng (LC)"]

# --- 2. Generate Dimension Data ---

print("Generating dim_customers...")
dim_customers_rows = []
for idx, cust in enumerate(ENTERPRISE_CUSTOMERS, start=1):
    cust_id = f"CUST-{idx:03d}"
    contact_p = f"Người đại diện {idx}"
    contact_email = f"contact.{cust_id.lower()}@domain.vn"
    contact_phone = f"09{random.randint(10000000, 99999999)}"
    created_at = datetime.date(2024, random.randint(1, 12), random.randint(1, 28))
    
    dim_customers_rows.append({
        "customer_id": cust_id,
        "customer_name": cust["name"],
        "contact_person": contact_p,
        "contact_email": contact_email,
        "contact_phone": contact_phone,
        "industry": cust["industry"],
        "customer_tier": cust["tier"],
        "region": cust["region"],
        "city": cust["city"],
        "is_active": "Y",
        "created_at": created_at
    })

df_customers = pd.DataFrame(dim_customers_rows)

print("Generating dim_products...")
dim_products_rows = []
for p in PRODUCTS:
    dim_products_rows.append({
        "product_id": p["id"],
        "product_name": p["name"],
        "category": p["category"],
        "unit_price_vnd": p["price"],
        "billing_cycle": p["cycle"],
        "is_active": "Y"
    })
df_products = pd.DataFrame(dim_products_rows)

# --- 3. Generate Fact Contracts ---

print("Generating fact_contracts...")
contracts_rows = []
contract_counter = 1

for cust in dim_customers_rows:
    # Each customer has 1 to 4 contracts
    num_contracts = 3 if cust["customer_tier"] == "Enterprise" else (2 if cust["customer_tier"] == "Mid-Market" else 1)
    rep_pool = [r for r in SALES_REPS if r["region"] == cust["region"]]
    if not rep_pool:
        rep_pool = SALES_REPS
    
    for _ in range(num_contracts):
        c_id = f"CTR-2025-{contract_counter:03d}" if contract_counter <= 50 else f"CTR-2026-{contract_counter:03d}"
        c_num = f"HD-{c_id}"
        
        sign_year = 2025 if contract_counter <= 50 else 2026
        sign_month = random.randint(1, 12)
        sign_day = random.randint(1, 28)
        sign_date = datetime.date(sign_year, sign_month, sign_day)
        
        start_date = sign_date + datetime.timedelta(days=random.randint(1, 15))
        duration_months = random.choice([6, 12, 24, 36])
        end_date = start_date + datetime.timedelta(days=duration_months * 30)
        
        c_value = round(random.uniform(500000000.0, 8000000000.0), -6) # 500M to 8B VND
        if cust["customer_tier"] == "Enterprise":
            c_value *= random.choice([2, 3, 5])
            
        p_term = random.choice(["Thanh toán 100% khi ký", "Theo tiến độ 40-40-20", "Thanh toán định kỳ theo quý", "Thanh toán hàng tháng"])
        status = random.choices(["Hiệu lực", "Đã gia hạn", "Chờ nghiệm thu", "Đã thanh lý"], weights=[0.65, 0.2, 0.1, 0.05])[0]
        rep = random.choice(rep_pool)["name"]
        
        contracts_rows.append({
            "contract_id": c_id,
            "contract_number": c_num,
            "customer_id": cust["customer_id"],
            "contract_title": f"Hợp đồng cung cấp giải pháp số {cust['customer_name']}",
            "sign_date": sign_date,
            "start_date": start_date,
            "end_date": end_date,
            "total_contract_value_vnd": c_value,
            "payment_terms": p_term,
            "contract_status": status,
            "sales_owner": rep
        })
        contract_counter += 1

df_contracts = pd.DataFrame(contracts_rows)

# --- 4. Generate Fact Sales Orders ---

print("Generating fact_sales_orders...")
orders_rows = []
order_id_counter = 1

start_date_orders = datetime.date(2025, 1, 1)
end_date_orders = datetime.date(2026, 12, 31)
total_days = (end_date_orders - start_date_orders).days + 1

# Generate 3000-4000 sales transactions across 2 years
for day_idx in range(total_days):
    curr_date = start_date_orders + datetime.timedelta(days=day_idx)
    is_weekend = curr_date.weekday() >= 5
    
    # 4 to 8 orders on weekdays, 1 to 3 on weekends
    num_orders_today = random.randint(1, 3) if is_weekend else random.randint(4, 8)
    
    for _ in range(num_orders_today):
        cust = random.choice(dim_customers_rows)
        prod = random.choice(dim_products_rows)
        
        # Find matching contracts for this customer
        cust_contracts = [c for c in contracts_rows if c["customer_id"] == cust["customer_id"]]
        contract_id = random.choice(cust_contracts)["contract_id"] if cust_contracts else None
        
        # Sales rep based on customer region
        rep_candidates = [r for r in SALES_REPS if r["region"] == cust["region"]]
        rep = random.choice(rep_candidates)["name"] if rep_candidates else random.choice(SALES_REPS)["name"]
        
        qty = random.choices([1, 2, 3, 5, 10], weights=[0.5, 0.25, 0.15, 0.07, 0.03])[0]
        unit_price = float(prod["unit_price_vnd"])
        gross = unit_price * qty
        
        # Discount logic
        discount_rate = random.choices([0.0, 0.05, 0.1, 0.15], weights=[0.6, 0.2, 0.15, 0.05])[0]
        discount_amount = round(gross * discount_rate, 2)
        net_revenue = gross - discount_amount
        tax_amount = round(net_revenue * 0.1, 2) # 10% VAT
        
        status = random.choices(["Hoàn thành", "Đang xử lý", "Đã hủy"], weights=[0.88, 0.09, 0.03])[0]
        if status == "Hoàn thành":
            pay_status = random.choices(["Đã thanh toán", "Chờ thanh toán", "Quá hạn"], weights=[0.85, 0.12, 0.03])[0]
        elif status == "Đang xử lý":
            pay_status = "Chờ thanh toán"
        else:
            pay_status = "Đã hủy"
            
        pay_method = random.choice(PAYMENT_METHODS)
        
        month_str = f"{curr_date.month:02d}/{curr_date.year}"
        quarter_str = f"Q{(curr_date.month - 1) // 3 + 1}"
        year_str = str(curr_date.year)
        date_key = int(curr_date.strftime("%Y%m%d"))
        
        order_id = f"ORD-{curr_date.year}-{order_id_counter:05d}"
        order_num = f"SO-{order_id}"
        
        orders_rows.append({
            "order_id": order_id,
            "order_number": order_num,
            "order_date": curr_date,
            "customer_id": cust["customer_id"],
            "customer_name": cust["customer_name"],
            "customer_industry": cust["industry"],
            "customer_tier": cust["customer_tier"],
            "product_id": prod["product_id"],
            "product_name": prod["product_name"],
            "product_category": prod["category"],
            "contract_id": contract_id,
            "quantity": qty,
            "unit_price_vnd": unit_price,
            "gross_amount_vnd": gross,
            "discount_amount_vnd": discount_amount,
            "net_revenue_vnd": net_revenue,
            "tax_amount_vnd": tax_amount,
            "order_status": status,
            "payment_status": pay_status,
            "payment_method": pay_method,
            "sales_representative": rep,
            "sales_region": cust["region"],
            "order_month_year": month_str,
            "order_quarter": quarter_str,
            "order_year": year_str,
            "d_order_date_key": date_key,
            "current_row_indicator": "Y"
        })
        order_id_counter += 1

df_orders = pd.DataFrame(orders_rows)

print(f"Total customers generated: {len(df_customers)}")
print(f"Total products generated: {len(df_products)}")
print(f"Total contracts generated: {len(df_contracts)}")
print(f"Total sales orders generated: {len(df_orders)}")

# --- 5. Export to CSV ---
os.makedirs("data", exist_ok=True)
print("Saving CSVs to workspace...")
df_customers.to_csv("dim_customers.csv", index=False, encoding="utf-8-sig")
df_products.to_csv("dim_products.csv", index=False, encoding="utf-8-sig")
df_contracts.to_csv("fact_contracts.csv", index=False, encoding="utf-8-sig")
df_orders.to_csv("fact_sales_orders.csv", index=False, encoding="utf-8-sig")

# --- 6. Export to SQL Seed File for MariaDB Container Initialization ---
seed_sql_path = os.path.join("init-mariadb", "02_sales_seed_data.sql")
print(f"Writing SQL seed to {seed_sql_path}...")

def escape_sql_str(val):
    if val is None or pd.isna(val):
        return "NULL"
    s = str(val).replace("'", "''").replace("\\", "\\\\")
    return f"'{s}'"

with open(seed_sql_path, "w", encoding="utf-8") as f:
    f.write("-- MariaDB Seed Data for Sales Domain\n")
    f.write("SET NAMES utf8mb4;\nUSE sales_db;\n\n")
    
    # 1. Insert customers
    f.write("-- 1. Insert dim_customers\n")
    for chunk_start in range(0, len(dim_customers_rows), 50):
        chunk = dim_customers_rows[chunk_start:chunk_start+50]
        values = []
        for r in chunk:
            v = f"({escape_sql_str(r['customer_id'])}, {escape_sql_str(r['customer_name'])}, {escape_sql_str(r['contact_person'])}, {escape_sql_str(r['contact_email'])}, {escape_sql_str(r['contact_phone'])}, {escape_sql_str(r['industry'])}, {escape_sql_str(r['customer_tier'])}, {escape_sql_str(r['region'])}, {escape_sql_str(r['city'])}, '{r['is_active']}', '{r['created_at']}')"
            values.append(v)
        f.write(f"INSERT INTO dim_customers VALUES\n" + ",\n".join(values) + ";\n\n")
        
    # 2. Insert products
    f.write("-- 2. Insert dim_products\n")
    values = []
    for r in dim_products_rows:
        v = f"({escape_sql_str(r['product_id'])}, {escape_sql_str(r['product_name'])}, {escape_sql_str(r['category'])}, {r['unit_price_vnd']}, {escape_sql_str(r['billing_cycle'])}, '{r['is_active']}')"
        values.append(v)
    f.write(f"INSERT INTO dim_products VALUES\n" + ",\n".join(values) + ";\n\n")

    # 3. Insert contracts
    f.write("-- 3. Insert fact_contracts\n")
    for chunk_start in range(0, len(contracts_rows), 50):
        chunk = contracts_rows[chunk_start:chunk_start+50]
        values = []
        for r in chunk:
            v = f"({escape_sql_str(r['contract_id'])}, {escape_sql_str(r['contract_number'])}, {escape_sql_str(r['customer_id'])}, {escape_sql_str(r['contract_title'])}, '{r['sign_date']}', '{r['start_date']}', '{r['end_date']}', {r['total_contract_value_vnd']}, {escape_sql_str(r['payment_terms'])}, {escape_sql_str(r['contract_status'])}, {escape_sql_str(r['sales_owner'])})"
            values.append(v)
        f.write(f"INSERT INTO fact_contracts VALUES\n" + ",\n".join(values) + ";\n\n")

    # 4. Insert sales orders
    f.write("-- 4. Insert fact_sales_orders\n")
    for chunk_start in range(0, len(orders_rows), 100):
        chunk = orders_rows[chunk_start:chunk_start+100]
        values = []
        for r in chunk:
            v = f"({escape_sql_str(r['order_id'])}, {escape_sql_str(r['order_number'])}, '{r['order_date']}', {escape_sql_str(r['customer_id'])}, {escape_sql_str(r['customer_name'])}, {escape_sql_str(r['customer_industry'])}, {escape_sql_str(r['customer_tier'])}, {escape_sql_str(r['product_id'])}, {escape_sql_str(r['product_name'])}, {escape_sql_str(r['product_category'])}, {escape_sql_str(r['contract_id'])}, {r['quantity']}, {r['unit_price_vnd']}, {r['gross_amount_vnd']}, {r['discount_amount_vnd']}, {r['net_revenue_vnd']}, {r['tax_amount_vnd']}, {escape_sql_str(r['order_status'])}, {escape_sql_str(r['payment_status'])}, {escape_sql_str(r['payment_method'])}, {escape_sql_str(r['sales_representative'])}, {escape_sql_str(r['sales_region'])}, {escape_sql_str(r['order_month_year'])}, {escape_sql_str(r['order_quarter'])}, {escape_sql_str(r['order_year'])}, {r['d_order_date_key']}, '{r['current_row_indicator']}')"
            values.append(v)
        f.write(f"INSERT INTO fact_sales_orders VALUES\n" + ",\n".join(values) + ";\n\n")

print("SQL Seed file created successfully!")

# --- 7. Optional Database Insertion if MariaDB is reachable ---
mariadb_uri = os.getenv("MARIADB_URL", "mysql+pymysql://sales_user:sales_pass@localhost:3306/sales_db")
try:
    print(f"Testing connection to MariaDB ({mariadb_uri})...")
    engine = create_engine(mariadb_uri, connect_args={"connect_timeout": 3})
    with engine.connect() as conn:
        print("Connected to MariaDB! Populating tables...")
        # If reachable, push DataFrames
        df_customers.to_sql("dim_customers", engine, if_exists="replace", index=False)
        df_products.to_sql("dim_products", engine, if_exists="replace", index=False)
        df_contracts.to_sql("fact_contracts", engine, if_exists="replace", index=False)
        df_orders.to_sql("fact_sales_orders", engine, if_exists="replace", index=False)
        print("Direct database insertion completed successfully!")
except Exception as e:
    print(f"Note: MariaDB local direct connection not reached ({e}). Data has been written to init-mariadb/02_sales_seed_data.sql and will auto-populate upon container start.")

print("All tasks completed successfully!")
