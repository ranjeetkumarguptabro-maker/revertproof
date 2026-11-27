"""Typed domain models for RevertProof verification runs and reports."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class RiskSeverity(StrEnum):
    SAFE = "SAFE"
    WARNING = "WARNING"
    HIGH_RISK = "HIGH_RISK"
    CRITICAL = "CRITICAL"


class RollbackClassification(StrEnum):
    SAFE_ROLLBACK = "SAFE_ROLLBACK"
    CONDITIONAL_ROLLBACK = "CONDITIONAL_ROLLBACK"
    ROLL_FORWARD_ONLY = "ROLL_FORWARD_ONLY"
    MANUAL_RECOVERY_REQUIRED = "MANUAL_RECOVERY_REQUIRED"


class GateDecision(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    CONDITIONAL = "CONDITIONAL"


class CheckStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARNING = "WARNING"
    BLOCKED = "BLOCKED"
    NOT_RUN = "NOT_RUN"


class ScannerExceptionRule(BaseModel):
    """Audited exception for a scanner finding (never an unexplained permanent bypass)."""

    rule_id: str
    reason: str
    owner: str
    expires_at: str  # ISO-8601 date YYYY-MM-DD


class ScanFinding(BaseModel):
    """Single static SQL risk finding."""

    rule_id: str
    severity: RiskSeverity
    line_number: int
    statement: str
    explanation: str
    affected_versions: list[str] = Field(default_factory=lambda: ["V1 (previous application)"])
    recommendation: str
    suppressed: bool = False
    suppression_note: str | None = None


class ScanReport(BaseModel):
    """Output of Module 1: Migration Scanner."""

    migration_file: str
    scanned_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    highest_severity: RiskSeverity = RiskSeverity.SAFE
    rollback_classification: RollbackClassification = RollbackClassification.SAFE_ROLLBACK
    findings: list[ScanFinding] = Field(default_factory=list)
    disclaimer: str = (
        "Static SQL scanning is a heuristic early-warning layer and cannot prove runtime "
        "compatibility alone. Always combine with execution-based V1/V2 compatibility and "
        "data integrity tests against an isolated database."
    )


class OperationResult(BaseModel):
    """Outcome of a single read or write contract check."""

    name: str
    operation_type: str  # READ, INSERT, UPDATE, DUAL_WRITE_CONFLICT
    passed: bool
    duration_ms: float = 0.0
    rows_affected: int = 0
    error: str | None = None
    details: str | None = None


class VersionCompatibilityResult(BaseModel):
    """Outcome of testing one application version (V1 or V2) against migrated DB."""

    app_version: str
    status: CheckStatus
    operations: list[OperationResult] = Field(default_factory=list)
    summary: str = ""


class CompatibilityReport(BaseModel):
    """Output of Module 2: Database Compatibility Tester."""

    domain: str = "banking"
    engine: str = "sqlite"
    schema_name: str = "rp_demo"
    fixture_count: int = 0
    migration_applied: bool = False
    migration_error: str | None = None
    v2_result: VersionCompatibilityResult
    v1_result: VersionCompatibilityResult
    dual_write_verified: bool = False
    rollback_verified: bool = False


class IntegrityMismatch(BaseModel):
    """Single data-invariant violation detected during reconciliation."""

    record_id: str
    invariant: str  # IDENTITY, UNIQUENESS, AMOUNT, CURRENCY, COMPLETENESS, TIMESTAMP, FIELD_PARITY
    expected: str
    actual: str
    message: str


class IntegrityReport(BaseModel):
    """Output of Module 3: Transaction / Data Integrity Checker."""

    status: CheckStatus
    domain: str = "banking"
    total_expected: int = 0
    total_actual: int = 0
    records_preserved: int = 0
    missing_ids: list[str] = Field(default_factory=list)
    duplicate_ids: list[str] = Field(default_factory=list)
    null_violations: int = 0
    amount_mismatches: int = 0
    currency_mismatches: int = 0
    timestamp_mismatches: int = 0
    field_mismatches: int = 0
    mismatches: list[IntegrityMismatch] = Field(default_factory=list)
    summary: str = ""


class ExpandContractStep(BaseModel):
    """Actionable Expand-and-Contract guidance stage."""

    stage: str
    title: str
    description: str
    sql_example: str


class MigrationGuidance(BaseModel):
    """Output of Module 4: Safe Migration Engine guidance."""

    rollback_classification: RollbackClassification
    rationale: str
    stages: list[ExpandContractStep] = Field(default_factory=list)


class EvidenceReport(BaseModel):
    """Master machine-readable evidence report produced by Module 5: Deployment Safety Gate."""

    run_id: str
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    commit_sha: str = "local"
    domain: str = "banking"
    scenario: str = "custom"
    engine: str = "sqlite"
    migration_file: str
    fixture_count: int = 0
    decision: GateDecision
    exit_code: int
    rollback_classification: RollbackClassification
    reason: str
    checks: dict[str, str] = Field(default_factory=dict)
    scan_report: ScanReport
    compatibility_report: CompatibilityReport | None = None
    integrity_report: IntegrityReport | None = None
    guidance: MigrationGuidance | None = None
    aws_artifacts: dict[str, Any] = Field(default_factory=dict)
