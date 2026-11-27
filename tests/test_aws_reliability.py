"""Automated verification tests for AWS Well-Architected Reliability Pillar remediations:
- REL-01: Private VPC S3 Gateway Endpoint on PrivateRouteTable
- REL-02: KMS-encrypted SNS Topic, least-privilege TopicPolicy, DeploymentBlocked + RDS Health Alarms
- REL-04: Adaptive boto3 retries & bounded timeouts across AWS SDK clients
"""

from __future__ import annotations

from pathlib import Path

from revertproof.aws_integration import get_aws_client_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CFN_PATH = PROJECT_ROOT / "infra" / "cloudformation" / "revertproof-stack.yaml"


def test_rel04_aws_client_config_enforces_adaptive_retries_and_timeouts() -> None:
    cfg = get_aws_client_config()
    assert cfg.retries["max_attempts"] == 5
    assert cfg.retries["mode"] == "adaptive"
    assert cfg.connect_timeout == 5
    assert cfg.read_timeout == 15


def test_rel01_and_rel02_cloudformation_reliability_controls_present() -> None:
    text = CFN_PATH.read_text(encoding="utf-8")
    # REL-01: S3 Gateway VPC Endpoint on PrivateRouteTable
    assert "S3GatewayEndpoint:" in text
    assert "VpcEndpointType: Gateway" in text
    assert "com.amazonaws.${AWS::Region}.s3" in text

    # REL-02: KMS-encrypted SNS topic + SourceAccount policy + AlarmActions + RDS health alarms
    assert "RevertProofAlertsTopic:" in text
    assert "KmsMasterKeyId: alias/aws/sns" in text
    assert "AlertsTopicPolicy:" in text
    assert "aws:SourceAccount:" in text
    assert "AlarmActions:" in text
    assert "RDSLowStorageAlarm:" in text
    assert "RDSHighCPUAlarm:" in text
