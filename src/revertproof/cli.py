"""RevertProof Command-Line Interface (CI/CD Deployment Safety Gate)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from revertproof.config import configure_logging, get_settings
from revertproof.demo import run_full_demo
from revertproof.gate import evaluate_migration_file
from revertproof.models import RiskSeverity
from revertproof.scanner import scan_file


def _cmd_scan(args: argparse.Namespace) -> int:
    report = scan_file(args.sql_file)
    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        print(f"File: {report.migration_file}")
        print(f"Highest Severity: {report.highest_severity.value}")
        print(f"Rollback Classification: {report.rollback_classification.value}")
        if not report.findings:
            print("No risky SQL patterns flagged.")
        for f in report.findings:
            print(f"  - [{f.severity.value}] {f.rule_id} (line {f.line_number}): {f.explanation}")
        print(f"\nNote: {report.disclaimer}")
    if args.fail_on_risk and report.highest_severity in (
        RiskSeverity.HIGH_RISK,
        RiskSeverity.CRITICAL,
    ):
        return 1
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    settings = get_settings()
    if args.database_url:
        settings.database_url = args.database_url
    if args.report_dir:
        settings.report_dir = Path(args.report_dir)

    report = evaluate_migration_file(
        migration_path=args.sql_file,
        domain=args.domain,
        scenario=args.scenario,
        fixture_count=args.fixtures,
        settings=settings,
    )
    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        print(f"Run ID: {report.run_id}")
        print(f"Engine: {report.engine} | Domain: {report.domain} | Fixtures: {report.fixture_count}")
        print(f"Decision: {report.decision.value} (exit code {report.exit_code})")
        print(f"Rollback Classification: {report.rollback_classification.value}")
        print(f"Checks: {json.dumps(report.checks)}")
        print(f"Reason: {report.reason}")
    return report.exit_code


def _cmd_demo(args: argparse.Namespace) -> int:
    settings = get_settings()
    if args.database_url:
        settings.database_url = args.database_url
    results = run_full_demo(
        fixture_count=args.fixtures,
        settings=settings,
        include_ecommerce=not args.banking_only,
    )
    # Demo succeeds if Scenario A correctly blocked (exit 1) and Scenarios B & C passed (exit 0)
    ok = (
        results["scenario_a_unsafe"].exit_code == 1
        and results["scenario_b_safe"].exit_code == 0
        and results["scenario_c_rollback"].exit_code == 0
    )
    return 0 if ok else 1


def _cmd_dashboard(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("revertproof.dashboard:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="revertproof",
        description="RevertProof — Database-Aware Deployment Rollback Verification Platform",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="Run static SQL risk scanner on a migration file")
    p_scan.add_argument("sql_file", help="Path to SQL migration file")
    p_scan.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_scan.add_argument(
        "--fail-on-risk", action="store_true", help="Exit 1 on HIGH_RISK or CRITICAL findings"
    )
    p_scan.set_defaults(func=_cmd_scan)

    p_verify = sub.add_parser("verify", help="Run full deployment safety gate on a migration file")
    p_verify.add_argument("sql_file", help="Path to SQL migration file")
    p_verify.add_argument("--domain", choices=["banking", "ecommerce"], default="banking")
    p_verify.add_argument("--scenario", default="ci_safety_gate")
    p_verify.add_argument("--fixtures", type=int, default=1000)
    p_verify.add_argument("--database-url", default=None)
    p_verify.add_argument("--report-dir", default=None)
    p_verify.add_argument("--json", action="store_true")
    p_verify.set_defaults(func=_cmd_verify)

    p_demo = sub.add_parser("demo", help="Run end-to-end 1,000-transaction demonstration")
    p_demo.add_argument("--fixtures", type=int, default=1000)
    p_demo.add_argument("--database-url", default=None)
    p_demo.add_argument("--banking-only", action="store_true")
    p_demo.set_defaults(func=_cmd_demo)

    p_dash = sub.add_parser("dashboard", help="Launch live RevertProof web dashboard")
    p_dash.add_argument("--host", default="127.0.0.1")
    p_dash.add_argument("--port", type=int, default=8000)
    p_dash.add_argument("--reload", action="store_true")
    p_dash.set_defaults(func=_cmd_dashboard)

    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
