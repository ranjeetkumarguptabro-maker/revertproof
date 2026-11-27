"""Product configuration schema (`revertproof.yaml`) for ANY repository.

Allows any engineering team to configure their migration directories, baseline schema,
V1/V2 application contracts (SQL queries, CLI/Docker test commands, or HTTP checks),
generic data invariants, and production-DB safety guardrails.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from revertproof.models import ScannerExceptionRule


class SqlContractCheck(BaseModel):
    """A single SQL read/write/update contract executed against the migrated database."""

    name: str
    operation_type: Literal["READ", "INSERT", "UPDATE", "DELETE"] = "READ"
    sql: str
    params: list[str | int | float | bool | None] = Field(default_factory=list)
    expect_min_rows: int | None = None
    expect_exact_rows: int | None = None
    expect_error: bool = False
    description: str = ""


class CommandContractCheck(BaseModel):
    """An external CLI, pytest, or Docker test command run with RP_DATABASE_URL injected."""

    name: str
    command: list[str]
    timeout_seconds: int = Field(default=120, ge=5, le=900)
    working_dir: str | None = None
    description: str = ""


class VersionContractConfig(BaseModel):
    """Application contract suite for a specific version (V1 previous or V2 new)."""

    version_label: str = "V1"
    sql_checks: list[SqlContractCheck] = Field(default_factory=list)
    command_checks: list[CommandContractCheck] = Field(default_factory=list)


class NumericParityRule(BaseModel):
    """Verifies exact Decimal(legacy_column) * multiplier == int(new_column)."""

    legacy_column: str
    new_column: str
    multiplier: int = 100


class ColumnParityRule(BaseModel):
    """Verifies TRIM(legacy_column) == TRIM(new_column)."""

    legacy_column: str
    new_column: str


class TableInvariantConfig(BaseModel):
    """Generic data-integrity invariants for any relational table."""

    table: str
    primary_key: str
    required_not_null: list[str] = Field(default_factory=list)
    preserve_row_count: bool = True
    preserve_primary_keys: bool = True
    numeric_parity: list[NumericParityRule] = Field(default_factory=list)
    column_parity: list[ColumnParityRule] = Field(default_factory=list)
    cleanup_probe_ids: list[str] = Field(default_factory=list)


class SafetyPolicyConfig(BaseModel):
    """Deployment gate policy and production-database protection rules."""

    block_on_v1_failure: bool = True
    block_on_v2_failure: bool = True
    block_on_integrity_failure: bool = True
    block_on_critical_scan: bool = True
    blocked_db_patterns: list[str] = Field(
        default_factory=lambda: ["prod", "production", "live", "primary-db"]
    )
    scanner_exceptions: list[ScannerExceptionRule] = Field(default_factory=list)


class RevertProofProjectConfig(BaseModel):
    """Top-level `revertproof.yaml` schema for any company repository."""

    project_name: str = "my-service"
    migration_dirs: list[str] = Field(default_factory=lambda: ["migrations", "sql"])
    baseline_sql: str = "sql/baseline.sql"
    seed_sql: str | None = None
    v2_contracts: VersionContractConfig = Field(
        default_factory=lambda: VersionContractConfig(version_label="V2")
    )
    v1_contracts: VersionContractConfig = Field(
        default_factory=lambda: VersionContractConfig(version_label="V1")
    )
    invariants: list[TableInvariantConfig] = Field(default_factory=list)
    policy: SafetyPolicyConfig = Field(default_factory=SafetyPolicyConfig)


def load_project_config(path: str | Path) -> RevertProofProjectConfig:
    """Load and validate a `revertproof.yaml` configuration file."""
    cfg_path = Path(path)
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    return RevertProofProjectConfig.model_validate(raw)


def generate_starter_config(project_name: str = "my-service") -> str:
    """Return a documented starter `revertproof.yaml` for any engineering team."""
    sample = {
        "project_name": project_name,
        "migration_dirs": ["migrations", "sql"],
        "baseline_sql": "sql/baseline.sql",
        "seed_sql": None,
        "v2_contracts": {
            "version_label": "V2 (proposed release)",
            "sql_checks": [
                {
                    "name": "v2_read_records",
                    "operation_type": "READ",
                    "sql": "SELECT * FROM transactions LIMIT 10;",
                    "expect_min_rows": 1,
                }
            ],
            "command_checks": [],
        },
        "v1_contracts": {
            "version_label": "V1 (rollback target)",
            "sql_checks": [
                {
                    "name": "v1_read_records",
                    "operation_type": "READ",
                    "sql": "SELECT transaction_id, amount, currency FROM transactions LIMIT 10;",
                    "expect_min_rows": 1,
                }
            ],
            "command_checks": [],
        },
        "invariants": [
            {
                "table": "transactions",
                "primary_key": "transaction_id",
                "required_not_null": ["amount", "currency"],
                "preserve_row_count": True,
                "preserve_primary_keys": True,
                "numeric_parity": [],
                "column_parity": [],
            }
        ],
        "policy": {
            "block_on_v1_failure": True,
            "block_on_v2_failure": True,
            "block_on_integrity_failure": True,
            "block_on_critical_scan": True,
            "blocked_db_patterns": ["prod", "production", "live", "primary-db"],
            "scanner_exceptions": [],
        },
    }
    return yaml.safe_dump(sample, sort_keys=False)
