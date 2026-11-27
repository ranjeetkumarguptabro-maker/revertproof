-- RevertProof SAFE Expand-and-Contract Migration (Stage 1 & 2: Expand + Backfill + Dual-Write Sync)
-- Retains legacy `amount` and `currency` columns so Application V1 remains fully compatible
-- during the rollback window, while Application V2 reads and writes `amount_minor` and `currency_code`.

ALTER TABLE transactions
    ADD COLUMN amount_minor BIGINT,
    ADD COLUMN currency_code CHAR(3);

-- Backfill existing rows using exact integer minor units (EUR/USD/GBP = 2 decimal places)
UPDATE transactions
SET
    amount_minor = ROUND(amount * 100)::BIGINT,
    currency_code = TRIM(currency);

-- Bidirectional dual-write synchronisation trigger:
--   * V1 writes (amount, currency) -> auto-populates (amount_minor, currency_code)
--   * V2 writes (amount_minor, currency_code) -> auto-populates (amount, currency)
--   * Conflicting values written simultaneously -> rejected inside the same DB transaction
CREATE OR REPLACE FUNCTION rp_sync_transactions_dual_write()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.amount IS NOT NULL AND NEW.amount_minor IS NOT NULL THEN
            IF ROUND(NEW.amount * 100)::BIGINT <> NEW.amount_minor THEN
                RAISE EXCEPTION 'Conflicting dual-write amounts: amount=% vs amount_minor=%', NEW.amount, NEW.amount_minor;
            END IF;
        ELSIF NEW.amount IS NOT NULL AND NEW.amount_minor IS NULL THEN
            NEW.amount_minor := ROUND(NEW.amount * 100)::BIGINT;
        ELSIF NEW.amount IS NULL AND NEW.amount_minor IS NOT NULL THEN
            NEW.amount := (NEW.amount_minor::NUMERIC / 100.0);
        END IF;

        IF NEW.currency IS NOT NULL AND NEW.currency_code IS NOT NULL THEN
            IF TRIM(NEW.currency) <> TRIM(NEW.currency_code) THEN
                RAISE EXCEPTION 'Conflicting dual-write currencies: currency=% vs currency_code=%', NEW.currency, NEW.currency_code;
            END IF;
        ELSIF NEW.currency IS NOT NULL AND NEW.currency_code IS NULL THEN
            NEW.currency_code := TRIM(NEW.currency);
        ELSIF NEW.currency IS NULL AND NEW.currency_code IS NOT NULL THEN
            NEW.currency := TRIM(NEW.currency_code);
        END IF;
    ELSIF TG_OP = 'UPDATE' THEN
        IF NEW.amount IS DISTINCT FROM OLD.amount AND NEW.amount_minor IS DISTINCT FROM OLD.amount_minor THEN
            IF ROUND(NEW.amount * 100)::BIGINT <> NEW.amount_minor THEN
                RAISE EXCEPTION 'Conflicting dual-write update amounts: amount=% vs amount_minor=%', NEW.amount, NEW.amount_minor;
            END IF;
        ELSIF NEW.amount IS DISTINCT FROM OLD.amount THEN
            NEW.amount_minor := ROUND(NEW.amount * 100)::BIGINT;
        ELSIF NEW.amount_minor IS DISTINCT FROM OLD.amount_minor THEN
            NEW.amount := (NEW.amount_minor::NUMERIC / 100.0);
        END IF;

        IF NEW.currency IS DISTINCT FROM OLD.currency AND NEW.currency_code IS DISTINCT FROM OLD.currency_code THEN
            IF TRIM(NEW.currency) <> TRIM(NEW.currency_code) THEN
                RAISE EXCEPTION 'Conflicting dual-write update currencies: currency=% vs currency_code=%', NEW.currency, NEW.currency_code;
            END IF;
        ELSIF NEW.currency IS DISTINCT FROM OLD.currency THEN
            NEW.currency_code := TRIM(NEW.currency);
        ELSIF NEW.currency_code IS DISTINCT FROM OLD.currency_code THEN
            NEW.currency := TRIM(NEW.currency_code);
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_rp_sync_transactions ON transactions;
CREATE TRIGGER trg_rp_sync_transactions
BEFORE INSERT OR UPDATE ON transactions
FOR EACH ROW
EXECUTE FUNCTION rp_sync_transactions_dual_write();
