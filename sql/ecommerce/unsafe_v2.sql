-- RevertProof E-Commerce Example: INTENTIONALLY UNSAFE Migration (Shopping Site V2)
-- Renames `shipping_address` to `delivery_address_json` and drops `status`.
-- Application V2 reads the new columns, but rolling back to Shopping Site V1 FAILS.

ALTER TABLE orders
    RENAME COLUMN shipping_address TO delivery_address_json;

ALTER TABLE orders
    DROP COLUMN status;
