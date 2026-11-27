# RevertProof — Database-Aware Deployment Rollback Verification Platform

> **Don't just deploy safely. Prove you can recover safely.**

**RevertProof** is a database-aware deployment safety gate for CI/CD pipelines. It answers the question most teams forget to ask until an outage occurs:

> *"If we deploy Application V2 with this database migration, and V2 has a bug 10 minutes later, will rolling back to Application V1 actually work — and will every record's meaning stay intact?"*

While RevertProof works for **any relational database migration** (banking, e-commerce, SaaS, healthcare), our flagship demonstration uses a **fictional banking application** with **1,000 synthetic transactions**, alongside a second built-in **e-commerce shopping site** profile (`orders` table) to prove domain-agnostic extensibility.

---

## The Problem (Banking Flagship Example)

1. **Application V1** reads `amount` as a decimal (`125.50`) and `currency` (`EUR`) as a separate column.
2. **Application V2** migrates `amount` to JSONB (`{"value_minor": 12550, "currency": "EUR"}`) and drops `currency`.
3. V2 deploys and works fine — until an unrelated bug forces the team to roll back code to V1.
4. **Code rolls back, but the database does not.** V1 crashes immediately (`column currency does not exist`).

The exact same failure happens on an **e-commerce shopping site** if V2 renames `shipping_address` to `delivery_address_json` and drops `status` — rolling back the storefront code leaves checkout broken.

---

## Architecture

```mermaid
flowchart LR
    PR["GitHub PR<br/>App Code + SQL Migration"] --> CP["AWS CodePipeline"]
    CP --> CB["AWS CodeBuild<br/>RevertProof Safety Gate"]
    CB --> M1["1. Static SQL Scanner"]
    M1 --> M2["2. Isolated DB Tester<br/>V2 + V1 Read/Write Contracts"]
    M2 --> M3["3. Exact Data Integrity<br/>Minor-Unit Reconciliation"]
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

## Implemented Modules

| # | Module | Location | Status |
|---|---|---|---|
| 1 | **SQL Migration Scanner** (11 rules, line numbers, severities, reviewed exceptions) | `src/revertproof/scanner.py` | **IMPLEMENTED AND TESTED** |
| 2 | **Database Compatibility Tester** (V2 + V1 READ/INSERT/UPDATE on same migrated DB) | `src/revertproof/compatibility.py`, `src/revertproof/db.py` | **IMPLEMENTED AND TESTED** (SQLite verified locally; PostgreSQL adapter ready for Docker/RDS) |
| 3 | **Transaction & Data Integrity Checker** (exact integer minor-unit cents, IDs, duplicates, NULLs, currency, timestamps) | `src/revertproof/integrity.py` | **IMPLEMENTED AND TESTED** |
| 4 | **Safe Migration Engine** (Expand + Backfill + Bidirectional Dual-Write Trigger + Conflict Guard + Rollback Classifier) | `src/revertproof/migration_engine.py`, `sql/safe_expand.sql` | **IMPLEMENTED AND TESTED** |
| 5 | **Deployment Safety Gate & CLI** (`PASS` exit 0, `FAIL` exit 1, `CONDITIONAL` exit 2) | `src/revertproof/gate.py`, `src/revertproof/cli.py`, `buildspec.yml` | **IMPLEMENTED AND TESTED** |
| 6 | **Live Web Dashboard** (FastAPI + 7 views backed by real `reports/` runs) | `src/revertproof/dashboard.py`, `src/revertproof/static/index.html` | **IMPLEMENTED AND TESTED** |
| 7 | **AWS Infrastructure-as-Code** (Private VPC, Private RDS PG16 + TLS, Secrets Manager, S3, CodeBuild, CodePipeline, CloudWatch) | `infra/cloudformation/revertproof-stack.yaml` | **IMPLEMENTED BUT NOT TESTED** (requires AWS account deployment) |
| 8 | **Official AWS Agent Toolkit Well-Architected Review** | `docs/well-architected-guide.md` | **NOT IMPLEMENTED** (must be run with official Agent Toolkit skill in Kiro/Claude Code/Codex) |

---

## Using RevertProof in Any Company Repository (Real Product Workflow)

RevertProof is a **config-driven platform** — the banking and e-commerce scenarios are built-in test cases, not hardcoded limits.

### 1. Initialize in Your Repository
```bash
revertproof init --name my-service
```
Edit `revertproof.yaml` to define your `migration_dirs`, `baseline_sql`, `v1_contracts` / `v2_contracts` (SQL queries **or** real `pytest`/Docker commands with `RP_DATABASE_URL` injected), table `invariants`, and `blocked_db_patterns`. See [examples/banking/revertproof.yaml](examples/banking/revertproof.yaml) for a complete example.

### 2. Run Locally or in CI
```bash
# Validate isolated test DB connectivity & production-DB guard
revertproof test-connection "postgresql://rp_tester:pass@localhost:54329/revertproof_test"

# Run config-driven verification on changed migrations (via git diff or explicit file)
revertproof run --config examples/banking/revertproof.yaml --migration sql/safe_expand.sql
```

### 3. Drop into GitHub Actions (Reusable Workflow)
```yaml
jobs:
  migration-rollback-gate:
    uses: ranjeetkumarguptabro-maker/revertproof/.github/workflows/revertproof-reusable.yml@main
    with:
      config_path: revertproof.yaml
      publish_pr_status: true
    secrets: inherit
```
RevertProof detects changed `.sql` migrations in the PR, rehearses them in an isolated PostgreSQL 16 database, runs V2 + V1 contracts and live schema diff, posts a real GitHub Commit Status check + Markdown PR review comment, and blocks merging on failure.

### 4. Security, Auth & Production-Readiness Honesty
- **Locally Verified:** Production-DB URL guard (`ProductionDatabaseBlockedError`), PBKDF2-SHA256 + HMAC-SHA256 bearer auth & RBAC (`admin`/`developer`/`viewer`), CI `X-API-Key`, GitHub Webhook `X-Hub-Signature-256` verification, append-only audit log, password masking, path-traversal protection, config-driven `revertproof.yaml` runner, live schema diff, and 28 automated tests.
- **AWS-Ready (Not Yet Deployed):** CloudFormation template (`infra/cloudformation/revertproof-stack.yaml`) for private VPC, private encrypted RDS PG16 (`rds.force_ssl=1`), Secrets Manager, S3 TLS-only evidence bucket, CodeBuild, CodePipeline, and CloudWatch alarms.
- **Requires Production Hardening Before Enterprise Rollout:** External PostgreSQL metadata store (replacing local SQLite `StateStore`), OIDC/SSO identity provider integration, KMS CMK rotation, VPC Interface Endpoints for zero-internet CodeBuild egress, and multi-replica rate limiting.

---

## Quick Start (Local)

### 1. Install
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
# Optional: for PostgreSQL & AWS
pip install -e ".[postgres,aws,dev]"
```

### 2. Run the 1,000-Transaction End-to-End Demo
```bash
# Runs Scenario A (Unsafe Banking -> BLOCKED), Scenario B (Safe Expand -> PASS),
# Scenario C (Rollback to V1 -> PASS), and Scenario D (E-Commerce Unsafe vs Safe)
python3 -m revertproof.demo
```

### 3. Run Against Local Docker PostgreSQL 16
```bash
docker compose up -d db
export RP_DATABASE_URL="postgresql://rp_tester:rp_local_dev_only@localhost:54329/revertproof_test"
revertproof demo --fixtures 1000
```

### 4. CLI Commands
```bash
revertproof scan sql/unsafe_v2.sql --fail-on-risk
revertproof verify sql/unsafe_v2.sql --domain banking --fixtures 1000
revertproof verify sql/safe_expand.sql --domain banking --fixtures 1000
revertproof verify sql/ecommerce/safe_expand.sql --domain ecommerce --fixtures 200
revertproof dashboard --port 8000
```

### 5. Run Automated Tests & Linter
```bash
ruff check src tests
pytest -v --cov=revertproof
```

---

## Actual Verified Local Test Results

- **Linter (`ruff check src tests`)**: `All checks passed!`
- **Test Suite (`pytest -v --cov=revertproof`)**: **22 passed in 1.01s** (**81% code coverage**, engine: local SQLite)
- **End-to-End Demo (`python3 -m revertproof.demo`, 1,000 synthetic banking transactions + 200 e-commerce orders)**:
  - **Scenario A (Unsafe Banking)**: `v2_compatibility=PASS`, `v1_rollback_compatibility=FAIL`, `decision=FAIL (exit 1)` — **BLOCKED**
  - **Scenario B (Safe Expand Banking)**: `scanner=CLEAN`, `v2=PASS`, `v1=PASS`, `dual_write_sync=PASS`, `integrity=PASS (1000/1000)`, `decision=PASS (exit 0)`
  - **Scenario C (Rollback to V1 Verification)**: `PASS (exit 0)` with 1,000/1,000 exact minor-unit parity
  - **Scenario D (E-Commerce Shopping Site)**: Unsafe migration `FAIL (exit 1)` | Safe Expand `PASS (exit 0)` (200/200 orders)

---

## Documentation

- [Architecture & Security Controls](docs/architecture.md)
- [AWS Deployment Guide](docs/aws-deployment.md)
- [Official AWS Well-Architected Review Hackathon Guide](docs/well-architected-guide.md)
- [Hackathon Submission Write-Up & 3-Minute Video Script](docs/hackathon-submission.md)
- [API & Testing Guide](docs/api-and-testing.md)
