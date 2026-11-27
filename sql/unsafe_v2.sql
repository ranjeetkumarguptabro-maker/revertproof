-- RevertProof INTENTIONALLY UNSAFE Migration (Banking V2)
-- Changes `amount` from NUMERIC(14,2) into JSONB {"value_minor": ..., "currency": ...}
-- and immediately drops the legacy `currency` column.
-- Result: Application V2 works, but rolling back to Application V1 FAILS.

ALTER TABLE transactions
    ALTER COLUMN amount TYPE JSONB
    USING jsonb_build_object(
        'value_minor', ROUND(amount * 100)::BIGINT,
        'currency', TRIM(currency)
    );

ALTER TABLE transactions
    DROP COLUMN currency;
