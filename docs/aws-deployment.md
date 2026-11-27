# AWS Deployment Guide

> **Deployment Status:** The CloudFormation template (`infra/cloudformation/revertproof-stack.yaml`) and `buildspec.yml` are complete and ready to deploy, but have **not yet been deployed** to an AWS account from this local session.

## Prerequisites
1. An AWS account with permissions to deploy CloudFormation, VPC, RDS, Secrets Manager, S3, CodeBuild, CodePipeline, and CloudWatch.
2. AWS CLI v2 configured (`aws configure`).
3. A GitHub repository containing this code and an AWS CodeConnections (CodeStar) connection ARN.

## Step 1: Validate the CloudFormation Template
```bash
aws cloudformation validate-template \
  --template-body file://infra/cloudformation/revertproof-stack.yaml
```

## Step 2: Deploy the Isolated RevertProof Stack
```bash
aws cloudformation deploy \
  --template-file infra/cloudformation/revertproof-stack.yaml \
  --stack-name revertproof-hackathon \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    ProjectName=revertproof \
    GitHubRepoOwner=<YOUR_GITHUB_USERNAME> \
    GitHubRepoName=revertproof \
    GitHubBranch=main \
    CodeStarConnectionArn=<YOUR_CODESTAR_CONNECTION_ARN> \
    DBInstanceClass=db.t4g.micro
```

## Step 3: Update Secret with RDS Host Endpoint
After the stack finishes, retrieve the outputs and add the `host` key to the Secrets Manager secret so `revertproof` connects over TLS (`sslmode=require`):
```bash
aws cloudformation describe-stacks \
  --stack-name revertproof-hackathon \
  --query "Stacks[0].Outputs" --output table
```

## Step 4: Trigger the Pipeline (Before & After Proof)
1. Push a commit setting `RP_MIGRATION_FILE=sql/unsafe_v2.sql` -> CodeBuild exits `1` -> **CodePipeline blocks deployment** -> CloudWatch `revertproof-deployment-blocked` alarm fires -> JSON/HTML report uploaded to S3.
2. Push a commit setting `RP_MIGRATION_FILE=sql/safe_expand.sql` -> CodeBuild exits `0` -> **CodePipeline passes** -> 1,000/1,000 transactions verified on private RDS PostgreSQL 16.

## Step 5: Cost Cleanup After Hackathon Demo
Always delete the stack when finished to avoid ongoing RDS/VPC charges:
```bash
# Empty the S3 evidence bucket first, then delete stack:
aws cloudformation delete-stack --stack-name revertproof-hackathon
```
