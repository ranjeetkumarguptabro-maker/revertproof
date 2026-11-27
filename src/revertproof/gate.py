"""Module 5: Deployment Safety Gate.

Orchestrates:
  1. Module 1 — Static Migration Scanner
  2. Module 2 — Isolated Database Compatibility Tester (V2 + V1 reads & writes)
  3. Module 3 — Exact Transaction / Data Integrity Checker
  4. Module 4 — Expand-and-Contract Guidance & Rollback Classification
  5. Evidence Report persistence (local JSON/HTML + optional S3 & CloudWatch)

Exit codes:
  0 -> PASS (deployment may proceed)
  1 -> FAIL / BLOCKED (critical compatibility or integrity failure)
  2 -> CONDITIONAL / BLOCKED (test DB unavailable or incomplete evidence; never silently pass)
"""

from __future__ import annotations

import logging
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

from revertproof.aws_integration import (
    load_database_url_from_secrets_manager,
    publish_cloudwatch_metrics,
    publish_evidence_to_s3,
)
from revertproof.compatibility import (
    run_banking_compatibility,
    run_ecommerce_compatibility,
)
from revertproof.config import Settings, get_settings
from revertproof.db import DatabaseUnavailableError, isolated_database
from revertproof.integrity import check_banking_integrity, check_ecommerce_integrity
from revertproof.migration_engine import build_expand_contract_guidance
from revertproof.models import (
    CheckStatus,
    EvidenceReport,
    GateDecision,
    RiskSeverity,
    RollbackClassification,
    ScannerExceptionRule,
)
from revertproof.reporting import save_evidence_report
from revertproof.scanner import scan_sql

logger = logging.getLogger(__name__)


def _current_git_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL, text=True
        ).strip()
        return out or "local"
    except Exception:
        return "local"


def _make_run_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    suffix = uuid.uuid4().hex[:6]
    return f"rp-{stamp}-{suffix}"


def evaluate_migration(
    migration_sql: str,
    migration_name: str = "<inline>",
    domain: str = "banking",
    scenario: str = "verification",
    fixture_count: int = 1000,
    settings: Settings | None = None,
    exceptions: list[ScannerExceptionRule] | None = None,
    save_report: bool = True,
) -> EvidenceReport:
    """Run the full RevertProof verification gate and return an EvidenceReport."""
    cfg = settings or get_settings()
    run_id = _make_run_id()
    commit_sha = _current_git_sha()

    # Resolve DB URL (prefer Secrets Manager if configured)
    db_url = cfg.database_url
    try:
        sm_url = load_database_url_from_secrets_manager(cfg)
        if sm_url:
            db_url = sm_url
    except Exception as exc:
        logger.error("Failed to load DB credentials from Secrets Manager: %s", exc)
        scan_report = scan_sql(migration_sql, migration_file=migration_name, exceptions=exceptions)
        guidance = build_expand_contract_guidance(scan_report, None, None, domain=domain)
        report = EvidenceReport(
            run_id=run_id,
            commit_sha=commit_sha,
            domain=domain,
            scenario=scenario,
            engine="unavailable",
            migration_file=migration_name,
            fixture_count=fixture_count,
            decision=GateDecision.CONDITIONAL,
            exit_code=2,
            rollback_classification=RollbackClassification.CONDITIONAL_ROLLBACK,
            reason=f"Blocked: could not retrieve test DB credentials ({exc})",
            checks={
                "scanner": scan_report.highest_severity.value,
                "v2_compatibility": CheckStatus.NOT_RUN.value,
                "v1_rollback_compatibility": CheckStatus.NOT_RUN.value,
                "transaction_integrity": CheckStatus.NOT_RUN.value,
            },
            scan_report=scan_report,
            guidance=guidance,
        )
        if save_report:
            save_evidence_report(report, cfg.report_dir)
        return report

    # Module 1: Static Migration Scanner
    scan_report = scan_sql(migration_sql, migration_file=migration_name, exceptions=exceptions)

    # Modules 2 & 3: Isolated DB Compatibility + Integrity
    try:
        with isolated_database(db_url, schema_name=cfg.db_schema) as db:
            if domain == "ecommerce":
                compat_report, fixtures = run_ecommerce_compatibility(
                    db, migration_sql, fixture_count=fixture_count
                )
                integrity_report = (
                    check_ecommerce_integrity(db, fixtures)
                    if compat_report.migration_applied
                    else None
                )
            else:
                compat_report, fixtures = run_banking_compatibility(
                    db, migration_sql, fixture_count=fixture_count
                )
                integrity_report = (
                    check_banking_integrity(db, fixtures)
                    if compat_report.migration_applied
                    else None
                )
            engine_name = db.engine_name
    except DatabaseUnavailableError as exc:
        guidance = build_expand_contract_guidance(scan_report, None, None, domain=domain)
        report = EvidenceReport(
            run_id=run_id,
            commit_sha=commit_sha,
            domain=domain,
            scenario=scenario,
            engine="unavailable",
            migration_file=migration_name,
            fixture_count=fixture_count,
            decision=GateDecision.CONDITIONAL,
            exit_code=2,
            rollback_classification=RollbackClassification.CONDITIONAL_ROLLBACK,
            reason=f"Test database unavailable — never silently passing: {exc}",
            checks={
                "scanner": scan_report.highest_severity.value,
                "v2_compatibility": CheckStatus.NOT_RUN.value,
                "v1_rollback_compatibility": CheckStatus.NOT_RUN.value,
                "transaction_integrity": CheckStatus.NOT_RUN.value,
            },
            scan_report=scan_report,
            guidance=guidance,
        )
        if save_report:
            save_evidence_report(report, cfg.report_dir)
        return report

    # Module 4: Guidance & Rollback Classification
    guidance = build_expand_contract_guidance(
        scan_report, compat_report, integrity_report, domain=domain
    )

    # Module 5: Gate Decision Logic
    if not compat_report.migration_applied:
        decision = GateDecision.FAIL
        exit_code = 1
        reason = f"Migration failed to apply: {compat_report.migration_error}"
    elif compat_report.v2_result.status != CheckStatus.PASS:
        decision = GateDecision.FAIL
        exit_code = 1
        reason = compat_report.v2_result.summary
    elif compat_report.v1_result.status != CheckStatus.PASS:
        decision = GateDecision.FAIL
        exit_code = 1
        reason = (
            f"UNSAFE ROLLBACK BLOCKED: Application V2 works, but rolling back to V1 FAILS — "
            f"{compat_report.v1_result.summary}"
        )
    elif integrity_report is None or integrity_report.status == CheckStatus.NOT_RUN:
        decision = GateDecision.CONDITIONAL
        exit_code = 2
        reason = "Integrity check could not complete; manual review required."
    elif integrity_report.status != CheckStatus.PASS:
        decision = GateDecision.FAIL
        exit_code = 1
        reason = f"DATA INTEGRITY BLOCKED: {integrity_report.summary}"
    elif scan_report.highest_severity in (RiskSeverity.HIGH_RISK, RiskSeverity.CRITICAL):
        decision = GateDecision.CONDITIONAL
        exit_code = 2
        reason = (
            "Runtime tests passed, but static scanner flagged HIGH_RISK/CRITICAL SQL patterns. "
            "Requires explicit reviewed exception."
        )
    else:
        decision = GateDecision.PASS
        exit_code = 0
        reason = (
            f"ROLLBACK COMPATIBILITY VERIFIED: V1 & V2 contracts passed and "
            f"{integrity_report.records_preserved}/{integrity_report.total_expected} "
            f"synthetic records preserved with exact parity."
        )

    checks = {
        "scanner": (
            "CLEAN"
            if scan_report.highest_severity == RiskSeverity.SAFE
            else f"{scan_report.highest_severity.value}_FINDING"
        ),
        "v2_compatibility": compat_report.v2_result.status.value,
        "v1_rollback_compatibility": compat_report.v1_result.status.value,
        "dual_write_sync": "PASS" if compat_report.dual_write_verified else "NOT_VERIFIED",
        "transaction_integrity": (
            integrity_report.status.value if integrity_report else CheckStatus.NOT_RUN.value
        ),
    }

    report = EvidenceReport(
        run_id=run_id,
        commit_sha=commit_sha,
        domain=domain,
        scenario=scenario,
        engine=engine_name,
        migration_file=migration_name,
        fixture_count=fixture_count,
        decision=decision,
        exit_code=exit_code,
        rollback_classification=guidance.rollback_classification,
        reason=reason,
        checks=checks,
        scan_report=scan_report,
        compatibility_report=compat_report,
        integrity_report=integrity_report,
        guidance=guidance,
    )

    if save_report:
        json_path, html_path = save_evidence_report(report, cfg.report_dir)
        s3_info = publish_evidence_to_s3(report, json_path, html_path, cfg)
        cw_info = publish_cloudwatch_metrics(report, cfg)
        report.aws_artifacts = {
            "local_json": str(json_path),
            "local_html": str(html_path),
            **s3_info,
            **cw_info,
        }
        # Update JSON with aws_artifacts metadata
        json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    return report


def evaluate_migration_file(
    migration_path: str | Path,
    domain: str = "banking",
    scenario: str = "file_verification",
    fixture_count: int = 1000,
    settings: Settings | None = None,
    exceptions: list[ScannerExceptionRule] | None = None,
    save_report: bool = True,
) -> EvidenceReport:
    """Read a SQL migration file and evaluate it through the Deployment Safety Gate."""
    path = Path(migration_path)
    sql = path.read_text(encoding="utf-8")
    return evaluate_migration(
        migration_sql=sql,
        migration_name=str(path),
        domain=domain,
        scenario=scenario,
        fixture_count=fixture_count,
        settings=settings,
        exceptions=exceptions,
        save_report=save_report,
    )


def run_rollback_simulation(
    migration_path: str | Path,
    fixture_count: int = 1000,
    settings: Settings | None = None,
    save_report: bool = True,
) -> EvidenceReport:
    """Scenario C: Simulate V2 health failure -> roll back to V1 -> prove V1 reads/writes & integrity."""
    return evaluate_migration_file(
        migration_path=migration_path,
        domain="banking",
        scenario="scenario_c_rollback_recovery",
        fixture_count=fixture_count,
        settings=settings,
        save_report=save_report,
    )
