import random
import datetime
import calendar
import pandas as pd
from sqlalchemy import create_engine

# Set random seed for reproducibility
random.seed(42)

# --- Define pool of names and attributes ---
LAST_NAMES = ["Nguyen", "Tran", "Le", "Pham", "Hoang", "Huynh", "Phan", "Vu", "Vo", "Dang", "Bui", "Do", "Ngo", "Duong", "Ly"]
MIDDLE_NAMES = ["Van", "Thi", "Minh", "Quang", "Huu", "Duc", "Ngoc", "Anh", "Quoc", "Duy", "Thanh", "Hai", "Xuan", "Hoai", "Trong"]
FIRST_NAMES = ["Anh", "Binh", "Chi", "Dung", "Em", "Giang", "Hung", "Huong", "Khanh", "Lan", "Linh", "Nam", "Phong", "Phuc", "Quynh", "Son", "Thao", "Trang", "Tuan", "Vy", "Yen", "Huy", "Long", "Viet", "Hai"]

ROLES = ["DATA", "BA", "Tester", "DEV", "PP", "DE", "DUL"]
LEVELS = ["J", "J+", "M", "M+", "F", "S"]
PROJECTS = ["Project Alpha", "Project Beta", "Project Gamma", "Project Delta", "Project Epsilon", "Project Omega", "Project Zeta"]

# Organization mappings based on Role
ORG_MAPPING = {
    "DATA": {"name": "Data Analytics", "code": "ORG-DA"},
    "DE": {"name": "Data Analytics", "code": "ORG-DA"},
    "DEV": {"name": "Software Development", "code": "ORG-SD"},
    "BA": {"name": "Product Management", "code": "ORG-PM"},
    "PP": {"name": "Product Management", "code": "ORG-PM"},
    "DUL": {"name": "Product Management", "code": "ORG-PM"},
    "Tester": {"name": "Quality Assurance", "code": "ORG-QA"}
}

def remove_vietnamese_accents(s):
    import unicodedata
    s = s.replace('đ', 'd').replace('Đ', 'd')
    normalized = unicodedata.normalize('NFD', s)
    return "".join(c for c in normalized if unicodedata.category(c) != 'Mn')

def generate_email(full_name, emp_id):
    clean_name = remove_vietnamese_accents(full_name.lower())
    parts = clean_name.split()
    # E.g. "nguyen.van.anh@company.com"
    email_name = ".".join(parts)
    return f"{email_name}.{emp_id.lower()}@company.com"

# 1. Generate 100 unique employees
employees = []
generated_names = set()

while len(employees) < 100:
    ln = random.choice(LAST_NAMES)
    mn = random.choice(MIDDLE_NAMES)
    fn = random.choice(FIRST_NAMES)
    full_name = f"{ln} {mn} {fn}"
    
    # Avoid duplicate names just in case
    if full_name in generated_names:
        continue
    generated_names.add(full_name)
    
    emp_id = f"EMP-{len(employees) + 1:03d}"
    role = random.choice(ROLES)
    level = random.choice(LEVELS)
    email = generate_email(full_name, emp_id)
    org = ORG_MAPPING[role]
    
    # Pre-select 1 to 3 projects that this employee is assigned to work on
    num_allowed_projects = random.choices([1, 2, 3], weights=[0.2, 0.6, 0.2])[0]
    allowed_projects = random.sample(PROJECTS, num_allowed_projects)
    
    employees.append({
        "employee_id": emp_id,
        "employee_full_name": full_name,
        "employee_email": email,
        "employee_role": role,
        "employee_level": level,
        "organization_name": org["name"],
        "organization_code": org["code"],
        "allowed_projects": allowed_projects
    })

print(f"Generated {len(employees)} unique employees.")

# 2. Build daily allocation registry for 2026
all_rows = []

# Generate month ranges for 2026
start_date = datetime.date(2026, 1, 1)
end_date = datetime.date(2026, 12, 31)

# Generate a list of dates in 2026
dates_2026 = [start_date + datetime.timedelta(days=x) for x in range((end_date - start_date).days + 1)]

# Group dates by month for easy processing
dates_by_month = {}
for dt in dates_2026:
    month_key = (dt.year, dt.month)
    if month_key not in dates_by_month:
        dates_by_month[month_key] = []
    dates_by_month[month_key].append(dt)

# Process month by month
for (year, month), month_dates in dates_by_month.items():
    # Calculate business days in this month
    business_days = [d for d in month_dates if d.weekday() < 5]
    total_biz_days = len(business_days)
    
    # MM/YYYY string representation
    month_year_str = f"{month:02d}/{year}"
    quarter_str = f"Q{(month - 1) // 3 + 1}"
    year_str = str(year)
    
    # Assign monthly allocations for each employee
    for emp in employees:
        # Determine number of projects for this month (70% 1 project, 30% 2 projects)
        allowed = emp["allowed_projects"]
        num_proj = random.choices([1, 2], weights=[0.7, 0.3])[0]
        num_proj = min(num_proj, len(allowed))
        
        assigned_projects = random.sample(allowed, num_proj)
        
        # Determine total Monthly FTE allocation: 1.0 (80%), 0.8 (10%), 0.5 (10%)
        total_fte = random.choices([1.0, 0.8, 0.5], weights=[0.8, 0.1, 0.1])[0]
        
        # Distribute fte among assigned projects
        project_ftes = {}
        if len(assigned_projects) == 1:
            project_ftes[assigned_projects[0]] = total_fte
        else:
            # 2 projects split
            if total_fte == 1.0:
                split = random.choice([0.5, 0.6, 0.7])
            elif total_fte == 0.8:
                split = random.choice([0.4, 0.5])
            else: # 0.5
                split = random.choice([0.25, 0.3])
            
            project_ftes[assigned_projects[0]] = split
            project_ftes[assigned_projects[1]] = round(total_fte - split, 2)
            
        # For each day in the month, write rows for each project
        for dt in month_dates:
            is_biz_day = 1 if dt.weekday() < 5 else 0
            date_str = dt.strftime("%Y-%m-%d")
            date_key = int(dt.strftime("%Y%m%d"))
            
            for proj_name, fte in project_ftes.items():
                if is_biz_day:
                    daily_hc = fte / total_biz_days
                else:
                    daily_hc = 0.0
                    
                all_rows.append({
                    "working_date": dt,
                    "project_allocated_hc": round(daily_hc, 6),
                    "current_row_indicator": "Y",
                    "employee_full_name": emp["employee_full_name"],
                    "employee_id": emp["employee_id"],
                    "employee_email": emp["employee_email"],
                    "employee_role": emp["employee_role"],
                    "employee_level": emp["employee_level"],
                    "project_name": proj_name,
                    "organization_name": emp["organization_name"],
                    "organization_code": emp["organization_code"],
                    "working_month_year": month_year_str,
                    "working_quarter": quarter_str,
                    "working_year": year_str,
                    "working_is_business_day": is_biz_day,
                    "d_working_date_key": date_key
                })

print(f"Generated {len(all_rows)} daily allocation records.")

# Convert to DataFrame
df = pd.DataFrame(all_rows)

# 3. Database Insertion
print("Connecting to database...")
engine = create_engine("postgresql://super_user:super_pass@localhost:5432/super_db")

from sqlalchemy import text
print("Clearing existing records...")
with engine.begin() as conn:
    conn.execute(text("TRUNCATE TABLE fact_employee_allocation;"))
    
print("Inserting data into fact_employee_allocation...")
df.to_sql("fact_employee_allocation", engine, if_exists="append", index=False)

print("Insertion complete successfully!")
