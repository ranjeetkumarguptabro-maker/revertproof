"""Integration tests for Module 2 (Compatibility Tester) & Module 4 (Dual-Write Sync)."""

from __future__ import annotations

import os
from pathlib import Path

from revertproof.compatibility import run_banking_compatibility, run_ecommerce_compatibility
from revertproof.db import isolated_database
from revertproof.integrity import check_banking_integrity, check_ecommerce_integrity
from revertproof.models import CheckStatus

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_DB_URL = os.getenv("RP_DATABASE_URL", "sqlite:///:memory:")


def test_banking_unsafe_migration_v2_passes_v1_fails() -> None:
    unsafe_sql = (PROJECT_ROOT / "sql" / "unsafe_v2.sql").read_text(encoding="utf-8")
    with isolated_database(TEST_DB_URL) as db:
        compat, fixtures = run_banking_compatibility(db, unsafe_sql, fixture_count=1000)
        assert compat.migration_applied is True
        assert compat.fixture_count == 1000
        assert compat.v2_result.status == CheckStatus.PASS
        assert compat.v1_result.status == CheckStatus.FAIL
        assert compat.rollback_verified is False
        # Verify integrity checker reports NOT_RUN because legacy currency column was dropped
        integrity = check_banking_integrity(db, fixtures)
        assert integrity.status == CheckStatus.NOT_RUN


def test_banking_safe_expand_1000_tx_dual_write_and_integrity() -> None:
    safe_sql = (PROJECT_ROOT / "sql" / "safe_expand.sql").read_text(encoding="utf-8")
    with isolated_database(TEST_DB_URL) as db:
        compat, fixtures = run_banking_compatibility(db, safe_sql, fixture_count=1000)
        assert compat.migration_applied is True
        assert compat.v2_result.status == CheckStatus.PASS
        assert compat.v1_result.status == CheckStatus.PASS
        assert compat.dual_write_verified is True
        assert compat.rollback_verified is True

        # Ensure conflict guard operation executed and passed
        conflict_ops = [
            op for op in compat.v1_result.operations if op.name == "dual_write_conflict_guard"
        ]
        assert len(conflict_ops) == 1 and conflict_ops[0].passed is True

        integrity = check_banking_integrity(db, fixtures)
        assert integrity.status == CheckStatus.PASS
        assert integrity.records_preserved == 1000
        assert integrity.amount_mismatches == 0
        assert integrity.currency_mismatches == 0
        assert integrity.missing_ids == []
        assert integrity.duplicate_ids == []


def test_ecommerce_unsafe_and_safe_migrations_end_to_end() -> None:
    unsafe_sql = (PROJECT_ROOT / "sql" / "ecommerce" / "unsafe_v2.sql").read_text(encoding="utf-8")
    with isolated_database(TEST_DB_URL) as db:
        compat, _ = run_ecommerce_compatibility(db, unsafe_sql, fixture_count=200)
        assert compat.v2_result.status == CheckStatus.PASS
        assert compat.v1_result.status == CheckStatus.FAIL

    safe_sql = (PROJECT_ROOT / "sql" / "ecommerce" / "safe_expand.sql").read_text(encoding="utf-8")
    with isolated_database(TEST_DB_URL) as db:
        compat, fixtures = run_ecommerce_compatibility(db, safe_sql, fixture_count=200)
        assert compat.v2_result.status == CheckStatus.PASS
        assert compat.v1_result.status == CheckStatus.PASS
        integrity = check_ecommerce_integrity(db, fixtures)
        assert integrity.status == CheckStatus.PASS
        assert integrity.records_preserved == 200
