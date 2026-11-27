# Preserved Pre-Remediation Official Assessment (BEFORE)

> Preserved verbatim copy of `aws-well-architected-review-report.md` from run `war-20261127-103100-rel` (executed using `aws/agent-toolkit-for-aws` `plugins/aws-core/skills/aws-well-architected-review/SKILL.md` v4 in disclosed degraded title-based Reliability Pillar mode against live stack `revertproof-hackathon` in `eu-north-1`).

See [aws-well-architected-review-report.md](../aws-well-architected-review-report.md) for the full initial report.

## Summary of Pre-Remediation Reliability Findings
- **REL-01 (Critical — Impact Severe, Likelihood High):** `PrivateRouteTable` (`revertproof-stack.yaml:70-89`) lacks an S3 Gateway Endpoint / VPC Endpoints for `SafetyGateCodeBuild` running in private subnets.
- **REL-02 (High — Impact Moderate, Likelihood High):** `DeploymentBlockedAlarm` (`revertproof-stack.yaml:223-239`) has no `AlarmActions` (no KMS-encrypted SNS topic), only monitors `Domain=banking`, and no RDS health alarms (`FreeStorageSpace`, `CPUUtilization`) exist.
- **REL-03 (High — Impact Severe, Likelihood Medium):** `IsolatedTestRDS` (`revertproof-stack.yaml:150-171`) is Single-AZ (`MultiAZ` unset).
- **REL-04 (High — Impact Moderate, Likelihood High):** `src/revertproof/aws_integration.py:32,57,98` uses default `boto3.client(...)` without explicit timeouts or adaptive retry backoff.
- **Overall Pre-Remediation Reliability Score:** `3 / 5`
