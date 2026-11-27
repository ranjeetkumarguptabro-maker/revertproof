"""Module 4: Safe Migration Engine (Expand-and-Contract Guidance & Rollback Classifier).

Never claims every migration is reversible. Classifies migrations into:
- SAFE_ROLLBACK
- CONDITIONAL_ROLLBACK
- ROLL_FORWARD_ONLY
- MANUAL_RECOVERY_REQUIRED
"""

from __future__ import annotations

from revertproof.models import (
    CheckStatus,
    CompatibilityReport,
    ExpandContractStep,
    IntegrityReport,
    MigrationGuidance,
    RollbackClassification,
    ScanReport,
)


def classify_rollback(
    scan: ScanReport,
    compat: CompatibilityReport | None,
    integrity: IntegrityReport | None,
) -> RollbackClassification:
    """Determine honest rollback classification from static + execution evidence."""
    if scan.rollback_classification == RollbackClassification.MANUAL_RECOVERY_REQUIRED:
        return RollbackClassification.MANUAL_RECOVERY_REQUIRED

    if compat is None or not compat.migration_applied:
        return RollbackClassification.CONDITIONAL_ROLLBACK

    if compat.v1_result.status == CheckStatus.FAIL:
        return RollbackClassification.ROLL_FORWARD_ONLY

    if integrity is not None and integrity.status == CheckStatus.FAIL:
        return RollbackClassification.MANUAL_RECOVERY_REQUIRED

    if (
        compat.v1_result.status == CheckStatus.PASS
        and compat.v2_result.status == CheckStatus.PASS
        and (integrity is None or integrity.status == CheckStatus.PASS)
    ):
        if compat.dual_write_verified:
            return RollbackClassification.SAFE_ROLLBACK
        return RollbackClassification.CONDITIONAL_ROLLBACK

    return RollbackClassification.CONDITIONAL_ROLLBACK


def build_expand_contract_guidance(
    scan: ScanReport,
    compat: CompatibilityReport | None,
    integrity: IntegrityReport | None,
    domain: str = "banking",
) -> MigrationGuidance:
    """Produce actionable Expand-and-Contract stages tailored to the domain and findings."""
    classification = classify_rollback(scan, compat, integrity)

    if domain == "ecommerce":
        stages = [
            ExpandContractStep(
                stage="Stage 1 — Expand",
                title="Add new columns without dropping legacy fields",
                description="Add order_state, total_price_minor, and delivery_address_json while keeping status and shipping_address intact.",
                sql_example=(
                    "ALTER TABLE orders\n"
                    "  ADD COLUMN order_state VARCHAR(32),\n"
                    "  ADD COLUMN total_price_minor BIGINT,\n"
                    "  ADD COLUMN delivery_address_json TEXT;"
                ),
            ),
            ExpandContractStep(
                stage="Stage 2 — Backfill & Dual-Write",
                title="Backfill existing orders and synchronise live writes",
                description="Populate new columns from legacy columns and keep both representations synchronised while V1 and V2 overlap.",
                sql_example=(
                    "UPDATE orders SET order_state = status,\n"
                    "  total_price_minor = ROUND(total_price * 100)::BIGINT;"
                ),
            ),
            ExpandContractStep(
                stage="Stage 3 — Compatibility Verification",
                title="Verify Shopping Site V1 & V2 against migrated DB",
                description="Confirm V2 reads/writes new columns AND V1 rollback reads/writes legacy columns with zero order discrepancies.",
                sql_example="revertproof verify sql/ecommerce/safe_expand.sql --domain ecommerce",
            ),
            ExpandContractStep(
                stage="Stage 4 — Contract Later",
                title="Drop legacy columns only after V1 is retired",
                description="In a separate future release once V1 is no longer a rollback target, drop legacy columns.",
                sql_example="ALTER TABLE orders DROP COLUMN status, DROP COLUMN shipping_address;",
            ),
        ]
    else:
        stages = [
            ExpandContractStep(
                stage="Stage 1 — Expand",
                title="Add minor-unit columns without dropping legacy fields",
                description="Add amount_minor (BIGINT) and currency_code (CHAR(3)) while keeping legacy amount (NUMERIC) and currency (CHAR(3)).",
                sql_example=(
                    "ALTER TABLE transactions\n"
                    "  ADD COLUMN amount_minor BIGINT,\n"
                    "  ADD COLUMN currency_code CHAR(3);"
                ),
            ),
            ExpandContractStep(
                stage="Stage 2 — Backfill & Dual-Write Sync",
                title="Backfill exact minor units and attach bidirectional trigger",
                description="Backfill existing rows with ROUND(amount * 100)::BIGINT and synchronise V1/V2 INSERTs and UPDATEs in the same DB transaction.",
                sql_example=(
                    "UPDATE transactions\n"
                    "SET amount_minor = ROUND(amount * 100)::BIGINT,\n"
                    "    currency_code = TRIM(currency);\n"
                    "-- Plus BEFORE INSERT OR UPDATE dual-write trigger rejecting conflicts"
                ),
            ),
            ExpandContractStep(
                stage="Stage 3 — Compatibility Window",
                title="Verify V1 + V2 reads/writes and exact financial parity",
                description="Deploy V2 while keeping V1 rollback-ready. Verify 100% of transaction IDs, minor-unit amounts, currencies, and timestamps.",
                sql_example="revertproof verify sql/safe_expand.sql --domain banking --fixtures 1000",
            ),
            ExpandContractStep(
                stage="Stage 4 — Contract Later",
                title="Remove legacy columns in a separate later release",
                description="Only after V1 is permanently retired and no longer a rollback target, run sql/safe_contract.sql (classified ROLL_FORWARD_ONLY).",
                sql_example="ALTER TABLE transactions DROP COLUMN amount, DROP COLUMN currency;",
            ),
        ]

    rationale_map = {
        RollbackClassification.SAFE_ROLLBACK: (
            "Both V1 and V2 read/write contracts passed with bidirectional dual-write "
            "synchronisation and exact data integrity parity. Rolling back application code "
            "to V1 is proven safe for the tested workload."
        ),
        RollbackClassification.CONDITIONAL_ROLLBACK: (
            "Rollback safety is conditional: either static warnings exist, dual-write sync was "
            "not verified, or test infrastructure was unavailable. Requires manual review."
        ),
        RollbackClassification.ROLL_FORWARD_ONLY: (
            "Application V1 cannot operate against this migrated schema (e.g., dropped or "
            "retyped column). Code rollback to V1 WILL FAIL. Use Expand-and-Contract instead, "
            "or treat this release strictly as roll-forward-only with an explicit recovery plan."
        ),
        RollbackClassification.MANUAL_RECOVERY_REQUIRED: (
            "Destructive data loss or integrity corruption detected (e.g., DROP TABLE, TRUNCATE, "
            "missing rows, or amount corruption). Automated rollback is impossible; restore from "
            "point-in-time backup / RDS snapshot."
        ),
    }

    return MigrationGuidance(
        rollback_classification=classification,
        rationale=rationale_map[classification],
        stages=stages,
    )
