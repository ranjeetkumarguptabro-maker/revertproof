"""Tests for real-world product features:
- Config-driven `revertproof.yaml` runner + external command contracts + live SchemaDiff
- Production Database Guard
- Real Authentication (PBKDF2 + HMAC-SHA256 tokens), RBAC (admin/developer/viewer), & Audit Logs
- GitHub Webhook HMAC-SHA256 verification, PR comment formatting, and Repository / DB Connection APIs
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from revertproof.cli import main as cli_main
from revertproof.config import Settings
from revertproof.contract_runner import evaluate_project_migration
from revertproof.dashboard import app
from revertproof.github_integration import (
    format_pr_comment_markdown,
    verify_github_webhook_signature,
)
from revertproof.models import GateDecision
from revertproof.project_config import (
    CommandContractCheck,
    RevertProofProjectConfig,
    SqlContractCheck,
    VersionContractConfig,
    load_project_config,
)
from revertproof.safety_guard import ProductionDatabaseBlockedError, validate_safe_test_database_url
from revertproof.store import mask_database_url

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_production_db_guard_blocks_prod_and_allows_test() -> None:
    with pytest.raises(ProductionDatabaseBlockedError):
        validate_safe_test_database_url(
            "postgresql://admin:secret@bank-prod.cluster.us-east-1.rds.amazonaws.com:5432/ledger"
        )
    with pytest.raises(ProductionDatabaseBlockedError):
        validate_safe_test_database_url("postgresql://user:pass@localhost:5432/payments_production")

    # Isolated test DB passes
    validate_safe_test_database_url("postgresql://rp_tester:pass@localhost:54329/revertproof_test")
    validate_safe_test_database_url("sqlite:///:memory:")


def test_mask_database_url_hides_password() -> None:
    masked = mask_database_url("postgresql://rp_tester:SuperSecret99@rds.internal:5432/testdb")
    assert "SuperSecret99" not in masked
    assert "***" in masked


def test_config_driven_runner_with_schema_diff_and_external_command(tmp_path: Path) -> None:
    cfg = load_project_config(PROJECT_ROOT / "examples" / "banking" / "revertproof.yaml")
    # Attach a real external command check (runs Python subprocess with RP_DATABASE_URL injected)
    cfg.v2_contracts.command_checks.append(
        CommandContractCheck(
            name="external_env_probe",
            command=[sys.executable, "-c", "import os; assert os.environ['RP_DATABASE_URL']"],
        )
    )
    settings = Settings(database_url="sqlite:///:memory:", report_dir=tmp_path)

    # Safe expand passes + shows ADDED columns in schema diff
    safe_sql = (PROJECT_ROOT / "sql" / "safe_expand.sql").read_text(encoding="utf-8")
    report_ok, diff_ok = evaluate_project_migration(
        cfg, safe_sql, "sql/safe_expand.sql", PROJECT_ROOT, settings=settings
    )
    assert report_ok.decision == GateDecision.PASS
    assert report_ok.exit_code == 0
    assert diff_ok is not None
    assert diff_ok.has_breaking_changes is False
    added_cols = {c.column for c in diff_ok.column_changes if c.change_type == "ADDED"}
    assert {"amount_minor", "currency_code"} <= added_cols

    # Unsafe migration blocked + shows DROPPED currency in schema diff
    unsafe_sql = (PROJECT_ROOT / "sql" / "unsafe_v2.sql").read_text(encoding="utf-8")
    report_fail, diff_fail = evaluate_project_migration(
        cfg, unsafe_sql, "sql/unsafe_v2.sql", PROJECT_ROOT, settings=settings
    )
    assert report_fail.decision == GateDecision.FAIL
    assert report_fail.exit_code == 1
    assert diff_fail is not None
    assert diff_fail.has_breaking_changes is True
    assert any(c.column == "currency" and c.change_type == "DROPPED" for c in diff_fail.column_changes)

    # Verify Markdown PR comment formatter
    md = format_pr_comment_markdown(report_fail)
    assert "RevertProof" in md and "FAIL" in md


def test_custom_arbitrary_saas_schema_works_without_banking_code(tmp_path: Path) -> None:
    """Proves RevertProof works for ANY company's schema (e.g., SaaS subscriptions)."""
    (tmp_path / "base.sql").write_text(
        "CREATE TABLE subscriptions (sub_id TEXT PRIMARY KEY, plan TEXT NOT NULL, seats INT NOT NULL);"
        "INSERT INTO subscriptions VALUES ('SUB1', 'PRO', 10);",
        encoding="utf-8",
    )
    project_cfg = RevertProofProjectConfig(
        project_name="saas-billing",
        baseline_sql="base.sql",
        v2_contracts=VersionContractConfig(
            version_label="SaaS V2",
            sql_checks=[
                SqlContractCheck(
                    name="v2_reads_tier",
                    operation_type="READ",
                    sql="SELECT sub_id, tier_name FROM subscriptions;",
                    expect_exact_rows=1,
                )
            ],
        ),
        v1_contracts=VersionContractConfig(
            version_label="SaaS V1",
            sql_checks=[
                SqlContractCheck(
                    name="v1_reads_plan",
                    operation_type="READ",
                    sql="SELECT sub_id, plan FROM subscriptions;",
                    expect_exact_rows=1,
                )
            ],
        ),
    )
    settings = Settings(database_url="sqlite:///:memory:", report_dir=tmp_path / "reports")

    # Unsafe rename breaks SaaS V1
    r_bad, _ = evaluate_project_migration(
        project_cfg,
        "ALTER TABLE subscriptions RENAME COLUMN plan TO tier_name;",
        "002_rename.sql",
        tmp_path,
        settings=settings,
    )
    assert r_bad.decision == GateDecision.FAIL

    # Safe expand keeps `plan` and adds `tier_name`
    r_ok, _ = evaluate_project_migration(
        project_cfg,
        "ALTER TABLE subscriptions ADD COLUMN tier_name TEXT; UPDATE subscriptions SET tier_name = plan;",
        "002_expand.sql",
        tmp_path,
        settings=settings,
    )
    assert r_ok.decision == GateDecision.PASS


def test_auth_rbac_repos_connections_webhook_and_audit_logs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RP_REPORT_DIR", str(tmp_path))
    monkeypatch.setenv("RP_STATE_DB", str(tmp_path / "state.db"))
    monkeypatch.setenv("RP_AUTH_ENABLED", "true")
    monkeypatch.setenv("RP_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("RP_ADMIN_PASSWORD", "StrongAdminPass123!")
    monkeypatch.setenv("RP_GITHUB_WEBHOOK_SECRET", "whsec_test_12345")
    client = TestClient(app)

    # Unauthenticated request rejected
    assert client.get("/api/overview").status_code == 401

    # Login as admin
    login_resp = client.post(
        "/api/auth/login", json={"username": "admin", "password": "StrongAdminPass123!"}
    )
    assert login_resp.status_code == 200
    admin_headers = {"Authorization": f"Bearer {login_resp.json()['access_token']}"}

    # Create viewer user & verify RBAC blocks viewer from admin/developer actions
    u_resp = client.post(
        "/api/users",
        headers=admin_headers,
        json={"username": "auditor", "password": "ViewerPass123!", "role": "viewer"},
    )
    assert u_resp.status_code == 201
    v_token = client.post(
        "/api/auth/login", json={"username": "auditor", "password": "ViewerPass123!"}
    ).json()["access_token"]
    viewer_headers = {"Authorization": f"Bearer {v_token}"}

    assert client.get("/api/overview", headers=viewer_headers).status_code == 200
    assert client.get("/api/audit-logs", headers=viewer_headers).status_code == 403
    assert client.post("/api/scan", headers=viewer_headers, json={"sql": "SELECT 1;"}).status_code == 403

    # Connect repository & verify persistence
    r_conn = client.post(
        "/api/repositories",
        headers=admin_headers,
        json={"full_name": "acme-corp/payments-api", "migration_dirs": ["migrations"]},
    )
    assert r_conn.status_code == 201
    assert len(client.get("/api/repositories", headers=viewer_headers).json()) == 1

    # Production DB guard blocks prod connection save
    prod_resp = client.post(
        "/api/connections",
        headers=admin_headers,
        json={
            "name": "bad-prod",
            "database_url": "postgresql://u:p@prod-db.internal:5432/bank_prod",
        },
    )
    assert prod_resp.status_code == 403

    # Valid isolated connection succeeds and masks password
    ok_conn = client.post(
        "/api/connections",
        headers=admin_headers,
        json={
            "name": "ci-pg-test",
            "database_url": "postgresql://rp:SecretPw123@ci-db.internal:5432/revertproof_test",
            "secret_ref": "arn:aws:secretsmanager:us-east-1:123:secret:rp",
        },
    )
    assert ok_conn.status_code == 201
    assert "SecretPw123" not in ok_conn.json()["masked_url"]

    # Path traversal & invalid run_id blocked
    assert client.get("/api/reports/..%2F..%2Fetc", headers=admin_headers).status_code in (400, 404)
    assert client.get("/api/reports/..bad..", headers=admin_headers).status_code == 400

    # GitHub webhook HMAC-SHA256 verification
    payload = json.dumps(
        {
            "action": "opened",
            "repository": {"full_name": "acme-corp/payments-api"},
            "pull_request": {"number": 42, "head": {"sha": "abc123def456"}},
        }
    ).encode("utf-8")
    good_sig = "sha256=" + hmac.new(b"whsec_test_12345", payload, hashlib.sha256).hexdigest()
    assert verify_github_webhook_signature(payload, good_sig, "whsec_test_12345") is True
    assert verify_github_webhook_signature(payload, "sha256=bad", "whsec_test_12345") is False

    wh_bad = client.post(
        "/api/webhooks/github",
        content=payload,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": "sha256=bad"},
    )
    assert wh_bad.status_code == 401

    wh_ok = client.post(
        "/api/webhooks/github",
        content=payload,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": good_sig},
    )
    assert wh_ok.status_code == 200
    assert wh_ok.json()["pr_number"] == 42

    # Audit log recorded actions
    logs = client.get("/api/audit-logs", headers=admin_headers).json()
    actions = {entry["action"] for entry in logs}
    assert {"LOGIN", "CREATE_USER", "CONNECT_REPO", "SAVE_DB_CONN", "WEBHOOK_pull_request"} <= actions


def test_cli_init_and_test_connection(tmp_path: Path) -> None:
    out_yaml = tmp_path / "revertproof.yaml"
    assert cli_main(["init", "--name", "orders-svc", "--output", str(out_yaml)]) == 0
    assert out_yaml.exists()

    assert cli_main(["test-connection", "sqlite:///:memory:"]) == 0
    assert cli_main(["test-connection", "postgresql://u:p@prod.rds.amazonaws.com:5432/prod"]) == 1
