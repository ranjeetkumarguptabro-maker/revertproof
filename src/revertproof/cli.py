"""RevertProof Command-Line Interface — Real-World CI/CD & GitHub Safety Gate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from revertproof.config import configure_logging, get_settings
from revertproof.contract_runner import evaluate_project_migration
from revertproof.db import DatabaseAdapter
from revertproof.demo import run_full_demo
from revertproof.gate import evaluate_migration_file
from revertproof.github_integration import GitHubClient, detect_local_git_migrations
from revertproof.models import RiskSeverity
from revertproof.project_config import generate_starter_config, load_project_config
from revertproof.safety_guard import ProductionDatabaseBlockedError, validate_safe_test_database_url
from revertproof.scanner import scan_file


def _cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.output)
    if target.exists() and not args.force:
        print(f"File already exists: {target}. Pass --force to overwrite.")
        return 1
    target.write_text(generate_starter_config(project_name=args.name), encoding="utf-8")
    print(f"Created {target}. Customize contracts & invariants for your repository.")
    return 0


def _cmd_test_connection(args: argparse.Namespace) -> int:
    try:
        validate_safe_test_database_url(args.database_url)
    except ProductionDatabaseBlockedError as exc:
        print(f"BLOCKED: {exc}")
        return 1
    adapter = DatabaseAdapter(args.database_url, schema_name=args.schema)
    try:
        adapter.connect(max_retries=2)
        adapter.close()
        print(f"OK: Connected safely to isolated {adapter.engine_name} test target (schema={args.schema}).")
        return 0
    except Exception as exc:
        print(f"FAILED: {exc}")
        return 2


def _cmd_run(args: argparse.Namespace) -> int:
    """Run config-driven `revertproof.yaml` verification against any repository."""
    cfg_path = Path(args.config).resolve()
    repo_root = Path(args.repo_root).resolve() if args.repo_root else cfg_path.parent
    project_cfg = load_project_config(cfg_path)
    settings = get_settings()
    if args.database_url:
        settings.database_url = args.database_url
    if args.report_dir:
        settings.report_dir = Path(args.report_dir)

    migrations: list[Path] = []
    if args.migration:
        migrations = [Path(args.migration)]
    else:
        detected = detect_local_git_migrations(
            repo_root, base_ref=args.base_ref, migration_dirs=project_cfg.migration_dirs
        )
        migrations = [repo_root / m for m in detected]

    if not migrations:
        print("No SQL migration files detected in diff. Safety gate PASS (exit 0).")
        return 0

    worst_exit = 0
    for mig in migrations:
        report, diff = evaluate_project_migration(
            project_config=project_cfg,
            migration_sql=mig.read_text(encoding="utf-8"),
            migration_name=str(mig),
            repo_root=repo_root,
            settings=settings,
        )
        if args.json:
            print(report.model_dump_json(indent=2))
        else:
            print(f"[{report.decision.value}] {mig} (exit {report.exit_code}) — {report.reason}")
            if diff and diff.column_changes:
                print(f"  Schema diff: {len(diff.column_changes)} column change(s), breaking={diff.has_breaking_changes}")
        worst_exit = max(worst_exit, report.exit_code)
    return worst_exit


def _cmd_github_check(args: argparse.Namespace) -> int:
    """Real GitHub PR gate: detect PR migrations, run checks, publish status + PR comment."""
    cfg_path = Path(args.config).resolve()
    repo_root = Path(args.repo_root).resolve() if args.repo_root else Path.cwd()
    project_cfg = load_project_config(cfg_path)
    gh = GitHubClient()

    pr_files = gh.list_pr_migration_files(
        args.repo, args.pr, migration_dirs=project_cfg.migration_dirs
    )
    if not pr_files:
        print(f"No SQL migrations changed in {args.repo} PR #{args.pr}.")
        return 0

    settings = get_settings()
    if args.database_url:
        settings.database_url = args.database_url

    worst_exit = 0
    for pf in pr_files:
        mig_path = repo_root / pf["filename"]
        if not mig_path.exists():
            print(f"Skipping deleted/missing file: {mig_path}")
            continue
        report, _ = evaluate_project_migration(
            project_config=project_cfg,
            migration_sql=mig_path.read_text(encoding="utf-8"),
            migration_name=pf["filename"],
            repo_root=repo_root,
            settings=settings,
        )
        if args.publish:
            gh.publish_commit_status(args.repo, args.sha, report, target_url=args.target_url)
            gh.publish_pr_comment(args.repo, args.pr, report)
        print(f"PR #{args.pr} | {pf['filename']} -> {report.decision.value} (exit {report.exit_code})")
        worst_exit = max(worst_exit, report.exit_code)
    return worst_exit


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

    p_init = sub.add_parser("init", help="Generate a starter revertproof.yaml in your repository")
    p_init.add_argument("--name", default="my-service")
    p_init.add_argument("--output", default="revertproof.yaml")
    p_init.add_argument("--force", action="store_true")
    p_init.set_defaults(func=_cmd_init)

    p_conn = sub.add_parser("test-connection", help="Validate isolated DB connectivity & production guard")
    p_conn.add_argument("database_url")
    p_conn.add_argument("--schema", default="rp_demo")
    p_conn.set_defaults(func=_cmd_test_connection)

    p_run = sub.add_parser("run", help="Run config-driven verification from revertproof.yaml")
    p_run.add_argument("--config", default="revertproof.yaml")
    p_run.add_argument("--migration", default=None, help="Explicit migration SQL file (else detects via git diff)")
    p_run.add_argument("--base-ref", default="HEAD~1")
    p_run.add_argument("--repo-root", default=None)
    p_run.add_argument("--database-url", default=None)
    p_run.add_argument("--report-dir", default=None)
    p_run.add_argument("--json", action="store_true")
    p_run.set_defaults(func=_cmd_run)

    p_gh = sub.add_parser("github-check", help="Run real GitHub PR migration check & publish status/comment")
    p_gh.add_argument("--repo", required=True, help="owner/repo")
    p_gh.add_argument("--pr", type=int, required=True)
    p_gh.add_argument("--sha", required=True)
    p_gh.add_argument("--config", default="revertproof.yaml")
    p_gh.add_argument("--repo-root", default=None)
    p_gh.add_argument("--database-url", default=None)
    p_gh.add_argument("--target-url", default=None)
    p_gh.add_argument("--publish", action="store_true", help="Post real GitHub Commit Status and PR comment")
    p_gh.set_defaults(func=_cmd_github_check)

    p_scan = sub.add_parser("scan", help="Run static SQL risk scanner on a migration file")
    p_scan.add_argument("sql_file")
    p_scan.add_argument("--json", action="store_true")
    p_scan.add_argument("--fail-on-risk", action="store_true")
    p_scan.set_defaults(func=_cmd_scan)

    p_verify = sub.add_parser("verify", help="Run built-in banking/ecommerce safety gate on a SQL file")
    p_verify.add_argument("sql_file")
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
