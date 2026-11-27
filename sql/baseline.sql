-- RevertProof Baseline Schema (Banking V1)
-- Synthetic financial transactions only. Never run against production data.

CREATE TABLE transactions (
    transaction_id VARCHAR(32) PRIMARY KEY,
    amount NUMERIC(14, 2) NOT NULL,
    currency CHAR(3) NOT NULL,
    occurred_at TIMESTAMP NOT NULL
);

CREATE INDEX idx_transactions_occurred_at ON transactions (occurred_at);
