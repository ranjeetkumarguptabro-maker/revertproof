# RevertProof Architecture & Security Design

## Core Design Principle
Static SQL scanning (`Module 1`) provides fast early warnings, **never** proof of safety. RevertProof combines static scanning with **execution-based V2 + V1 compatibility testing (`Module 2`)** and **exact minor-unit data integrity reconciliation (`Module 3`)** inside an isolated, disposable database before allowing a deployment to proceed (`Module 5`).

## Multi-Domain Extensibility
RevertProof is not limited to banking. While **Banking (`transactions`)** is the primary flagship workload, the engine supports pluggable domain profiles:
- `banking`: 1,000 synthetic financial transactions (`NUMERIC` vs `BIGINT` minor units + `CHAR(3)` currency + bidirectional dual-write triggers).
- `ecommerce`: 200 synthetic shopping-site orders (`status`, `shipping_address`, `total_price` vs `order_state`, `delivery_address_json`, `total_price_minor`).
- Generic SQL scanning: works on **any** PostgreSQL/SQL migration file across any schema.

## AWS Architecture (`infra/cloudformation/revertproof-stack.yaml`)

| AWS Service | Role in RevertProof | Security & Cost Controls |
|---|---|---|
| **Amazon VPC (Private Subnets)** | Isolates test RDS & CodeBuild runner | No public IP on RDS; strict SG ingress (only CodeBuild SG -> port 5432) |
| **Amazon RDS for PostgreSQL 16** | Isolated disposable test database | `PubliclyAccessible: false`, `StorageEncrypted: true`, `rds.force_ssl: 1`, `db.t4g.micro` |
| **AWS Secrets Manager** | Stores isolated test DB credentials | Zero credentials in Git; CodeBuild reads secret via least-privilege IAM |
| **AWS CodeBuild** | Runs linter, pytest, and RevertProof safety gate | Attached to private subnets; exits non-zero on `FAIL` (1) or `CONDITIONAL` (2) |
| **AWS CodePipeline** | Blocks deployment if Safety Gate fails | Source (GitHub CodeConnection) -> Verification Gate -> Deploy |
| **Amazon S3** | Stores JSON & HTML evidence reports | SSE-AES256, Public Access Blocked, Versioning enabled, TLS-only bucket policy, 30-day lifecycle |
| **Amazon CloudWatch** | Build logs + `DeploymentBlocked` alarm | 14-day log retention; alarms when unsafe migrations are blocked |

## Honest Rollback Classification
RevertProof never claims every migration is reversible:
- `SAFE_ROLLBACK`: V1 & V2 read/write contracts + bidirectional dual-write + 100% integrity verified.
- `CONDITIONAL_ROLLBACK`: Warnings exist, dual-write unverified, or test DB unavailable (exit 2 — never silently passes).
- `ROLL_FORWARD_ONLY`: V1 fails against migrated schema (e.g., `DROP COLUMN`, `ALTER COLUMN TYPE`, `RENAME COLUMN`).
- `MANUAL_RECOVERY_REQUIRED`: Destructive data loss or integrity corruption (`DROP TABLE`, `TRUNCATE`, missing/corrupted rows).
