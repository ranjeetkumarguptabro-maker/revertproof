"""Configuration management via environment variables (never hardcoded secrets)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for RevertProof loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="RP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(
        default="sqlite:///:memory:",
        description="Isolated test database URL (postgresql://... or sqlite:///:memory:)",
    )
    db_schema: str = Field(
        default="rp_demo",
        description="Isolated PostgreSQL schema rebuilt on every run",
    )
    report_dir: Path = Field(
        default=Path("reports"),
        description="Local directory for JSON and HTML evidence reports",
    )
    log_level: str = Field(default="INFO")
    aws_region: str = Field(default="us-east-1")
    aws_secret_id: str = Field(default="")
    aws_s3_bucket: str = Field(default="")
    aws_cloudwatch_namespace: str = Field(default="RevertProof")

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith(("postgresql://", "postgres://"))


def configure_logging(level: str | None = None) -> None:
    """Configure structured logging for RevertProof."""
    log_level = (level or os.getenv("RP_LOG_LEVEL", "INFO")).upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )


def get_settings() -> Settings:
    """Return a fresh Settings instance."""
    return Settings()
