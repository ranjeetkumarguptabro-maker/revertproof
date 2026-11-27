-- RevertProof Contract Migration (Stage 4: Contract Later)
-- WARNING: Execute ONLY in a later release AFTER Application V1 is permanently retired
-- and is no longer a rollback target.
-- Rollback Classification: ROLL_FORWARD_ONLY (once legacy columns are dropped, V1 cannot run).

DROP TRIGGER IF EXISTS trg_rp_sync_transactions ON transactions;
DROP FUNCTION IF EXISTS rp_sync_transactions_dual_write();

ALTER TABLE transactions
    ALTER COLUMN amount_minor SET NOT NULL,
    ALTER COLUMN currency_code SET NOT NULL;

ALTER TABLE transactions
    DROP COLUMN amount,
    DROP COLUMN currency;
