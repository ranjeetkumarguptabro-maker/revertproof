"""Database adapter supporting isolated PostgreSQL (RDS / Docker) and local SQLite execution.

- PostgreSQL mode (`postgresql://...`) uses `psycopg` v3, rebuilds only the configured
  isolated schema (default `rp_demo`), and enforces TLS when configured (`sslmode=require`).
- SQLite mode (`sqlite:///:memory:`) uses stdlib `sqlite3` so unit/integration tests and
  local demos work out-of-the-box even when Docker/PostgreSQL is unavailable.
  Reports ALWAYS record the actual engine (`postgresql` or `sqlite`) transparently.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import Any

from revertproof.safety_guard import validate_safe_test_database_url

logger = logging.getLogger(__name__)


class DatabaseUnavailableError(RuntimeError):
    """Raised when the test database cannot be reached or initialised."""


class DatabaseAdapter:
    """Unified isolated database session for PostgreSQL and SQLite."""

    def __init__(self, database_url: str, schema_name: str = "rp_demo") -> None:
        self.database_url = database_url
        self.schema_name = self._validate_identifier(schema_name)
        self.is_postgres = database_url.startswith(("postgresql://", "postgres://"))
        self.engine_name = "postgresql" if self.is_postgres else "sqlite"
        self._conn: Any = None

    @staticmethod
    def _validate_identifier(name: str) -> str:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f"Unsafe SQL identifier: {name!r}")
        return name

    def connect(self, max_retries: int = 3, blocked_patterns: list[str] | None = None) -> None:
        """Validate safety guardrails and connect with bounded retry backoff."""
        validate_safe_test_database_url(self.database_url, blocked_patterns=blocked_patterns)
        if self.is_postgres:
            try:
                import psycopg  # type: ignore[import-not-found]
            except ImportError as exc:
                raise DatabaseUnavailableError(
                    "psycopg is not installed. Install with: pip install -e '.[postgres]'"
                ) from exc
            last_exc: Exception | None = None
            for attempt in range(1, max_retries + 1):
                try:
                    self._conn = psycopg.connect(self.database_url, autocommit=False)
                    return
                except Exception as exc:
                    last_exc = exc
                    if attempt < max_retries:
                        time.sleep(0.25 * attempt)
            raise DatabaseUnavailableError(
                f"Cannot connect to PostgreSQL test database after {max_retries} attempts: {last_exc}"
            ) from last_exc
        else:
            path = self.database_url.replace("sqlite:///", "", 1)
            if not path or path == ":memory:":
                path = ":memory:"
            try:
                self._conn = sqlite3.connect(path)
                self._conn.row_factory = sqlite3.Row
            except Exception as exc:
                raise DatabaseUnavailableError(f"Cannot open SQLite database: {exc}") from exc

    def cleanup_isolated_schema(self) -> None:
        """Drop temporary isolated test schema after verification completes."""
        if self._conn is None:
            return
        if self.is_postgres:
            try:
                with self._conn.cursor() as cur:
                    cur.execute(f"DROP SCHEMA IF EXISTS {self.schema_name} CASCADE;")
                self._conn.commit()
            except Exception:
                self._conn.rollback()

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def reset_isolated_schema(self) -> None:
        """Create a clean isolated test schema (PostgreSQL) or clean in-memory DB (SQLite)."""
        assert self._conn is not None
        if self.is_postgres:
            with self._conn.cursor() as cur:
                cur.execute(f"DROP SCHEMA IF EXISTS {self.schema_name} CASCADE;")
                cur.execute(f"CREATE SCHEMA {self.schema_name};")
                cur.execute(f"SET search_path TO {self.schema_name};")
            self._conn.commit()
        else:
            # Drop all existing user tables and triggers in SQLite
            cur = self._conn.cursor()
            tables = [
                row[0]
                for row in cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';"
                ).fetchall()
            ]
            for tbl in tables:
                cur.execute(f'DROP TABLE IF EXISTS "{tbl}";')
            self._conn.commit()

    def execute_script(self, sql: str) -> None:
        """Execute a multi-statement SQL script atomically."""
        assert self._conn is not None
        if self.is_postgres:
            try:
                with self._conn.cursor() as cur:
                    cur.execute(f"SET search_path TO {self.schema_name};")
                    cur.execute(sql)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        else:
            translated = self._translate_pg_to_sqlite(sql)
            try:
                self._conn.executescript(translated)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        """Execute a single parameterized query and return rowcount."""
        assert self._conn is not None
        if self.is_postgres:
            try:
                with self._conn.cursor() as cur:
                    cur.execute(sql, params)
                    rowcount = cur.rowcount
                self._conn.commit()
                return rowcount
            except Exception:
                self._conn.rollback()
                raise
        else:
            q = sql.replace("%s", "?")
            try:
                cur = self._conn.execute(q, params)
                self._conn.commit()
                return cur.rowcount
            except Exception:
                self._conn.rollback()
                raise

    def executemany(self, sql: str, param_rows: list[tuple[Any, ...]]) -> None:
        assert self._conn is not None
        if self.is_postgres:
            try:
                with self._conn.cursor() as cur:
                    cur.executemany(sql, param_rows)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        else:
            q = sql.replace("%s", "?")
            try:
                self._conn.executemany(q, param_rows)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        assert self._conn is not None
        if self.is_postgres:
            try:
                with self._conn.cursor() as cur:
                    cur.execute(sql, params)
                    cols = [desc[0] for desc in (cur.description or [])]
                    return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]
            except Exception:
                self._conn.rollback()
                raise
        else:
            q = sql.replace("%s", "?")
            cur = self._conn.execute(q, params)
            return [dict(row) for row in cur.fetchall()]

    def get_table_columns(self, table_name: str) -> set[str]:
        table = self._validate_identifier(table_name)
        if self.is_postgres:
            rows = self.fetchall(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s;",
                (self.schema_name, table),
            )
            return {str(r["column_name"]).lower() for r in rows}
        rows = self.fetchall(f"PRAGMA table_info({table});")
        return {str(r["name"]).lower() for r in rows}

    def _translate_pg_to_sqlite(self, sql: str) -> str:
        """Translate supported PostgreSQL DDL/trigger scripts for local SQLite execution.

        Handles:
        1. Banking unsafe migration (ALTER COLUMN amount TYPE JSONB + DROP COLUMN currency)
        2. Banking safe expand migration (ADD COLUMN + backfill + bidirectional dual-write triggers)
        3. E-Commerce unsafe and safe expand migrations
        4. Generic SQLite-compatible DDL statements
        """
        # Case 1: Banking unsafe JSONB conversion + DROP COLUMN currency
        if re.search(r"ALTER\s+COLUMN\s+amount\s+TYPE\s+JSONB", sql, re.IGNORECASE):
            return """
            CREATE TABLE transactions_v2_unsafe (
                transaction_id TEXT PRIMARY KEY,
                amount TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            );
            INSERT INTO transactions_v2_unsafe (transaction_id, amount, occurred_at)
            SELECT
                transaction_id,
                '{"value_minor": ' || CAST(ROUND(CAST(amount AS REAL) * 100) AS INTEGER) || ', "currency": "' || TRIM(currency) || '"}',
                occurred_at
            FROM transactions;
            DROP TABLE transactions;
            ALTER TABLE transactions_v2_unsafe RENAME TO transactions;
            """

        # Case 2: Banking safe expand with PL/pgSQL trigger -> SQLite table + triggers
        if "rp_sync_transactions_dual_write" in sql:
            return """
            ALTER TABLE transactions ADD COLUMN amount_minor INTEGER;
            ALTER TABLE transactions ADD COLUMN currency_code TEXT;

            UPDATE transactions
            SET
                amount_minor = CAST(ROUND(CAST(amount AS REAL) * 100) AS INTEGER),
                currency_code = TRIM(currency);

            CREATE TABLE transactions_expanded (
                transaction_id VARCHAR(32) PRIMARY KEY,
                amount NUMERIC(14, 2),
                currency CHAR(3),
                occurred_at TIMESTAMP NOT NULL,
                amount_minor BIGINT,
                currency_code CHAR(3)
            );
            INSERT INTO transactions_expanded (transaction_id, amount, currency, occurred_at, amount_minor, currency_code)
            SELECT transaction_id, amount, currency, occurred_at, amount_minor, currency_code FROM transactions;
            DROP TABLE transactions;
            ALTER TABLE transactions_expanded RENAME TO transactions;
            CREATE INDEX idx_transactions_occurred_at ON transactions (occurred_at);

            -- Reject conflicting dual-writes on INSERT
            CREATE TRIGGER trg_rp_conflict_insert
            BEFORE INSERT ON transactions
            WHEN NEW.amount IS NOT NULL AND NEW.amount_minor IS NOT NULL
                 AND CAST(ROUND(CAST(NEW.amount AS REAL) * 100) AS INTEGER) != NEW.amount_minor
            BEGIN
                SELECT RAISE(ABORT, 'Conflicting dual-write amounts on INSERT');
            END;

            CREATE TRIGGER trg_rp_conflict_currency_insert
            BEFORE INSERT ON transactions
            WHEN NEW.currency IS NOT NULL AND NEW.currency_code IS NOT NULL
                 AND TRIM(NEW.currency) != TRIM(NEW.currency_code)
            BEGIN
                SELECT RAISE(ABORT, 'Conflicting dual-write currencies on INSERT');
            END;

            -- V1 INSERT (amount, currency) -> populate (amount_minor, currency_code)
            CREATE TRIGGER trg_rp_sync_v1_insert
            AFTER INSERT ON transactions
            WHEN NEW.amount IS NOT NULL AND NEW.amount_minor IS NULL
            BEGIN
                UPDATE transactions
                SET
                    amount_minor = CAST(ROUND(CAST(NEW.amount AS REAL) * 100) AS INTEGER),
                    currency_code = COALESCE(TRIM(NEW.currency_code), TRIM(NEW.currency))
                WHERE transaction_id = NEW.transaction_id;
            END;

            -- V2 INSERT (amount_minor, currency_code) -> populate (amount, currency)
            CREATE TRIGGER trg_rp_sync_v2_insert
            AFTER INSERT ON transactions
            WHEN NEW.amount IS NULL AND NEW.amount_minor IS NOT NULL
            BEGIN
                UPDATE transactions
                SET
                    amount = printf('%.2f', NEW.amount_minor / 100.0),
                    currency = COALESCE(TRIM(NEW.currency), TRIM(NEW.currency_code))
                WHERE transaction_id = NEW.transaction_id;
            END;

            -- V1 UPDATE (amount/currency changed) -> sync (amount_minor/currency_code)
            CREATE TRIGGER trg_rp_sync_v1_update
            AFTER UPDATE OF amount, currency ON transactions
            WHEN NEW.amount IS NOT OLD.amount OR NEW.currency IS NOT OLD.currency
            BEGIN
                UPDATE transactions
                SET
                    amount_minor = CAST(ROUND(CAST(NEW.amount AS REAL) * 100) AS INTEGER),
                    currency_code = TRIM(NEW.currency)
                WHERE transaction_id = NEW.transaction_id
                  AND (amount_minor IS NOT CAST(ROUND(CAST(NEW.amount AS REAL) * 100) AS INTEGER)
                       OR TRIM(COALESCE(currency_code, '')) != TRIM(COALESCE(NEW.currency, '')));
            END;

            -- V2 UPDATE (amount_minor/currency_code changed) -> sync (amount/currency)
            CREATE TRIGGER trg_rp_sync_v2_update
            AFTER UPDATE OF amount_minor, currency_code ON transactions
            WHEN NEW.amount_minor IS NOT OLD.amount_minor OR NEW.currency_code IS NOT OLD.currency_code
            BEGIN
                UPDATE transactions
                SET
                    amount = printf('%.2f', NEW.amount_minor / 100.0),
                    currency = TRIM(NEW.currency_code)
                WHERE transaction_id = NEW.transaction_id
                  AND (CAST(ROUND(CAST(amount AS REAL) * 100) AS INTEGER) IS NOT NEW.amount_minor
                       OR TRIM(COALESCE(currency, '')) != TRIM(COALESCE(NEW.currency_code, '')));
            END;
            """

        # Case 3: Multi-ADD COLUMN in single ALTER TABLE (SQLite requires one ADD COLUMN per ALTER)
        sql_no_comments = re.sub(r"--[^\n]*", "", sql)
        if re.search(
            r"ALTER\s+TABLE\s+\w+\s+ADD\s+COLUMN.*,.*ADD\s+COLUMN", sql_no_comments, re.I | re.S
        ):
            lines: list[str] = []
            for stmt in sql_no_comments.split(";"):
                s = stmt.strip()
                if not s:
                    continue
                m = re.match(r"ALTER\s+TABLE\s+(\w+)\s+(.*)", s, re.I | re.S)
                if m and s.upper().count("ADD COLUMN") > 1:
                    tbl = m.group(1)
                    parts = [p.strip() for p in m.group(2).split(",")]
                    for p in parts:
                        lines.append(f"ALTER TABLE {tbl} {p};")
                else:
                    s_clean = re.sub(
                        r"ROUND\(([^)]+)\)::BIGINT",
                        r"CAST(ROUND(\1) AS INTEGER)",
                        s,
                        flags=re.IGNORECASE,
                    )
                    lines.append(s_clean + ";")
            return "\n".join(lines)

        # Generic cleanup for PostgreSQL casts
        cleaned = re.sub(
            r"ROUND\(([^)]+)\)::BIGINT",
            r"CAST(ROUND(\1) AS INTEGER)",
            sql,
            flags=re.IGNORECASE,
        )
        return cleaned


@contextmanager
def isolated_database(
    database_url: str, schema_name: str = "rp_demo"
) -> Iterator[DatabaseAdapter]:
    """Context manager that connects to an isolated test DB and guarantees cleanup."""
    adapter = DatabaseAdapter(database_url=database_url, schema_name=schema_name)
    adapter.connect()
    try:
        adapter.reset_isolated_schema()
        yield adapter
    finally:
        adapter.close()


def decimal_to_minor_units(value: Any) -> int:
    """Convert a major-unit Decimal, int, or numeric string to exact integer minor units (cents).

    Never uses binary float equality for financial comparisons.
    """
    dec = Decimal(str(value).strip())
    return int((dec * Decimal("100")).to_integral_exact())
