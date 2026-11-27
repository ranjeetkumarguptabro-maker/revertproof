-- RevertProof E-Commerce Example: SAFE Expand Migration (Shopping Site V2)
-- Adds `order_state` and `total_price_minor` while retaining legacy `status`, `total_price`,
-- and `shipping_address` during the rollback compatibility window.

ALTER TABLE orders
    ADD COLUMN order_state VARCHAR(32),
    ADD COLUMN total_price_minor BIGINT,
    ADD COLUMN delivery_address_json TEXT;

UPDATE orders
SET
    order_state = status,
    total_price_minor = ROUND(total_price * 100)::BIGINT,
    delivery_address_json = '{"raw": "' || REPLACE(shipping_address, '"', '\"') || '"}';
