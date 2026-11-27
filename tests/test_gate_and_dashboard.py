"""Tests for Module 5 (Deployment Safety Gate), failure handling, CLI, and Module 6 (Dashboard API)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from revertproof.cli import main as cli_main
from revertproof.config import Settings
from revertproof.dashboard import app
from revertproof.gate import evaluate_migration, evaluate_migration_file
from revertproof.models import GateDecision, RollbackClassification

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_gate_blocks_unsafe_migration_with_exit_1(tmp_path: Path) -> None:
    cfg = Settings(database_url="sqlite:///:memory:", report_dir=tmp_path)
    report = evaluate_migration_file(
        PROJECT_ROOT / "sql" / "unsafe_v2.sql",
        domain="banking",
        fixture_count=100,
        settings=cfg,
    )
    assert report.decision == GateDecision.FAIL
    assert report.exit_code == 1
    assert report.rollback_classification == RollbackClassification.ROLL_FORWARD_ONLY
    assert (tmp_path / f"{report.run_id}.json").exists()
    assert (tmp_path / f"{report.run_id}.html").exists()


def test_gate_passes_safe_expand_migration_with_exit_0(tmp_path: Path) -> None:
    cfg = Settings(database_url="sqlite:///:memory:", report_dir=tmp_path)
    report = evaluate_migration_file(
        PROJECT_ROOT / "sql" / "safe_expand.sql",
        domain="banking",
        fixture_count=100,
        settings=cfg,
    )
    assert report.decision == GateDecision.PASS
    assert report.exit_code == 0
    assert report.rollback_classification == RollbackClassification.SAFE_ROLLBACK


def test_gate_returns_conditional_when_db_unavailable(tmp_path: Path) -> None:
    # Unreachable PostgreSQL endpoint -> must NEVER silently pass
    cfg = Settings(
        database_url="postgresql://invalid:invalid@127.0.0.1:1/nonexistent",
        report_dir=tmp_path,
    )
    report = evaluate_migration(
        "SELECT 1;",
        migration_name="noop.sql",
        settings=cfg,
    )
    assert report.decision == GateDecision.CONDITIONAL
    assert report.exit_code == 2


def test_cli_scan_and_verify_exit_codes(tmp_path: Path) -> None:
    rc_scan_unsafe = cli_main(
        ["scan", str(PROJECT_ROOT / "sql" / "unsafe_v2.sql"), "--fail-on-risk"]
    )
    assert rc_scan_unsafe == 1

    rc_scan_safe = cli_main(
        ["scan", str(PROJECT_ROOT / "sql" / "safe_expand.sql"), "--fail-on-risk"]
    )
    assert rc_scan_safe == 0

    rc_verify_unsafe = cli_main(
        [
            "verify",
            str(PROJECT_ROOT / "sql" / "unsafe_v2.sql"),
            "--fixtures",
            "50",
            "--report-dir",
            str(tmp_path),
        ]
    )
    assert rc_verify_unsafe == 1

    rc_verify_safe = cli_main(
        [
            "verify",
            str(PROJECT_ROOT / "sql" / "safe_expand.sql"),
            "--fixtures",
            "50",
            "--report-dir",
            str(tmp_path),
        ]
    )
    assert rc_verify_safe == 0


def test_dashboard_api_serves_real_results(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RP_REPORT_DIR", str(tmp_path))
    monkeypatch.setenv("RP_DATABASE_URL", "sqlite:///:memory:")
    client = TestClient(app)

    assert client.get("/").status_code == 200
    assert client.get("/api/health").json()["status"] == "ok"

    # Run preset safe banking scenario via API
    resp = client.post("/api/run-preset", json={"preset": "banking_safe", "fixture_count": 50})
    assert resp.status_code == 200
    run_data = resp.json()
    assert run_data["decision"] == "PASS"
    run_id = run_data["run_id"]

    # Overview and history reflect the real run
    ov = client.get("/api/overview").json()
    assert ov["total_migrations_scanned"] == 1
    assert ov["passed_runs"] == 1

    # JSON & HTML evidence endpoints work
    assert client.get(f"/api/reports/{run_id}").status_code == 200
    assert "RevertProof Evidence Report" in client.get(f"/api/reports/{run_id}/html").text

    # Live scanner endpoint works
    scan_resp = client.post("/api/scan", json={"sql": "ALTER TABLE t DROP COLUMN c;"})
    assert scan_resp.status_code == 200
    assert scan_resp.json()["highest_severity"] == "CRITICAL"
