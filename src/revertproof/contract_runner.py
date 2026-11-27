"""Generic Config-Driven Contract Runner & Data Integrity Engine.

Works for ANY repository that supplies a `revertproof.yaml` — no hardcoded banking
or e-commerce schema assumptions. Supports:
- Live Schema Diff (before vs after migration)
- Parameterized SQL contracts for V2 and V1
- Real external test commands (pytest, Go/Node tests, Docker containers) with RP_DATABASE_URL injected
- Generic before/after table row reconciliation (PK preservation, duplicates, NULLs, exact minor-unit parity, column parity)
"""

from __future__ import annotations

import os
import subprocess
import time
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from revertproof.config import Settings, get_settings
from revertproof.db import DatabaseAdapter, DatabaseUnavailableError, isolated_database
from revertproof.migration_engine import build_expand_contract_guidance
from revertproof.models import (
    CheckStatus,
    CompatibilityReport,
    EvidenceReport,
    GateDecision,
    IntegrityMismatch,
    IntegrityReport,
    OperationResult,
    RiskSeverity,
    RollbackClassification,
    VersionCompatibilityResult,
)
from revertproof.project_config import (
    CommandContractCheck,
    RevertProofProjectConfig,
    SqlContractCheck,
    TableInvariantConfig,
    VersionContractConfig,
)
from revertproof.reporting import save_evidence_report
from revertproof.scanner import scan_sql
from revertproof.schema_inspector import SchemaDiff, capture_schema_snapshot, diff_schemas


def _run_sql_check(db: DatabaseAdapter, check: SqlContractCheck) -> OperationResult:
    start = time.perf_counter()
    try:
        params = tuple(check.params)
        if check.operation_type == "READ":
            rows = db.fetchall(check.sql, params)
            count = len(rows)
            if check.expect_error:
                raise AssertionError("Expected SQL error but query succeeded")
            if check.expect_min_rows is not None and count < check.expect_min_rows:
                raise AssertionError(f"Expected >= {check.expect_min_rows} rows, got {count}")
            if check.expect_exact_rows is not None and count != check.expect_exact_rows:
                raise AssertionError(f"Expected exactly {check.expect_exact_rows} rows, got {count}")
            details = check.description or f"Returned {count} row(s)"
        else:
            count = db.execute(check.sql, params)
            if check.expect_error:
                raise AssertionError("Expected SQL error but write succeeded")
            details = check.description or f"Affected {count} row(s)"
        return OperationResult(
            name=check.name,
            operation_type=check.operation_type,
            passed=True,
            duration_ms=round((time.perf_counter() - start) * 1000.0, 2),
            rows_affected=count,
            details=details,
        )
    except Exception as exc:
        elapsed = round((time.perf_counter() - start) * 1000.0, 2)
        if check.expect_error:
            return OperationResult(
                name=check.name,
                operation_type=check.operation_type,
                passed=True,
                duration_ms=elapsed,
                details=f"Expected rejection verified: {exc}",
            )
        return OperationResult(
            name=check.name,
            operation_type=check.operation_type,
            passed=False,
            duration_ms=elapsed,
            error=f"{type(exc).__name__}: {exc}",
        )


def _run_command_check(
    check: CommandContractCheck,
    database_url: str,
    schema_name: str,
    repo_root: Path,
) -> OperationResult:
    """Execute a real external test command (pytest, npm test, docker run, etc.)."""
    start = time.perf_counter()
    env = os.environ.copy()
    env["RP_DATABASE_URL"] = database_url
    env["RP_DB_SCHEMA"] = schema_name
    cwd = (repo_root / check.working_dir) if check.working_dir else repo_root
    try:
        proc = subprocess.run(
            check.command,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=check.timeout_seconds,
            check=False,
        )
        elapsed = round((time.perf_counter() - start) * 1000.0, 2)
        if proc.returncode == 0:
            return OperationResult(
                name=check.name,
                operation_type="COMMAND",
                passed=True,
                duration_ms=elapsed,
                details=(proc.stdout.strip()[:240] or "Command exited 0"),
            )
        err_out = (proc.stderr or proc.stdout).strip()[:300]
        return OperationResult(
            name=check.name,
            operation_type="COMMAND",
            passed=False,
            duration_ms=elapsed,
            error=f"Exit code {proc.returncode}: {err_out}",
        )
    except Exception as exc:
        return OperationResult(
            name=check.name,
            operation_type="COMMAND",
            passed=False,
            duration_ms=round((time.perf_counter() - start) * 1000.0, 2),
            error=f"{type(exc).__name__}: {exc}",
        )


def _run_version_contracts(
    db: DatabaseAdapter,
    cfg: VersionContractConfig,
    repo_root: Path,
) -> VersionCompatibilityResult:
    ops: list[OperationResult] = []
    for sql_check in cfg.sql_checks:
        ops.append(_run_sql_check(db, sql_check))
    for cmd_check in cfg.command_checks:
        ops.append(_run_command_check(cmd_check, db.database_url, db.schema_name, repo_root))

    if not ops:
        return VersionCompatibilityResult(
            app_version=cfg.version_label,
            status=CheckStatus.PASS,
            operations=[],
            summary=f"No explicit {cfg.version_label} checks configured",
        )
    passed = all(op.passed for op in ops)
    return VersionCompatibilityResult(
        app_version=cfg.version_label,
        status=CheckStatus.PASS if passed else CheckStatus.FAIL,
        operations=ops,
        summary=(
            f"{cfg.version_label}: all {len(ops)} contract check(s) passed"
            if passed
            else f"{cfg.version_label} FAILED: "
            + "; ".join(op.error or op.name for op in ops if not op.passed)
        ),
    )


def _snapshot_table_rows(
    db: DatabaseAdapter, inv: TableInvariantConfig
) -> list[dict[str, Any]]:
    tbl = DatabaseAdapter._validate_identifier(inv.table)
    pk = DatabaseAdapter._validate_identifier(inv.primary_key)
    return db.fetchall(f'SELECT * FROM "{tbl}" ORDER BY "{pk}";')


def _reconcile_generic_invariants(
    db: DatabaseAdapter,
    invariants: list[TableInvariantConfig],
    before_snapshots: dict[str, list[dict[str, Any]]],
    domain: str,
) -> IntegrityReport:
    if not invariants:
        return IntegrityReport(
            status=CheckStatus.PASS,
            domain=domain,
            summary="No table invariants configured.",
        )

    mismatches: list[IntegrityMismatch] = []
    total_expected = 0
    total_actual = 0
    records_preserved = 0
    missing_ids: list[str] = []
    duplicate_ids: list[str] = []
    null_violations = 0
    amount_mismatches = 0
    field_mismatches = 0

    for inv in invariants:
        tbl = DatabaseAdapter._validate_identifier(inv.table)
        pk = DatabaseAdapter._validate_identifier(inv.primary_key)

        # Clean up probe rows inserted during contract tests
        for probe_id in inv.cleanup_probe_ids:
            try:
                db.execute(f'DELETE FROM "{tbl}" WHERE "{pk}" = %s;', (probe_id,))
            except Exception:
                pass

        try:
            after_rows = db.fetchall(f'SELECT * FROM "{tbl}" ORDER BY "{pk}";')
        except Exception as exc:
            return IntegrityReport(
                status=CheckStatus.FAIL,
                domain=domain,
                summary=f"Table '{tbl}' unreadable after migration: {exc}",
            )

        before_rows = before_snapshots.get(inv.table, [])
        total_expected += len(before_rows)
        total_actual += len(after_rows)

        after_id_counts = Counter(str(r.get(pk)) for r in after_rows)
        for dup_id, cnt in after_id_counts.items():
            if cnt > 1:
                duplicate_ids.append(f"{tbl}:{dup_id}")
                mismatches.append(
                    IntegrityMismatch(
                        record_id=dup_id,
                        invariant="UNIQUENESS",
                        expected="1",
                        actual=str(cnt),
                        message=f"Duplicate PK {dup_id} in {tbl}",
                    )
                )

        after_map = {str(r.get(pk)): r for r in after_rows}
        for b_row in before_rows:
            rid = str(b_row.get(pk))
            a_row = after_map.get(rid)
            if a_row is None:
                if inv.preserve_primary_keys:
                    missing_ids.append(f"{tbl}:{rid}")
                    mismatches.append(
                        IntegrityMismatch(
                            record_id=rid,
                            invariant="IDENTITY",
                            expected="present",
                            actual="missing",
                            message=f"Record {rid} missing from {tbl}",
                        )
                    )
                continue

            row_ok = True
            for col in inv.required_not_null:
                if col not in a_row or a_row[col] is None or str(a_row[col]).strip() == "":
                    null_violations += 1
                    row_ok = False
                    mismatches.append(
                        IntegrityMismatch(
                            record_id=rid,
                            invariant="COMPLETENESS",
                            expected=f"{col} NOT NULL",
                            actual="NULL",
                            message=f"{tbl}.{col} is NULL for {rid}",
                        )
                    )

            for rule in inv.numeric_parity:
                if rule.legacy_column in a_row and rule.new_column in a_row:
                    leg = a_row[rule.legacy_column]
                    new = a_row[rule.new_column]
                    if leg is not None and new is not None:
                        expected_minor = int(
                            (Decimal(str(leg).strip()) * Decimal(rule.multiplier)).to_integral_exact()
                        )
                        if int(new) != expected_minor:
                            amount_mismatches += 1
                            row_ok = False
                            mismatches.append(
                                IntegrityMismatch(
                                    record_id=rid,
                                    invariant="AMOUNT",
                                    expected=str(expected_minor),
                                    actual=str(new),
                                    message=(
                                        f"{tbl} {rule.legacy_column}*{rule.multiplier} != "
                                        f"{rule.new_column} for {rid}"
                                    ),
                                )
                            )

            for rule in inv.column_parity:
                if rule.legacy_column in a_row and rule.new_column in a_row:
                    v1 = str(a_row[rule.legacy_column]).strip()
                    v2 = str(a_row[rule.new_column]).strip()
                    if v1 != v2:
                        field_mismatches += 1
                        row_ok = False
                        mismatches.append(
                            IntegrityMismatch(
                                record_id=rid,
                                invariant="FIELD_PARITY",
                                expected=v1,
                                actual=v2,
                                message=f"{tbl} {rule.legacy_column} != {rule.new_column} for {rid}",
                            )
                        )

            if row_ok:
                records_preserved += 1

    passed = len(mismatches) == 0
    return IntegrityReport(
        status=CheckStatus.PASS if passed else CheckStatus.FAIL,
        domain=domain,
        total_expected=total_expected,
        total_actual=total_actual,
        records_preserved=records_preserved,
        missing_ids=missing_ids,
        duplicate_ids=duplicate_ids,
        null_violations=null_violations,
        amount_mismatches=amount_mismatches,
        field_mismatches=field_mismatches,
        mismatches=mismatches[:100],
        summary=(
            f"All {records_preserved}/{total_expected} records preserved across "
            f"{len(invariants)} table(s)."
            if passed
            else f"Generic integrity FAILED with {len(mismatches)} violation(s)."
        ),
    )


def evaluate_project_migration(
    project_config: RevertProofProjectConfig,
    migration_sql: str,
    migration_name: str,
    repo_root: Path,
    settings: Settings | None = None,
    save_report: bool = True,
) -> tuple[EvidenceReport, SchemaDiff | None]:
    """Run the full real-world RevertProof verification using any team's `revertproof.yaml`."""
    from revertproof.gate import _current_git_sha, _make_run_id

    cfg = settings or get_settings()
    run_id = _make_run_id()
    commit_sha = _current_git_sha()
    domain = project_config.project_name

    scan_report = scan_sql(
        migration_sql,
        migration_file=migration_name,
        exceptions=project_config.policy.scanner_exceptions,
    )

    schema_diff: SchemaDiff | None = None
    try:
        with isolated_database(cfg.database_url, schema_name=cfg.db_schema) as db:
            # 1. Load baseline schema + optional seed data
            baseline_path = repo_root / project_config.baseline_sql
            db.execute_script(baseline_path.read_text(encoding="utf-8"))
            if project_config.seed_sql:
                seed_path = repo_root / project_config.seed_sql
                db.execute_script(seed_path.read_text(encoding="utf-8"))

            before_schema = capture_schema_snapshot(db)
            before_rows = {
                inv.table: _snapshot_table_rows(db, inv) for inv in project_config.invariants
            }

            # 2. Apply candidate migration
            migration_applied = True
            migration_error = None
            try:
                db.execute_script(migration_sql)
            except Exception as exc:
                migration_applied = False
                migration_error = f"{type(exc).__name__}: {exc}"

            if migration_applied:
                after_schema = capture_schema_snapshot(db)
                schema_diff = diff_schemas(before_schema, after_schema)
                v2_res = _run_version_contracts(db, project_config.v2_contracts, repo_root)
                v1_res = _run_version_contracts(db, project_config.v1_contracts, repo_root)
                integrity_report = _reconcile_generic_invariants(
                    db, project_config.invariants, before_rows, domain=domain
                )
            else:
                v2_res = VersionCompatibilityResult(
                    app_version=project_config.v2_contracts.version_label,
                    status=CheckStatus.NOT_RUN,
                )
                v1_res = VersionCompatibilityResult(
                    app_version=project_config.v1_contracts.version_label,
                    status=CheckStatus.NOT_RUN,
                )
                integrity_report = None

            total_fixtures = sum(len(rows) for rows in before_rows.values())
            compat_report = CompatibilityReport(
                domain=domain,
                engine=db.engine_name,
                schema_name=db.schema_name,
                fixture_count=total_fixtures,
                migration_applied=migration_applied,
                migration_error=migration_error,
                v2_result=v2_res,
                v1_result=v1_res,
                dual_write_verified=(
                    v1_res.status == CheckStatus.PASS
                    and v2_res.status == CheckStatus.PASS
                    and (integrity_report is not None and integrity_report.status == CheckStatus.PASS)
                ),
                rollback_verified=(v1_res.status == CheckStatus.PASS),
            )
            engine_name = db.engine_name
            db.cleanup_isolated_schema()
    except DatabaseUnavailableError as exc:
        guidance = build_expand_contract_guidance(scan_report, None, None, domain=domain)
        report = EvidenceReport(
            run_id=run_id,
            commit_sha=commit_sha,
            domain=domain,
            scenario="project_config_verification",
            engine="unavailable",
            migration_file=migration_name,
            fixture_count=0,
            decision=GateDecision.CONDITIONAL,
            exit_code=2,
            rollback_classification=RollbackClassification.CONDITIONAL_ROLLBACK,
            reason=f"Database unavailable: {exc}",
            checks={"scanner": scan_report.highest_severity.value},
            scan_report=scan_report,
            guidance=guidance,
        )
        if save_report:
            save_evidence_report(report, cfg.report_dir)
        return report, None

    guidance = build_expand_contract_guidance(scan_report, compat_report, integrity_report, domain)
    pol = project_config.policy

    if not compat_report.migration_applied:
        decision, exit_code = GateDecision.FAIL, 1
        reason = f"Migration failed to apply: {compat_report.migration_error}"
    elif pol.block_on_v2_failure and compat_report.v2_result.status != CheckStatus.PASS:
        decision, exit_code = GateDecision.FAIL, 1
        reason = compat_report.v2_result.summary
    elif pol.block_on_v1_failure and compat_report.v1_result.status != CheckStatus.PASS:
        decision, exit_code = GateDecision.FAIL, 1
        reason = f"UNSAFE ROLLBACK BLOCKED: {compat_report.v1_result.summary}"
    elif pol.block_on_integrity_failure and (
        integrity_report is None or integrity_report.status != CheckStatus.PASS
    ):
        decision, exit_code = GateDecision.FAIL, 1
        reason = f"DATA INTEGRITY BLOCKED: {integrity_report.summary if integrity_report else 'not run'}"
    elif pol.block_on_critical_scan and scan_report.highest_severity == RiskSeverity.CRITICAL:
        decision, exit_code = GateDecision.CONDITIONAL, 2
        reason = "Critical static SQL finding requires reviewed exception."
    else:
        decision, exit_code = GateDecision.PASS, 0
        reason = f"VERIFIED: {project_config.project_name} passed V2, V1 rollback, and data integrity checks."

    checks = {
        "scanner": scan_report.highest_severity.value,
        "v2_compatibility": compat_report.v2_result.status.value,
        "v1_rollback_compatibility": compat_report.v1_result.status.value,
        "transaction_integrity": (
            integrity_report.status.value if integrity_report else CheckStatus.NOT_RUN.value
        ),
        "schema_breaking_diff": (
            "BREAKING" if (schema_diff and schema_diff.has_breaking_changes) else "BACKWARD_COMPATIBLE"
        ),
    }

    report = EvidenceReport(
        run_id=run_id,
        commit_sha=commit_sha,
        domain=domain,
        scenario="project_config_verification",
        engine=engine_name,
        migration_file=migration_name,
        fixture_count=compat_report.fixture_count,
        decision=decision,
        exit_code=exit_code,
        rollback_classification=guidance.rollback_classification,
        reason=reason,
        checks=checks,
        scan_report=scan_report,
        compatibility_report=compat_report,
        integrity_report=integrity_report,
        guidance=guidance,
        aws_artifacts={
            "schema_diff": schema_diff.model_dump() if schema_diff else None,
        },
    )
    if save_report:
        json_path, html_path = save_evidence_report(report, cfg.report_dir)
        report.aws_artifacts["local_json"] = str(json_path)
        report.aws_artifacts["local_html"] = str(html_path)
        json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report, schema_diff
