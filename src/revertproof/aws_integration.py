"""Optional AWS integrations (Secrets Manager, S3 Evidence Storage, CloudWatch Metrics).

Never fakes AWS calls. If boto3 is unavailable or AWS credentials/env vars are unset,
returns explicit `skipped` metadata so local and AWS execution remain 100% transparent.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from revertproof.config import Settings
from revertproof.models import EvidenceReport

logger = logging.getLogger(__name__)


def get_aws_client_config() -> Any:
    """REL-04 Remediation: Shared adaptive retry + bounded timeout config for all AWS SDK clients."""
    from botocore.config import Config  # type: ignore[import-not-found]

    return Config(
        retries={"max_attempts": 5, "mode": "adaptive"},
        connect_timeout=5,
        read_timeout=15,
    )


def load_database_url_from_secrets_manager(settings: Settings) -> str | None:
    """Retrieve isolated RDS PostgreSQL credentials from AWS Secrets Manager with TLS enforced."""
    if not settings.aws_secret_id:
        return None
    try:
        import boto3  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "RP_AWS_SECRET_ID is set but boto3 is not installed. Run: pip install -e '.[aws]'"
        ) from exc

    client = boto3.client(
        "secretsmanager", region_name=settings.aws_region, config=get_aws_client_config()
    )
    resp = client.get_secret_value(SecretId=settings.aws_secret_id)
    secret = json.loads(resp["SecretString"])
    user = quote_plus(str(secret["username"]))
    password = quote_plus(str(secret["password"]))
    host = secret["host"]
    port = secret.get("port", 5432)
    dbname = secret.get("dbname", "revertproof_test")
    return f"postgresql://{user}:{password}@{host}:{port}/{dbname}?sslmode=require"


def publish_evidence_to_s3(
    report: EvidenceReport,
    json_path: Path,
    html_path: Path,
    settings: Settings,
) -> dict[str, Any]:
    """Upload JSON and HTML evidence reports to Amazon S3 if RP_AWS_S3_BUCKET is configured."""
    if not settings.aws_s3_bucket:
        return {"s3_uploaded": False, "reason": "RP_AWS_S3_BUCKET not configured (local mode)"}
    try:
        import boto3  # type: ignore[import-not-found]
    except ImportError:
        return {"s3_uploaded": False, "reason": "boto3 not installed"}

    s3 = boto3.client("s3", region_name=settings.aws_region, config=get_aws_client_config())
    prefix = f"runs/{report.run_id}"
    json_key = f"{prefix}/report.json"
    html_key = f"{prefix}/report.html"

    try:
        s3.upload_file(
            str(json_path),
            settings.aws_s3_bucket,
            json_key,
            ExtraArgs={"ContentType": "application/json", "ServerSideEncryption": "AES256"},
        )
        s3.upload_file(
            str(html_path),
            settings.aws_s3_bucket,
            html_key,
            ExtraArgs={"ContentType": "text/html", "ServerSideEncryption": "AES256"},
        )
        return {
            "s3_uploaded": True,
            "bucket": settings.aws_s3_bucket,
            "json_uri": f"s3://{settings.aws_s3_bucket}/{json_key}",
            "html_uri": f"s3://{settings.aws_s3_bucket}/{html_key}",
        }
    except Exception as exc:
        logger.warning("Failed to upload evidence to S3: %s", exc)
        return {"s3_uploaded": False, "error": str(exc)}


def publish_cloudwatch_metrics(
    report: EvidenceReport,
    settings: Settings,
) -> dict[str, Any]:
    """Emit deployment gate result metrics to Amazon CloudWatch when AWS S3/Region is active."""
    if not settings.aws_s3_bucket and not settings.aws_secret_id:
        return {"cloudwatch_published": False, "reason": "AWS integration not configured (local mode)"}
    try:
        import boto3  # type: ignore[import-not-found]
    except ImportError:
        return {"cloudwatch_published": False, "reason": "boto3 not installed"}

    cw = boto3.client("cloudwatch", region_name=settings.aws_region, config=get_aws_client_config())
    blocked_val = 1.0 if report.decision.value != "PASS" else 0.0
    try:
        cw.put_metric_data(
            Namespace=settings.aws_cloudwatch_namespace,
            MetricData=[
                {
                    "MetricName": "DeploymentBlocked",
                    "Dimensions": [{"Name": "Domain", "Value": report.domain}],
                    "Value": blocked_val,
                    "Unit": "Count",
                },
                {
                    "MetricName": "DeploymentBlocked",
                    "Dimensions": [{"Name": "Domain", "Value": "all"}],
                    "Value": blocked_val,
                    "Unit": "Count",
                },
                {
                    "MetricName": "VerificationRuns",
                    "Dimensions": [{"Name": "Decision", "Value": report.decision.value}],
                    "Value": 1.0,
                    "Unit": "Count",
                },
            ],
        )
        return {"cloudwatch_published": True, "namespace": settings.aws_cloudwatch_namespace}
    except Exception as exc:
        logger.warning("Failed to publish CloudWatch metric: %s", exc)
        return {"cloudwatch_published": False, "error": str(exc)}
