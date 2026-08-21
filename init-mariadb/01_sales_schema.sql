-- Database: sales_db
-- Domain: Sales & Revenue (Khách hàng, Sản phẩm, Hợp đồng, Đơn hàng & Doanh thu)

-- 1. Dimension Table: dim_customers
CREATE TABLE IF NOT EXISTS dim_customers (
    customer_id VARCHAR(50) PRIMARY KEY,
    customer_name VARCHAR(255) NOT NULL,
    contact_person VARCHAR(255),
    contact_email VARCHAR(255),
    contact_phone VARCHAR(50),
    industry VARCHAR(100) NOT NULL,
    customer_tier VARCHAR(50) NOT NULL,
    region VARCHAR(50) NOT NULL,
    city VARCHAR(100) NOT NULL,
    is_active VARCHAR(1) DEFAULT 'Y',
    created_at DATE NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 2. Dimension Table: dim_products
CREATE TABLE IF NOT EXISTS dim_products (
    product_id VARCHAR(50) PRIMARY KEY,
    product_name VARCHAR(255) NOT NULL,
    category VARCHAR(100) NOT NULL,
    unit_price_vnd DECIMAL(15, 2) NOT NULL,
    billing_cycle VARCHAR(50) NOT NULL,
    is_active VARCHAR(1) DEFAULT 'Y'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3. Fact Table: fact_contracts
CREATE TABLE IF NOT EXISTS fact_contracts (
    contract_id VARCHAR(50) PRIMARY KEY,
    contract_number VARCHAR(100) NOT NULL,
    customer_id VARCHAR(50) NOT NULL,
    contract_title VARCHAR(255) NOT NULL,
    sign_date DATE NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    total_contract_value_vnd DECIMAL(18, 2) NOT NULL,
    payment_terms VARCHAR(100),
    contract_status VARCHAR(50) NOT NULL,
    sales_owner VARCHAR(100) NOT NULL,
    INDEX idx_contracts_customer (customer_id),
    INDEX idx_contracts_status (contract_status),
    INDEX idx_contracts_sign_date (sign_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 4. Fact Table: fact_sales_orders
CREATE TABLE IF NOT EXISTS fact_sales_orders (
    order_id VARCHAR(50) PRIMARY KEY,
    order_number VARCHAR(100) NOT NULL,
    order_date DATE NOT NULL,
    customer_id VARCHAR(50) NOT NULL,
    customer_name VARCHAR(255) NOT NULL,
    customer_industry VARCHAR(100) NOT NULL,
    customer_tier VARCHAR(50) NOT NULL,
    product_id VARCHAR(50) NOT NULL,
    product_name VARCHAR(255) NOT NULL,
    product_category VARCHAR(100) NOT NULL,
    contract_id VARCHAR(50),
    quantity INT NOT NULL,
    unit_price_vnd DECIMAL(15, 2) NOT NULL,
    gross_amount_vnd DECIMAL(18, 2) NOT NULL,
    discount_amount_vnd DECIMAL(15, 2) DEFAULT 0.00,
    net_revenue_vnd DECIMAL(18, 2) NOT NULL,
    tax_amount_vnd DECIMAL(15, 2) DEFAULT 0.00,
    order_status VARCHAR(50) NOT NULL,
    payment_status VARCHAR(50) NOT NULL,
    payment_method VARCHAR(100) NOT NULL,
    sales_representative VARCHAR(100) NOT NULL,
    sales_region VARCHAR(50) NOT NULL,
    order_month_year VARCHAR(10) NOT NULL,
    order_quarter VARCHAR(2) NOT NULL,
    order_year VARCHAR(4) NOT NULL,
    d_order_date_key INT NOT NULL,
    current_row_indicator VARCHAR(1) DEFAULT 'Y',
    INDEX idx_orders_date (order_date),
    INDEX idx_orders_customer (customer_id),
    INDEX idx_orders_product (product_id),
    INDEX idx_orders_rep (sales_representative),
    INDEX idx_orders_status (order_status),
    INDEX idx_orders_region (sales_region),
    INDEX idx_orders_year_month (order_month_year)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
