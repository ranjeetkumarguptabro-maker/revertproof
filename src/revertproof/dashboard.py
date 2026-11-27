"""Module 6: FastAPI Dashboard Backend.

Serves real verification results persisted in `reports/` and lets users run live
SQL scans and 1,000-transaction compatibility/integrity/rollback verifications.
Zero fake metrics — every number is computed from real backend runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from revertproof.config import get_settings
from revertproof.demo import run_full_demo
from revertproof.gate import evaluate_migration, evaluate_migration_file
from revertproof.models import RiskSeverity
from revertproof.reporting import load_recent_reports
from revertproof.scanner import scan_sql

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="RevertProof — Database-Aware Deployment Rollback Verification Platform",
    version="1.0.0",
    description="Proves whether previous application versions survive database migrations before deployment.",
)


class ScanRequest(BaseModel):
    sql: str = Field(..., min_length=1)
    migration_name: str = Field(default="custom_migration.sql")


class VerifyRequest(BaseModel):
    sql: str = Field(..., min_length=1)
    migration_name: str = Field(default="custom_migration.sql")
    domain: str = Field(default="banking", pattern="^(banking|ecommerce)$")
    fixture_count: int = Field(default=1000, ge=1, le=5000)


class PresetRunRequest(BaseModel):
    preset: str = Field(
        default="banking_unsafe",
        pattern="^(banking_unsafe|banking_safe|banking_rollback|ecommerce_unsafe|ecommerce_safe|full_demo)$",
    )
    fixture_count: int = Field(default=1000, ge=1, le=5000)


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html_file = STATIC_DIR / "index.html"
    return HTMLResponse(content=html_file.read_text(encoding="utf-8"))


@app.get("/api/health")
def health() -> dict[str, Any]:
    cfg = get_settings()
    return {
        "status": "ok",
        "engine": "postgresql" if cfg.is_postgres else "sqlite",
        "report_dir": str(cfg.report_dir),
    }


@app.get("/api/overview")
def overview() -> dict[str, Any]:
    """Compute real aggregate metrics from actual persisted verification runs."""
    cfg = get_settings()
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
        "engine": "postgresql" if cfg.is_postgres else "sqlite",
        "latest": latest.model_dump() if latest else None,
    }


@app.get("/api/history")
def history(limit: int = 50) -> list[dict[str, Any]]:
    cfg = get_settings()
    reports = load_recent_reports(cfg.report_dir, limit=limit)
    return [r.model_dump() for r in reports]


@app.get("/api/reports/{run_id}")
def get_report(run_id: str) -> JSONResponse:
    cfg = get_settings()
    path = cfg.report_dir / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Report {run_id} not found")
    return JSONResponse(content=__import__("json").loads(path.read_text(encoding="utf-8")))


@app.get("/api/reports/{run_id}/html", response_class=HTMLResponse)
def get_report_html(run_id: str) -> HTMLResponse:
    cfg = get_settings()
    path = cfg.report_dir / f"{run_id}.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"HTML report {run_id} not found")
    return HTMLResponse(content=path.read_text(encoding="utf-8"))


@app.post("/api/scan")
def api_scan(req: ScanRequest) -> dict[str, Any]:
    result = scan_sql(req.sql, migration_file=req.migration_name)
    return result.model_dump()


@app.post("/api/verify")
def api_verify(req: VerifyRequest) -> dict[str, Any]:
    cfg = get_settings()
    report = evaluate_migration(
        migration_sql=req.sql,
        migration_name=req.migration_name,
        domain=req.domain,
        scenario="dashboard_custom_verify",
        fixture_count=req.fixture_count,
        settings=cfg,
    )
    return report.model_dump()


@app.post("/api/run-preset")
def api_run_preset(req: PresetRunRequest) -> dict[str, Any]:
    cfg = get_settings()
    if req.preset == "full_demo":
        res = run_full_demo(fixture_count=req.fixture_count, settings=cfg, quiet=True)
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
    return report.model_dump()
