# Official AWS Well-Architected Review Guide ("Let the Agent Review You" Hackathon)

> **Transparency Note:** The official AWS Agent Toolkit `aws-well-architected-review` skill is not installed in this local environment, so it has **NOT** been executed yet. Never fabricate review findings. Follow the exact steps below to complete this mandatory hackathon requirement genuinely.

## Step 1: Push RevertProof to a Public GitHub Repository & Deploy AWS Stack
1. Push `/Users/ranjeet/.gemini/antigravity/scratch/revertproof` to GitHub.
2. Deploy `infra/cloudformation/revertproof-stack.yaml` to your AWS account (see `docs/aws-deployment.md`).
3. Take a screenshot / terminal capture of the **"Before"** state (initial CloudFormation template + test run).

## Step 2: Install the Official AWS Agent Toolkit Skill
In **Kiro**, **Claude Code**, or **Codex**, install the official AWS Agent Toolkit skill from the AWS Builder Center article linked on the hackathon page:
- Article: https://builder.aws.com/content/3MKuEy8icUTBmJjXg8grmVi9jCs/can-your-agent-review-your-cloud-architecture

## Step 3: Run the Exact Official Skill Prompt
Open this repository in your coding agent with the Agent Toolkit skill enabled and run this exact prompt:

> `"Use the Well-Architected Review skill of Agent Toolkit to review this project."`

Verify the agent actually invokes the `aws-well-architected-review` skill (not a generic review) and save the full review output to `docs/well-architected-review-report.md`.

## Step 4: Pick ONE High-Impact Recommendation & Implement It for Real
Examples of real Well-Architected findings the skill commonly surfaces on this type of stack (pick what the skill *actually* flags on your repo):
- **Security (SEC)**: Adding KMS Customer Managed Key (CMK) encryption + automatic Secrets Manager rotation, or VPC Interface Endpoints (Secrets Manager, S3, CloudWatch Logs) so CodeBuild in private subnets needs zero public internet egress (`0.0.0.0/0` removed!).
- **Reliability (REL)**: Adding RDS Multi-AZ / automated snapshot before destructive rehearsal, or S3 Object Lock / Dead-Letter Queue for failed pipeline notifications.
- **Operational Excellence (OPS)**: Adding structured CloudWatch dashboard + SNS alert on `DeploymentBlocked` alarm.

## Step 5: Record Before-and-After Evidence
1. Commit the fix in a clean, focused Git commit referencing the exact Well-Architected pillar & finding ID.
2. Re-run `pytest -v`, `ruff check`, `aws cloudformation deploy`, and `revertproof demo`.
3. Re-run the Well-Architected Review skill to confirm the finding is resolved!
4. Capture the 3-minute video showing **Before -> Skill Finding -> Real Code/IaC Fix -> After Verification**.
