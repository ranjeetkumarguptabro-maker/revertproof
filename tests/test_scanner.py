"""Unit tests for Module 1: SQL Migration Scanner."""

from __future__ import annotations

from pathlib import Path

from revertproof.models import RiskSeverity, RollbackClassification, ScannerExceptionRule
from revertproof.scanner import scan_file, scan_sql

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_unsafe_banking_migration_flagged_critical() -> None:
    report = scan_file(PROJECT_ROOT / "sql" / "unsafe_v2.sql")
    rule_ids = {f.rule_id for f in report.findings}
    assert "ALTER_COLUMN_TYPE" in rule_ids
    assert "DROP_COLUMN" in rule_ids
    assert report.highest_severity == RiskSeverity.CRITICAL
    assert report.rollback_classification == RollbackClassification.ROLL_FORWARD_ONLY


def test_safe_expand_banking_migration_is_clean() -> None:
    report = scan_file(PROJECT_ROOT / "sql" / "safe_expand.sql")
    assert report.highest_severity == RiskSeverity.SAFE
    assert report.rollback_classification == RollbackClassification.SAFE_ROLLBACK
    assert report.findings == []


def test_contract_migration_flagged_roll_forward_only() -> None:
    report = scan_file(PROJECT_ROOT / "sql" / "safe_contract.sql")
    rule_ids = {f.rule_id for f in report.findings}
    assert "DROP_COLUMN" in rule_ids
    assert "SET_NOT_NULL" in rule_ids
    assert report.rollback_classification == RollbackClassification.ROLL_FORWARD_ONLY


def test_ecommerce_unsafe_and_safe_migrations() -> None:
    unsafe = scan_file(PROJECT_ROOT / "sql" / "ecommerce" / "unsafe_v2.sql")
    assert {"RENAME_COLUMN", "DROP_COLUMN"} <= {f.rule_id for f in unsafe.findings}
    assert unsafe.highest_severity == RiskSeverity.CRITICAL

    safe = scan_file(PROJECT_ROOT / "sql" / "ecommerce" / "safe_expand.sql")
    assert safe.highest_severity == RiskSeverity.SAFE


def test_drop_table_and_truncate_require_manual_recovery() -> None:
    report = scan_sql("DROP TABLE audit_logs;\nTRUNCATE TABLE sessions;")
    rule_ids = {f.rule_id for f in report.findings}
    assert "DROP_TABLE" in rule_ids
    assert "TRUNCATE_TABLE" in rule_ids
    assert report.rollback_classification == RollbackClassification.MANUAL_RECOVERY_REQUIRED


def test_not_null_without_default_flagged_high_risk() -> None:
    report = scan_sql("ALTER TABLE users ADD COLUMN tax_id VARCHAR(32) NOT NULL;")
    assert any(f.rule_id == "ADD_COLUMN_NOT_NULL_NO_DEFAULT" for f in report.findings)
    assert report.highest_severity == RiskSeverity.HIGH_RISK


def test_destructive_delete_without_where_flagged_critical() -> None:
    report = scan_sql("DELETE FROM transactions;")
    assert any(f.rule_id == "DESTRUCTIVE_DELETE_NO_WHERE" for f in report.findings)
    assert report.highest_severity == RiskSeverity.CRITICAL


def test_reviewed_exception_suppresses_finding_until_expiry() -> None:
    ex = ScannerExceptionRule(
        rule_id="DROP_COLUMN",
        reason="V1 retired on 2026-11-01; approved Contract release",
        owner="db-reliability-team",
        expires_at="2099-12-31",
    )
    report = scan_sql("ALTER TABLE orders DROP COLUMN legacy_flag;", exceptions=[ex])
    assert len(report.findings) == 1
    assert report.findings[0].suppressed is True
    assert report.highest_severity == RiskSeverity.SAFE
