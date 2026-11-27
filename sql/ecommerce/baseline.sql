-- RevertProof E-Commerce Example: Baseline Schema (Shopping Site V1)
-- Demonstrates that RevertProof protects ANY relational database migration, not just banking.

CREATE TABLE orders (
    order_id VARCHAR(32) PRIMARY KEY,
    customer_email VARCHAR(255) NOT NULL,
    total_price NUMERIC(14, 2) NOT NULL,
    currency CHAR(3) NOT NULL,
    status VARCHAR(32) NOT NULL,
    shipping_address TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL
);
