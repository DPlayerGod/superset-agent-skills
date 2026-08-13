-- Create Table fact_employee_allocation
CREATE TABLE IF NOT EXISTS fact_employee_allocation (
    working_date DATE NOT NULL,
    project_allocated_hc DOUBLE PRECISION NOT NULL,
    current_row_indicator VARCHAR(1) DEFAULT 'Y',
    employee_full_name VARCHAR(255) NOT NULL,
    employee_id VARCHAR(50) NOT NULL,
    employee_email VARCHAR(255),
    employee_role VARCHAR(100),
    employee_level VARCHAR(50),
    project_name VARCHAR(255) NOT NULL,
    organization_name VARCHAR(255) NOT NULL,
    organization_code VARCHAR(50) NOT NULL,
    working_month_year VARCHAR(10) NOT NULL,
    working_quarter VARCHAR(2) NOT NULL,
    working_year VARCHAR(4) NOT NULL,
    working_is_business_day INTEGER NOT NULL,
    d_working_date_key INTEGER NOT NULL
);

-- Create indexes for optimization
CREATE INDEX IF NOT EXISTS idx_employee_alloc_date ON fact_employee_allocation(working_date);
CREATE INDEX IF NOT EXISTS idx_employee_alloc_emp_id ON fact_employee_allocation(employee_id);
CREATE INDEX IF NOT EXISTS idx_employee_alloc_project ON fact_employee_allocation(project_name);
