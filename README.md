# RevertProof — Database-Aware Deployment Rollback Verification Platform

> **Don't just deploy safely. Prove you can recover safely.**

**RevertProof** is a reusable, config-driven deployment rollback verification platform for modern engineering teams. Before a pull request merges or a CI/CD pipeline deploys, RevertProof rehearses your proposed database migrations against an isolated PostgreSQL test database, validates **both the new (V2) and previous rollback-target (V1) application contracts**, verifies exact data integrity, and blocks unsafe releases automatically.

While RevertProof works for **any relational database schema** via `revertproof.yaml`, it includes a flagship **Banking Transaction Migration** test suite (**1,000 synthetic transactions**) and an **E-Commerce Shopping Site** suite (**200 synthetic orders**) as built-in verification benchmarks.

---

## The Problem

1. **Application V1** reads `amount` as `NUMERIC(14,2)` (`125.50`) and `currency` (`EUR`) as a separate column.
2. **Application V2** migrates `amount` to `JSONB` (`{"value_minor": 12550, "currency": "EUR"}`) and drops `currency`.
3. V2 deploys and works — until a bug forces the team to roll application code back to V1.
4. **Code rolls back, but the database schema does not.** V1 crashes immediately (`column currency does not exist`).

RevertProof prevents this failure across **any schema** (banking, e-commerce, SaaS, healthcare) by proving rollback compatibility and data integrity *before* deployment.

---

## Architecture

```mermaid
flowchart LR
    PR["GitHub PR<br/>App Code + SQL Migration"] --> CP["GitHub Actions /<br/>AWS CodePipeline"]
    CP --> CB["RevertProof Safety Gate<br/>(CLI / AWS CodeBuild)"]
    CB --> M1["1. Static SQL Scanner"]
    M1 --> M2["2. Isolated DB Tester<br/>V2 + V1 Contracts + Schema Diff"]
    M2 --> M3["3. Exact Data Integrity<br/>Minor-Unit & Invariant Parity"]
    M3 --> M4["4. Expand-and-Contract<br/>Rollback Classifier"]
    M4 --> Gate{"Gate Decision"}
    Gate -- "PASS (exit 0)" --> Deploy["Allow Next Stage"]
    Gate -- "FAIL / CONDITIONAL (exit 1/2)" --> Block["Block Release"]
    CB <--> RDS[("Private Amazon RDS<br/>PostgreSQL 16 (TLS)")]
    CB --> SM["AWS Secrets Manager"]
    CB --> S3["Amazon S3<br/>JSON + HTML Evidence"]
    CB --> CW["Amazon CloudWatch<br/>Logs & Blocked Alarm"]
```

---

## Web Experience & Product Workspace

Launching the RevertProof server provides two connected experiences in a unified warm-paper (`#FAF9F5`) + mint (`#8CEAB3`) + obsidian (`#090C0B`) design system:

### 1. Interactive Product Homepage (`/`)
- **Hero Section:** `DON'T JUST DEPLOY SAFELY / PROVE YOU CAN / RECOVER SAFELY` with direct `GET STARTED` (`/app`) and `CHECK DEMO` (`/app?demo=1`) CTAs.
- **Sticky 2-to-5 Horizontal Scroll Workflow (`#workflow`):** Starts with 2 cards (`01 Detect Risky Migration → 02 Test Both App Versions`) and smoothly glides horizontally as you scroll to reveal `03 Verify Data Integrity`, `04 Gate the Deployment`, and `05 Keep the Evidence`.
- **Real-World Example (`#real-use-case`):** Side-by-side `BEFORE` vs. `WITH REVERTPROOF` vs. `RESULT` banking migration comparison + Expand-and-Contract approach bar.
- **Animated Dark Safety Pipeline (`#pipeline`):** Live animated `DETECT → TEST → VERIFY → DEPLOY` stepper with traveling energy orbs, curved flowing SVG connectors (`INPUTS → Migration Safety Engine → VERIFICATION → DEPLOYMENT GATE`), GitHub/AWS/PostgreSQL badges, and animated `CONTINUOUS FEEDBACK` loop.
- **Interactive Production Scale Section (`#scale`):** 5-tab interactive view (`DEPLOYMENT`, `DATA INTEGRITY`, `COMPATIBILITY`, `RECOVERY`, `IMPACT`) dynamically updating the Before vs. With RevertProof chart and KPI stack.

### 2. Connected 10-Tab Real Product Workspace (`/app`)
1. **Overview** — Real metrics from persisted runs + one-click scenario runners (Banking Unsafe/Safe/Rollback, E-Commerce Unsafe/Safe, Full Demo).
2. **GitHub Repos** — Connect real GitHub repositories, configure migration directories, and receive HMAC-SHA256 verified PR webhooks (`POST /api/webhooks/github`).
3. **DB Connections** — Test and save isolated PostgreSQL/SQLite profiles with password masking and automatic **Production-DB Guard** protection.
4. **Migration Scanner** — Paste or upload any SQL migration to detect 11 destructive/rollback-breaking patterns.
5. **Compatibility & Schema Diff** — Inspect V2 and V1 read/write contract evidence plus live before/after structural schema diffs.
6. **Data Integrity** — Exact minor-unit reconciliation, missing/duplicate PK detection, currency parity, and NULL checks.
7. **Safety Gate** — `PASS` (`exit 0`), `FAIL` (`exit 1`), `CONDITIONAL` (`exit 2`), rollback classification, and Expand-and-Contract guidance.
8. **Test History** — Real persisted verification runs.
9. **Reports** — Downloadable machine-readable JSON and self-contained HTML evidence reports.
10. **Audit Logs** — Append-only security & activity audit trail.

---

## Using RevertProof in Any Company Repository

### 1. Initialize Configuration
```bash
revertproof init --name my-service
```
Customize `revertproof.yaml` with your `migration_dirs`, `baseline_sql`, `seed_sql`, V1/V2 SQL contracts or external `pytest`/Docker commands (`RP_DATABASE_URL` injected automatically), generic table invariants, and production-DB blocklist. See [examples/banking/revertproof.yaml](examples/banking/revertproof.yaml).

### 2. Validate Isolated DB Connection & Run Locally
```bash
# Blocks any suspected production hostname/dbname automatically
revertproof test-connection "postgresql://rp_tester:pass@localhost:54329/revertproof_test"

# Run config-driven verification on changed migrations (via git diff or --migration)
revertproof run --config examples/banking/revertproof.yaml --migration sql/safe_expand.sql
```

### 3. Add to GitHub Actions (Reusable Workflow)
```yaml
jobs:
  migration-rollback-gate:
    uses: ranjeetkumarguptabro-maker/revertproof/.github/workflows/revertproof-reusable.yml@main
    with:
      config_path: revertproof.yaml
      publish_pr_status: true
    secrets: inherit
```
RevertProof detects changed `.sql` files in the PR, rehearses them against an ephemeral PostgreSQL 16 database, publishes a real GitHub Commit Status check + Markdown PR review comment, and blocks merging when critical checks fail.

---

## Quick Start (Local)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
# Optional: PostgreSQL & AWS SDK extras
pip install -e ".[postgres,aws,dev]"
```

### Start the Web App (Homepage + Connected Workspace)
```bash
PYTHONPATH=src uvicorn revertproof.dashboard:app --host 127.0.0.1 --port 8000
```
Open:
- **Homepage:** http://127.0.0.1:8000
- **Product Workspace (`GET STARTED`):** http://127.0.0.1:8000/app
- **Live 1,000-Tx Demo (`CHECK DEMO`):** http://127.0.0.1:8000/app?demo=1
- **OpenAPI / Swagger Docs:** http://127.0.0.1:8000/docs

### Run CLI & 1,000-Transaction Demo
```bash
# Run Scenarios A (Unsafe -> BLOCKED), B (Safe Expand -> PASS), C (Rollback -> PASS), D (E-Commerce)
PYTHONPATH=src python3 -m revertproof.demo

# Run against local Docker PostgreSQL 16
docker compose up -d db
RP_DATABASE_URL="postgresql://rp_tester:rp_local_dev_only@localhost:54329/revertproof_test" \
  PYTHONPATH=src python3 -m revertproof.demo
```

### Run Linter & Automated Tests
```bash
PYTHONPATH=src ruff check src tests
PYTHONPATH=src pytest -v --cov=revertproof
```

---

## Implemented Modules & Verification Status

| Module / Capability | Location | Status |
|---|---|---|
| **Module 1: SQL Migration Scanner** (11 rules, severities, reviewed exceptions) | `src/revertproof/scanner.py` | **IMPLEMENTED AND TESTED** |
| **Module 2: Database Compatibility & Live Schema Diff** (V2 + V1 READ/INSERT/UPDATE, PostgreSQL + SQLite) | `src/revertproof/compatibility.py`, `src/revertproof/db.py`, `src/revertproof/schema_inspector.py` | **IMPLEMENTED AND TESTED** *(SQLite verified locally; PostgreSQL adapter tested in GitHub Actions CI)* |
| **Module 3: Exact Data Integrity Checker** (minor-unit cents, IDs, duplicates, NULLs, currency, timestamps, generic invariants) | `src/revertproof/integrity.py`, `src/revertproof/contract_runner.py` | **IMPLEMENTED AND TESTED** |
| **Module 4: Safe Migration Engine** (Expand + Backfill + Bidirectional Dual-Write Trigger + Conflict Guard + Rollback Classifier) | `src/revertproof/migration_engine.py`, `sql/safe_expand.sql`, `sql/safe_contract.sql` | **IMPLEMENTED AND TESTED** |
| **Module 5: Deployment Safety Gate, CLI & CI/CD** (`PASS`=0, `FAIL`=1, `CONDITIONAL`=2, `buildspec.yml`, reusable GitHub Action) | `src/revertproof/gate.py`, `src/revertproof/cli.py`, `.github/workflows/revertproof-reusable.yml` | **IMPLEMENTED AND TESTED** |
| **Module 6: Homepage & 10-Tab Product Workspace** (FastAPI backend + `/` landing page + `/app` workspace) | `src/revertproof/dashboard.py`, `src/revertproof/static/home.html`, `src/revertproof/static/index.html` | **IMPLEMENTED AND TESTED** |
| **Real GitHub Integration** (REST API v3, PR migration detection, Commit Status, PR comments, HMAC-SHA256 webhooks) | `src/revertproof/github_integration.py` | **IMPLEMENTED AND TESTED** |
| **Security, Auth, RBAC, Production-DB Guard & Audit Logs** (PBKDF2-SHA256, HMAC tokens, `X-API-Key`, masked URLs, path-traversal guard) | `src/revertproof/auth.py`, `src/revertproof/safety_guard.py`, `src/revertproof/store.py` | **IMPLEMENTED AND TESTED** |
| **AWS Infrastructure-as-Code** (Private VPC, Private RDS PG16 + `rds.force_ssl=1`, Secrets Manager, S3 TLS-only bucket, CodeBuild, CodePipeline, CloudWatch) | `infra/cloudformation/revertproof-stack.yaml` | **IMPLEMENTED BUT NOT TESTED** *(ready for AWS account deployment)* |
| **Official AWS Agent Toolkit Well-Architected Review** | `docs/well-architected-guide.md` | **NOT IMPLEMENTED** *(must be run externally with official Agent Toolkit skill)* |

---

## Actual Verified Test Results

- **Linter (`ruff check src tests`):** `All checks passed!`
- **Test Suite (`pytest -v --cov=revertproof`):** **28/28 passed** (including `test_scanner.py`, `test_compatibility_and_dual_write.py`, `test_integrity.py`, `test_gate_and_dashboard.py`, and `test_product_platform.py`).
- **Production Hardening Roadmap (Honest Assessment):** Before multi-tenant enterprise rollout, replace local SQLite `StateStore` with an external PostgreSQL metadata database, integrate OIDC/SSO, enable Secrets Manager KMS CMK rotation, add VPC Interface Endpoints for zero-internet CodeBuild egress, and add multi-replica rate limiting.

---

## Documentation

- [Architecture & Security Controls](docs/architecture.md)
- [AWS Deployment Guide](docs/aws-deployment.md)
- [Official AWS Well-Architected Review Hackathon Guide](docs/well-architected-guide.md)
- [Hackathon Submission Write-Up & 3-Minute Video Script](docs/hackathon-submission.md)
- [API & Testing Guide](docs/api-and-testing.md)
