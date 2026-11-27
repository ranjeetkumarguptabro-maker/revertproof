# Hackathon Submission Write-Up & 3-Minute Video Script

## Project Summary ("Let the Agent Review You" — AWS Builder Center Hackathon)

**Project Name:** RevertProof — Database-Aware Deployment Rollback Verification Platform  
**Tagline:** *Don't just deploy safely. Prove you can recover safely.*

### What We Built
RevertProof is an automated CI/CD deployment safety gate built on **Amazon RDS for PostgreSQL, AWS CodeBuild, AWS CodePipeline, AWS Secrets Manager, Amazon S3, and Amazon CloudWatch**.

When a developer submits a database migration alongside application V2, RevertProof:
1. **Scans the SQL** for destructive patterns (`DROP COLUMN`, `ALTER COLUMN TYPE`, `RENAME COLUMN`, `DROP TABLE`, `NOT NULL` without default) and classifies rollback risk.
2. **Rehearses the migration** against an isolated disposable PostgreSQL schema loaded with **1,000 synthetic banking transactions** (and also supports **e-commerce** and general schemas).
3. **Tests BOTH Application V2 AND Application V1 (the rollback target)** for reads, inserts, updates, and bidirectional dual-write conflict guards against the same migrated database.
4. **Verifies exact financial integrity** using integer minor units (cents — never binary float), confirming zero missing IDs, zero duplicates, zero currency drift, and zero NULL violations.
5. **Blocks unsafe deployments** with a non-zero exit code in AWS CodeBuild/CodePipeline and persists JSON + HTML evidence reports to Amazon S3.

### Well-Architected Review Section *(Fill in after running the official Agent Toolkit skill)*
- **Skill Used:** Official AWS Agent Toolkit `aws-well-architected-review` skill
- **Recommendation Surfaced:** *(Paste exact finding ID and summary from `docs/well-architected-review-report.md`)*
- **Why We Chose It:** *(Explain security/reliability/operational impact)*
- **What We Changed:** *(Link exact commit SHA and CloudFormation/code diff)*
- **Measured Before vs. After:** *(Paste actual before/after test and review evidence)*

---

## 3-Minute Hackathon Video Script

| Time | Visual on Screen | Narration |
|---|---|---|
| **0:00–0:25** | Dashboard + Banking V1 table (`TX0001: 125.50 EUR`) | "Rolling back broken application code doesn't roll back your database schema. If V2 changes `amount` to JSONB and drops `currency`, rolling back to V1 crashes production. We built **RevertProof** on AWS to prove rollback compatibility *before* deployment." |
| **0:25–0:55** | Terminal running `revertproof demo` — Scenario A (`FAIL`, exit 1) | "Here are 1,000 synthetic banking transactions. When we test the unsafe V2 migration, V2 passes — but RevertProof tests V1 against the migrated schema, catches the missing `currency` column, and blocks the pipeline with exit code 1." |
| **0:55–1:30** | Scenario B (`sql/safe_expand.sql`) + Dashboard Integrity tab (`1000/1000` preserved) | "Switching to Expand-and-Contract keeps legacy columns and adds bidirectional dual-write triggers. Both V1 and V2 read and write cleanly, all 1,000 transactions match to the exact cent, and the safety gate passes. It also works for any schema — like our e-commerce orders demo." |
| **1:30–2:15** | Official AWS Agent Toolkit `aws-well-architected-review` output + Git diff | "We ran the official AWS Agent Toolkit Well-Architected Review skill against our AWS CodePipeline, CodeBuild, and private RDS architecture. It flagged **[INSERT REAL FINDING]**. Here is the exact before state, and here is the commit where we implemented the fix." |
| **2:15–2:55** | Re-running AWS stack / tests + S3 HTML evidence report + CloudWatch | "After implementing the recommendation, we re-verified our deployment and test suite. All 22 automated tests pass, the Well-Architected finding is resolved, and RevertProof proves safe recovery before every release." |

---

## Feature Delivery Status Matrix

| Deliverable / Feature | Status |
|---|---|
| Module 1: SQL Migration Scanner (11 rules + exceptions) | **IMPLEMENTED AND TESTED** |
| Module 2: Database Compatibility Tester (V1+V2 READ/INSERT/UPDATE, Banking + E-Commerce) | **IMPLEMENTED AND TESTED** (SQLite local verified; PostgreSQL psycopg adapter implemented, awaiting local Docker/RDS run) |
| Module 3: Transaction Integrity Checker (exact minor-unit cents, IDs, duplicates, NULLs, currency, timestamps) | **IMPLEMENTED AND TESTED** |
| Module 4: Safe Migration Engine (Expand + Backfill + Bidirectional Dual-Write Trigger + Rollback Classifier) | **IMPLEMENTED AND TESTED** |
| Module 5: Deployment Safety Gate (`PASS`=0, `FAIL`=1, `CONDITIONAL`=2) + CLI + `buildspec.yml` + GitHub Actions CI | **IMPLEMENTED AND TESTED** |
| Module 6: Live Web Dashboard (7 screens connected to real backend results) | **IMPLEMENTED AND TESTED** |
| End-to-End 1,000-Transaction Demo (Scenarios A, B, C + E-Commerce D) | **IMPLEMENTED AND TESTED** |
| AWS Infrastructure-as-Code (`infra/cloudformation/revertproof-stack.yaml`) | **IMPLEMENTED BUT NOT TESTED** (requires AWS credentials/account deployment) |
| Official AWS Agent Toolkit `aws-well-architected-review` execution & fix | **NOT IMPLEMENTED** (must be run externally with official skill — see `docs/well-architected-guide.md`) |
