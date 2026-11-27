"""Persistent state store for connected repositories, DB profiles, users, and audit logs.

Uses SQLite for zero-dependency single-node persistence (and is cleanly abstracted so
PostgreSQL metadata storage can replace it in multi-node production deployments).
Raw database passwords are NEVER stored in API responses; store references to env vars
or AWS Secrets Manager ARNs instead.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def mask_database_url(url: str) -> str:
    """Mask password in any database URL before storing or displaying."""
    if url.startswith("sqlite://"):
        return url
    parsed = urlparse(url)
    if parsed.password:
        netloc = f"{parsed.username or ''}:***@{parsed.hostname or ''}"
        if parsed.port:
            netloc += f":{parsed.port}"
        return f"{parsed.scheme}://{netloc}{parsed.path}"
    return url


class StateStore:
    """Persistent repository, connection, user, and append-only audit log store."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('admin', 'developer', 'viewer')),
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS repositories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    full_name TEXT UNIQUE NOT NULL,
                    default_branch TEXT NOT NULL DEFAULT 'main',
                    migration_dirs TEXT NOT NULL DEFAULT '["migrations", "sql"]',
                    config_path TEXT NOT NULL DEFAULT 'revertproof.yaml',
                    github_verified INTEGER NOT NULL DEFAULT 0,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS db_connections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    engine TEXT NOT NULL,
                    masked_url TEXT NOT NULL,
                    secret_ref TEXT NOT NULL DEFAULT '',
                    schema_name TEXT NOT NULL DEFAULT 'rp_demo',
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    role TEXT NOT NULL,
                    action TEXT NOT NULL,
                    resource TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    details TEXT NOT NULL DEFAULT ''
                );
                """
            )

    # --- Users ---
    def get_user(self, username: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, username, password_hash, role, created_at FROM users WHERE username = ?;",
                (username,),
            ).fetchone()
            return dict(row) if row else None

    def count_users(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM users;").fetchone()
            return int(row["cnt"])

    def create_user(self, username: str, password_hash: str, role: str) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?);",
                (username, password_hash, role, now),
            )
        return {"username": username, "role": role, "created_at": now}

    def list_users(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, username, role, created_at FROM users ORDER BY id;"
            ).fetchall()
            return [dict(r) for r in rows]

    # --- Repositories ---
    def upsert_repository(
        self,
        full_name: str,
        default_branch: str,
        migration_dirs: list[str],
        config_path: str,
        github_verified: bool,
        created_by: str,
    ) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        dirs_json = json.dumps(migration_dirs)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO repositories (full_name, default_branch, migration_dirs, config_path, github_verified, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(full_name) DO UPDATE SET
                    default_branch=excluded.default_branch,
                    migration_dirs=excluded.migration_dirs,
                    config_path=excluded.config_path,
                    github_verified=excluded.github_verified;
                """,
                (
                    full_name,
                    default_branch,
                    dirs_json,
                    config_path,
                    1 if github_verified else 0,
                    created_by,
                    now,
                ),
            )
        return {
            "full_name": full_name,
            "default_branch": default_branch,
            "migration_dirs": migration_dirs,
            "config_path": config_path,
            "github_verified": github_verified,
            "created_by": created_by,
            "created_at": now,
        }

    def list_repositories(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM repositories ORDER BY id DESC;").fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["migration_dirs"] = json.loads(d["migration_dirs"])
                d["github_verified"] = bool(d["github_verified"])
                result.append(d)
            return result

    def delete_repository(self, full_name: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM repositories WHERE full_name = ?;", (full_name,))
            return cur.rowcount > 0

    # --- Database Connection Profiles ---
    def save_db_connection(
        self,
        name: str,
        engine: str,
        raw_url: str,
        secret_ref: str,
        schema_name: str,
        created_by: str,
    ) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        masked = mask_database_url(raw_url)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO db_connections (name, engine, masked_url, secret_ref, schema_name, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    engine=excluded.engine,
                    masked_url=excluded.masked_url,
                    secret_ref=excluded.secret_ref,
                    schema_name=excluded.schema_name;
                """,
                (name, engine, masked, secret_ref, schema_name, created_by, now),
            )
        return {
            "name": name,
            "engine": engine,
            "masked_url": masked,
            "secret_ref": secret_ref,
            "schema_name": schema_name,
            "created_by": created_by,
            "created_at": now,
        }

    def list_db_connections(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM db_connections ORDER BY id DESC;").fetchall()
            return [dict(r) for r in rows]

    # --- Append-Only Audit Log ---
    def record_audit(
        self,
        actor: str,
        role: str,
        action: str,
        resource: str,
        outcome: str,
        details: str = "",
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO audit_logs (timestamp, actor, role, action, resource, outcome, details) "
                "VALUES (?, ?, ?, ?, ?, ?, ?);",
                (now, actor, role, action, resource, outcome, details[:500]),
            )

    def list_audit_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?;", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
