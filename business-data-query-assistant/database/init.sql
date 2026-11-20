-- The Docker image runs this script once when it creates a new database.
\set ON_ERROR_STOP on
\getenv reader_password REPORT_READER_PASSWORD

CREATE ROLE report_reader LOGIN PASSWORD :'reader_password';
ALTER ROLE report_reader SET default_transaction_read_only = on;
REVOKE TEMPORARY ON DATABASE business_demo FROM PUBLIC;

CREATE SCHEMA shop;
CREATE TABLE shop.orders (
    order_id integer PRIMARY KEY,
    order_date date NOT NULL,
    region text NOT NULL,
    product text NOT NULL,
    quantity integer NOT NULL CHECK (quantity > 0),
    revenue numeric(10,2) NOT NULL CHECK (revenue >= 0)
);

-- Fictional orders. The revenue column stores the total for each order in INR.
INSERT INTO shop.orders VALUES
    (1, '2025-01-05', 'South', 'Keyboard', 2, 3000),
    (2, '2025-01-09', 'West',  'Mouse',    3, 2400),
    (3, '2025-01-15', 'North', 'Monitor',  1, 12500),
    (4, '2025-01-22', 'South', 'Mouse',    4, 3200),
    (5, '2025-02-03', 'East',  'Keyboard', 3, 4500),
    (6, '2025-02-08', 'West',  'Monitor',  2, 25000),
    (7, '2025-02-13', 'South', 'Monitor',  1, 12500),
    (8, '2025-02-18', 'North', 'Mouse',    5, 4000),
    (9, '2025-03-01', 'East',  'Mouse',    2, 1600),
    (10, '2025-03-11', 'West', 'Keyboard', 2, 3000),
    (11, '2025-03-14', 'North','Keyboard', 4, 6000),
    (12, '2025-03-20', 'South','Monitor',  2, 25000);

-- The app connects as report_reader and can read this view only.
CREATE SCHEMA reporting;
CREATE VIEW reporting.sales AS
SELECT order_id, order_date, region, product, quantity, revenue FROM shop.orders;
GRANT CONNECT ON DATABASE business_demo TO report_reader;
GRANT USAGE ON SCHEMA reporting TO report_reader;
GRANT SELECT ON reporting.sales TO report_reader;
