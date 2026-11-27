"""RevertProof Backend API & Dashboard Server.

Production-structured FastAPI backend with:
- Authentication (PBKDF2 + HMAC-SHA256 bearer tokens + CI API key) and RBAC
- Connected GitHub repositories + real GitHub API verification & webhook receiver
- Database connection profiles + Production-DB Guard validation
- Config-driven `revertproof.yaml` verification engine + Live Schema Diff
- Append-only Audit Logging
- Path-traversal-safe JSON & HTML evidence reports
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from revertproof.auth import (
    AuthUser,
    create_access_token,
    ensure_bootstrap_admin,
    get_current_user,
    hash_password,
    is_auth_enabled,
    require_role,
    verify_password,
)
from revertproof.config import get_settings
from revertproof.contract_runner import evaluate_project_migration
from revertproof.db import DatabaseAdapter
from revertproof.demo import run_full_demo
from revertproof.fixtures import generate_banking_transactions
from revertproof.gate import evaluate_migration, evaluate_migration_file
from revertproof.github_integration import GitHubClient, verify_github_webhook_signature
from revertproof.models import RiskSeverity
from revertproof.project_config import load_project_config
from revertproof.reporting import load_recent_reports
from revertproof.safety_guard import ProductionDatabaseBlockedError, validate_safe_test_database_url
from revertproof.scanner import scan_sql
from revertproof.store import StateStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"
SAFE_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{3,80}$")

app = FastAPI(
    title="RevertProof — Database-Aware Deployment Rollback Verification Platform",
    version="2.0.0",
    description="Reusable database migration safety gate for GitHub, PostgreSQL, and AWS CI/CD.",
)


def _get_store() -> StateStore:
    cfg = get_settings()
    db_path = Path(os.getenv("RP_STATE_DB", str(cfg.report_dir / "revertproof_state.db")))
    store = StateStore(db_path)
    ensure_bootstrap_admin(store)
    return store


# --- Request Schemas ---
class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=8, max_length=256)
    role: str = Field(default="developer", pattern="^(admin|developer|viewer)$")


class ConnectRepoRequest(BaseModel):
    full_name: str = Field(..., pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    default_branch: str = Field(default="main", max_length=64)
    migration_dirs: list[str] = Field(default_factory=lambda: ["migrations", "sql"])
    config_path: str = Field(default="revertproof.yaml", max_length=200)
    verify_with_github: bool = False


class SaveConnectionRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=64)
    database_url: str = Field(..., min_length=5, max_length=512)
    secret_ref: str = Field(default="", max_length=256)
    schema_name: str = Field(default="rp_demo", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")


class TestConnectionRequest(BaseModel):
    database_url: str = Field(..., min_length=5, max_length=512)
    schema_name: str = Field(default="rp_demo", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")


class ScanRequest(BaseModel):
    sql: str = Field(..., min_length=1)
    migration_name: str = Field(default="custom_migration.sql")


class VerifyRequest(BaseModel):
    sql: str = Field(..., min_length=1)
    migration_name: str = Field(default="custom_migration.sql")
    domain: str = Field(default="banking", pattern="^(banking|ecommerce)$")
    fixture_count: int = Field(default=1000, ge=1, le=5000)


class ProjectVerifyRequest(BaseModel):
    config_path: str = Field(default="examples/banking/revertproof.yaml")
    migration_path: str = Field(default="sql/safe_expand.sql")


class PresetRunRequest(BaseModel):
    preset: str = Field(
        default="banking_unsafe",
        pattern="^(banking_unsafe|banking_safe|banking_rollback|ecommerce_unsafe|ecommerce_safe|full_demo)$",
    )
    fixture_count: int = Field(default=1000, ge=1, le=5000)


# --- Public / UI / Health ---
@app.get("/", response_class=HTMLResponse)
def homepage() -> HTMLResponse:
    """Serve the RevertProof Landing Homepage with sticky 5-card scroll workflow."""
    return HTMLResponse(content=(STATIC_DIR / "home.html").read_text(encoding="utf-8"))


@app.get("/app", response_class=HTMLResponse)
def workspace_app() -> HTMLResponse:
    """Serve the connected RevertProof Product Workspace (connected to GET STARTED)."""
    return HTMLResponse(content=(STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/api/health")
def health() -> dict[str, Any]:
    cfg = get_settings()
    return {
        "status": "ok",
        "version": "2.0.0",
        "engine": "postgresql" if cfg.is_postgres else "sqlite",
        "auth_enabled": is_auth_enabled(),
        "report_dir": str(cfg.report_dir),
    }


# --- Public Sanitized Evidence Downloads (JSON & CSV) ---
def _sanitize_report_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Remove local host filesystem paths so exported evidence is clean and portable."""
    clean = dict(data)
    artifacts = dict(clean.get("aws_artifacts") or {})
    artifacts.pop("local_json", None)
    artifacts.pop("local_html", None)
    clean["aws_artifacts"] = artifacts
    clean["sanitized"] = True
    clean["data_notice"] = "Synthetic test records only — no real customer or production data."
    return clean


@app.get("/api/evidence/before-unsafe.json")
def download_before_unsafe_json() -> JSONResponse:
    """Download sanitized JSON evidence report for BEFORE (Unsafe Banking Migration)."""
    report = evaluate_migration_file(
        PROJECT_ROOT / "sql" / "unsafe_v2.sql",
        domain="banking",
        scenario="before_unsafe_banking_migration",
        fixture_count=1000,
        settings=get_settings(),
        save_report=False,
    )
    return JSONResponse(
        content=_sanitize_report_dict(report.model_dump()),
        headers={"Content-Disposition": 'attachment; filename="revertproof-before-unsafe.json"'},
    )


@app.get("/api/evidence/after-safe.json")
def download_after_safe_json() -> JSONResponse:
    """Download sanitized JSON evidence report for AFTER (Safe Expand-and-Contract)."""
    report = evaluate_migration_file(
        PROJECT_ROOT / "sql" / "safe_expand.sql",
        domain="banking",
        scenario="after_safe_expand_banking_migration",
        fixture_count=1000,
        settings=get_settings(),
        save_report=False,
    )
    return JSONResponse(
        content=_sanitize_report_dict(report.model_dump()),
        headers={"Content-Disposition": 'attachment; filename="revertproof-after-safe.json"'},
    )


@app.get("/api/evidence/summary.csv")
def download_summary_csv() -> PlainTextResponse:
    """Download sanitized CSV summary comparing Before (Unsafe) vs After (Safe) verification runs."""
    cfg = get_settings()
    r_before = evaluate_migration_file(
        PROJECT_ROOT / "sql" / "unsafe_v2.sql",
        domain="banking",
        scenario="before_unsafe_banking",
        fixture_count=1000,
        settings=cfg,
        save_report=False,
    )
    r_after = evaluate_migration_file(
        PROJECT_ROOT / "sql" / "safe_expand.sql",
        domain="banking",
        scenario="after_safe_expand_banking",
        fixture_count=1000,
        settings=cfg,
        save_report=False,
    )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "scenario",
            "domain",
            "engine",
            "fixtures",
            "decision",
            "exit_code",
            "rollback_classification",
            "v1_compatibility",
            "v2_compatibility",
            "dual_write_sync",
            "records_preserved",
            "amount_mismatches",
            "missing_ids",
            "duplicate_ids",
        ]
    )
    for r in (r_before, r_after):
        ir = r.integrity_report
        writer.writerow(
            [
                r.scenario,
                r.domain,
                r.engine,
                r.fixture_count,
                r.decision.value,
                r.exit_code,
                r.rollback_classification.value,
                r.checks.get("v1_rollback_compatibility", "NOT_RUN"),
                r.checks.get("v2_compatibility", "NOT_RUN"),
                r.checks.get("dual_write_sync", "NOT_VERIFIED"),
                ir.records_preserved if ir else 0,
                ir.amount_mismatches if ir else "N/A",
                len(ir.missing_ids) if ir else "N/A",
                len(ir.duplicate_ids) if ir else "N/A",
            ]
        )
    return PlainTextResponse(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="revertproof-verification-summary.csv"'},
    )


@app.get("/api/evidence/synthetic-transactions.csv")
def download_synthetic_transactions_csv() -> PlainTextResponse:
    """Download the sanitized 1,000 synthetic banking transaction dataset as CSV."""
    fixtures = generate_banking_transactions(1000)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["transaction_id", "amount", "currency", "amount_minor", "currency_code", "occurred_at"]
    )
    for tx in fixtures:
        writer.writerow(
            [tx.transaction_id, str(tx.amount), tx.currency, tx.amount_minor, tx.currency, tx.occurred_at]
        )
    return PlainTextResponse(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="revertproof-synthetic-1000-transactions.csv"'
        },
    )


# --- Authentication & User Management ---
@app.post("/api/auth/login")
def login(req: LoginRequest) -> dict[str, Any]:
    store = _get_store()
    user = store.get_user(req.username)
    if not user or not verify_password(req.password, user["password_hash"]):
        store.record_audit(req.username, "anonymous", "LOGIN", "auth", "DENIED", "Invalid credentials")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    token = create_access_token(user["username"], user["role"])
    store.record_audit(user["username"], user["role"], "LOGIN", "auth", "SUCCESS")
    return {"access_token": token, "token_type": "bearer", "username": user["username"], "role": user["role"]}


@app.get("/api/auth/me")
def me(user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    return {"username": user.username, "role": user.role, "auth_enabled": is_auth_enabled()}


@app.get("/api/users")
def list_users(user: AuthUser = Depends(require_role("admin"))) -> list[dict[str, Any]]:
    return _get_store().list_users()


@app.post("/api/users", status_code=201)
def create_user(req: CreateUserRequest, user: AuthUser = Depends(require_role("admin"))) -> dict[str, Any]:
    store = _get_store()
    if store.get_user(req.username):
        raise HTTPException(status_code=409, detail="Username already exists")
    created = store.create_user(req.username, hash_password(req.password), req.role)
    store.record_audit(user.username, user.role, "CREATE_USER", req.username, "SUCCESS", f"role={req.role}")
    return created


# --- Overview, History & Reports ---
@app.get("/api/overview")
def overview(user: AuthUser = Depends(require_role("viewer"))) -> dict[str, Any]:
    cfg = get_settings()
    store = _get_store()
    reports = load_recent_reports(cfg.report_dir, limit=100)
    total_scanned = len(reports)
    risky_detected = sum(
        1
        for r in reports
        if r.scan_report.highest_severity in (RiskSeverity.HIGH_RISK, RiskSeverity.CRITICAL)
    )
    blocked_runs = sum(1 for r in reports if r.decision.value != "PASS")
    passed_runs = sum(1 for r in reports if r.decision.value == "PASS")
    latest = reports[0] if reports else None
    return {
        "total_migrations_scanned": total_scanned,
        "risky_migrations_detected": risky_detected,
        "passed_runs": passed_runs,
        "blocked_runs": blocked_runs,
        "connected_repositories": len(store.list_repositories()),
        "saved_connections": len(store.list_db_connections()),
        "engine": "postgresql" if cfg.is_postgres else "sqlite",
        "latest": latest.model_dump() if latest else None,
    }


@app.get("/api/history")
def history(limit: int = 50, user: AuthUser = Depends(require_role("viewer"))) -> list[dict[str, Any]]:
    cfg = get_settings()
    return [r.model_dump() for r in load_recent_reports(cfg.report_dir, limit=limit)]


@app.get("/api/reports/{run_id}")
def get_report(run_id: str, user: AuthUser = Depends(require_role("viewer"))) -> JSONResponse:
    if not SAFE_RUN_ID_RE.fullmatch(run_id):
        raise HTTPException(status_code=400, detail="Invalid run_id format")
    cfg = get_settings()
    path = cfg.report_dir / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Report {run_id} not found")
    return JSONResponse(content=json.loads(path.read_text(encoding="utf-8")))


@app.get("/api/reports/{run_id}/html", response_class=HTMLResponse)
def get_report_html(run_id: str, user: AuthUser = Depends(require_role("viewer"))) -> HTMLResponse:
    if not SAFE_RUN_ID_RE.fullmatch(run_id):
        raise HTTPException(status_code=400, detail="Invalid run_id format")
    cfg = get_settings()
    path = cfg.report_dir / f"{run_id}.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"HTML report {run_id} not found")
    return HTMLResponse(content=path.read_text(encoding="utf-8"))


# --- Real GitHub Repository Connections & Webhook ---
@app.get("/api/repositories")
def list_repositories(user: AuthUser = Depends(require_role("viewer"))) -> list[dict[str, Any]]:
    return _get_store().list_repositories()


@app.post("/api/repositories", status_code=201)
def connect_repository(
    req: ConnectRepoRequest, user: AuthUser = Depends(require_role("developer"))
) -> dict[str, Any]:
    store = _get_store()
    verified = False
    default_branch = req.default_branch
    if req.verify_with_github:
        try:
            meta = GitHubClient().verify_repository(req.full_name)
            verified = True
            default_branch = meta["default_branch"]
        except Exception as exc:
            store.record_audit(user.username, user.role, "CONNECT_REPO", req.full_name, "FAILED", str(exc))
            raise HTTPException(status_code=502, detail=f"GitHub API verification failed: {exc}") from exc

    repo = store.upsert_repository(
        full_name=req.full_name,
        default_branch=default_branch,
        migration_dirs=req.migration_dirs,
        config_path=req.config_path,
        github_verified=verified,
        created_by=user.username,
    )
    store.record_audit(user.username, user.role, "CONNECT_REPO", req.full_name, "SUCCESS", f"verified={verified}")
    return repo


@app.delete("/api/repositories/{owner}/{name}")
def remove_repository(
    owner: str, name: str, user: AuthUser = Depends(require_role("admin"))
) -> dict[str, Any]:
    full_name = f"{owner}/{name}"
    store = _get_store()
    deleted = store.delete_repository(full_name)
    if not deleted:
        raise HTTPException(status_code=404, detail="Repository not found")
    store.record_audit(user.username, user.role, "DELETE_REPO", full_name, "SUCCESS")
    return {"deleted": full_name}


@app.post("/api/webhooks/github")
async def github_webhook(
    request: Request,
    x_github_event: str | None = Header(default=None),
    x_hub_signature_256: str | None = Header(default=None),
) -> dict[str, Any]:
    """Real GitHub Webhook receiver with mandatory HMAC-SHA256 signature verification."""
    secret = os.getenv("RP_GITHUB_WEBHOOK_SECRET", "")
    body = await request.body()
    if not secret or not verify_github_webhook_signature(body, x_hub_signature_256, secret):
        raise HTTPException(status_code=401, detail="Invalid or missing GitHub webhook signature")

    payload = json.loads(body.decode("utf-8"))
    repo_name = payload.get("repository", {}).get("full_name", "unknown")
    action = payload.get("action", "unknown")
    pr_num = payload.get("pull_request", {}).get("number")
    sha = payload.get("pull_request", {}).get("head", {}).get("sha", "")

    store = _get_store()
    store.record_audit(
        actor="github-webhook",
        role="system",
        action=f"WEBHOOK_{x_github_event or 'unknown'}",
        resource=f"{repo_name}#PR{pr_num}",
        outcome="ACCEPTED",
        details=f"action={action} sha={sha[:12]}",
    )
    return {
        "accepted": True,
        "event": x_github_event,
        "action": action,
        "repository": repo_name,
        "pr_number": pr_num,
        "head_sha": sha,
    }


# --- Database Connections & Production Guard ---
@app.get("/api/connections")
def list_connections(user: AuthUser = Depends(require_role("viewer"))) -> list[dict[str, Any]]:
    return _get_store().list_db_connections()


@app.post("/api/connections/test")
def test_connection(
    req: TestConnectionRequest, user: AuthUser = Depends(require_role("developer"))
) -> dict[str, Any]:
    store = _get_store()
    try:
        validate_safe_test_database_url(req.database_url)
    except ProductionDatabaseBlockedError as exc:
        store.record_audit(user.username, user.role, "TEST_DB_CONN", req.schema_name, "BLOCKED_PROD", str(exc))
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    adapter = DatabaseAdapter(req.database_url, schema_name=req.schema_name)
    try:
        adapter.connect(max_retries=1)
        adapter.close()
    except Exception as exc:
        store.record_audit(user.username, user.role, "TEST_DB_CONN", req.schema_name, "FAILED", str(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    store.record_audit(user.username, user.role, "TEST_DB_CONN", req.schema_name, "SUCCESS", adapter.engine_name)
    return {"connected": True, "engine": adapter.engine_name, "production_guard": "SAFE_TEST_TARGET"}


@app.post("/api/connections", status_code=201)
def save_connection(
    req: SaveConnectionRequest, user: AuthUser = Depends(require_role("admin"))
) -> dict[str, Any]:
    store = _get_store()
    try:
        validate_safe_test_database_url(req.database_url)
    except ProductionDatabaseBlockedError as exc:
        store.record_audit(user.username, user.role, "SAVE_DB_CONN", req.name, "BLOCKED_PROD", str(exc))
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    engine = "postgresql" if req.database_url.startswith(("postgresql://", "postgres://")) else "sqlite"
    saved = store.save_db_connection(
        name=req.name,
        engine=engine,
        raw_url=req.database_url,
        secret_ref=req.secret_ref,
        schema_name=req.schema_name,
        created_by=user.username,
    )
    store.record_audit(user.username, user.role, "SAVE_DB_CONN", req.name, "SUCCESS", f"engine={engine}")
    return saved


# --- Audit Logs ---
@app.get("/api/audit-logs")
def audit_logs(limit: int = 100, user: AuthUser = Depends(require_role("admin"))) -> list[dict[str, Any]]:
    return _get_store().list_audit_logs(limit=limit)


# --- Scanning & Verification Endpoints ---
@app.post("/api/scan")
def api_scan(req: ScanRequest, user: AuthUser = Depends(require_role("developer"))) -> dict[str, Any]:
    result = scan_sql(req.sql, migration_file=req.migration_name)
    _get_store().record_audit(
        user.username, user.role, "SCAN_SQL", req.migration_name, result.highest_severity.value
    )
    return result.model_dump()


@app.post("/api/verify")
def api_verify(req: VerifyRequest, user: AuthUser = Depends(require_role("developer"))) -> dict[str, Any]:
    cfg = get_settings()
    report = evaluate_migration(
        migration_sql=req.sql,
        migration_name=req.migration_name,
        domain=req.domain,
        scenario="dashboard_custom_verify",
        fixture_count=req.fixture_count,
        settings=cfg,
    )
    _get_store().record_audit(
        user.username, user.role, "VERIFY_SQL", req.migration_name, report.decision.value, report.run_id
    )
    return report.model_dump()


@app.post("/api/verify-project")
def api_verify_project(
    req: ProjectVerifyRequest, user: AuthUser = Depends(require_role("developer"))
) -> dict[str, Any]:
    """Run config-driven `revertproof.yaml` verification (works for any repository config)."""
    cfg_path = (PROJECT_ROOT / req.config_path).resolve()
    mig_path = (PROJECT_ROOT / req.migration_path).resolve()
    if not str(cfg_path).startswith(str(PROJECT_ROOT)) or not str(mig_path).startswith(str(PROJECT_ROOT)):
        raise HTTPException(status_code=400, detail="Path must stay inside repository root")
    if not cfg_path.exists() or not mig_path.exists():
        raise HTTPException(status_code=404, detail="Config or migration file not found")

    project_cfg = load_project_config(cfg_path)
    report, schema_diff = evaluate_project_migration(
        project_config=project_cfg,
        migration_sql=mig_path.read_text(encoding="utf-8"),
        migration_name=req.migration_path,
        repo_root=PROJECT_ROOT,
        settings=get_settings(),
    )
    _get_store().record_audit(
        user.username, user.role, "VERIFY_PROJECT", req.migration_path, report.decision.value, report.run_id
    )
    return {
        "report": report.model_dump(),
        "schema_diff": schema_diff.model_dump() if schema_diff else None,
    }


@app.post("/api/run-preset")
def api_run_preset(
    req: PresetRunRequest, user: AuthUser = Depends(require_role("developer"))
) -> dict[str, Any]:
    cfg = get_settings()
    if req.preset == "full_demo":
        res = run_full_demo(fixture_count=req.fixture_count, settings=cfg, quiet=True)
        _get_store().record_audit(user.username, user.role, "RUN_DEMO", "full_demo", "COMPLETED")
        return {"runs": {k: v.model_dump() for k, v in res.items()}}

    preset_map = {
        "banking_unsafe": (PROJECT_ROOT / "sql" / "unsafe_v2.sql", "banking", "scenario_a_unsafe_banking"),
        "banking_safe": (PROJECT_ROOT / "sql" / "safe_expand.sql", "banking", "scenario_b_safe_expand_banking"),
        "banking_rollback": (PROJECT_ROOT / "sql" / "safe_expand.sql", "banking", "scenario_c_rollback_recovery"),
        "ecommerce_unsafe": (PROJECT_ROOT / "sql" / "ecommerce" / "unsafe_v2.sql", "ecommerce", "scenario_d_ecommerce_unsafe"),
        "ecommerce_safe": (PROJECT_ROOT / "sql" / "ecommerce" / "safe_expand.sql", "ecommerce", "scenario_d_ecommerce_safe"),
    }
    sql_path, domain, scenario = preset_map[req.preset]
    fixtures = req.fixture_count if domain == "banking" else min(req.fixture_count, 200)
    report = evaluate_migration_file(
        migration_path=sql_path,
        domain=domain,
        scenario=scenario,
        fixture_count=fixtures,
        settings=cfg,
    )
    _get_store().record_audit(user.username, user.role, "RUN_PRESET", req.preset, report.decision.value, report.run_id)
    return report.model_dump()
