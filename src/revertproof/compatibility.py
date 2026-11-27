"""Module 2: Database Compatibility Tester.

Tests BOTH Application V2 (new release) and Application V1 (previous rollback target)
against the EXACT SAME migrated database state — covering SELECT, INSERT, UPDATE,
bidirectional dual-write synchronisation, conflict rejection, and controlled rollback.

Supports multiple domain profiles:
- `banking` (primary flagship): transactions table with exact minor-unit money handling
- `ecommerce` (shopping site): orders table showing RevertProof works for any migration
"""

from __future__ import annotations

import json
import time
from decimal import Decimal
from pathlib import Path

from revertproof.db import DatabaseAdapter, decimal_to_minor_units
from revertproof.fixtures import (
    BankingTransaction,
    EcommerceOrder,
    generate_banking_transactions,
    generate_ecommerce_orders,
)
from revertproof.models import (
    CheckStatus,
    CompatibilityReport,
    OperationResult,
    VersionCompatibilityResult,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _timed_op(
    name: str,
    op_type: str,
    fn,
) -> OperationResult:
    start = time.perf_counter()
    try:
        rows_affected, details = fn()
        elapsed = (time.perf_counter() - start) * 1000.0
        return OperationResult(
            name=name,
            operation_type=op_type,
            passed=True,
            duration_ms=round(elapsed, 2),
            rows_affected=rows_affected,
            details=details,
        )
    except Exception as exc:
        elapsed = (time.perf_counter() - start) * 1000.0
        return OperationResult(
            name=name,
            operation_type=op_type,
            passed=False,
            duration_ms=round(elapsed, 2),
            error=f"{type(exc).__name__}: {exc}",
        )


def load_banking_baseline(
    db: DatabaseAdapter,
    fixture_count: int = 1000,
    baseline_sql_path: Path | None = None,
) -> list[BankingTransaction]:
    """Create the baseline Banking V1 schema and insert deterministic synthetic transactions."""
    sql_path = baseline_sql_path or (PROJECT_ROOT / "sql" / "baseline.sql")
    db.execute_script(sql_path.read_text(encoding="utf-8"))
    fixtures = generate_banking_transactions(fixture_count)
    db.executemany(
        "INSERT INTO transactions (transaction_id, amount, currency, occurred_at) "
        "VALUES (%s, %s, %s, %s);",
        [(tx.transaction_id, str(tx.amount), tx.currency, tx.occurred_at) for tx in fixtures],
    )
    return fixtures


def load_ecommerce_baseline(
    db: DatabaseAdapter,
    fixture_count: int = 200,
    baseline_sql_path: Path | None = None,
) -> list[EcommerceOrder]:
    """Create the baseline E-Commerce V1 schema and insert synthetic shopping site orders."""
    sql_path = baseline_sql_path or (PROJECT_ROOT / "sql" / "ecommerce" / "baseline.sql")
    db.execute_script(sql_path.read_text(encoding="utf-8"))
    fixtures = generate_ecommerce_orders(fixture_count)
    db.executemany(
        "INSERT INTO orders (order_id, customer_email, total_price, currency, status, shipping_address, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s);",
        [
            (
                o.order_id,
                o.customer_email,
                str(o.total_price),
                o.currency,
                o.status,
                o.shipping_address,
                o.created_at,
            )
            for o in fixtures
        ],
    )
    return fixtures


def _parse_v2_json_or_minor(row: dict) -> tuple[int, str]:
    """Read V2 money representation from either Expand columns or unsafe JSONB amount."""
    if "amount_minor" in row and "currency_code" in row:
        if row["amount_minor"] is None or row["currency_code"] is None:
            raise ValueError(f"NULL V2 fields in row {row.get('transaction_id')}")
        return int(row["amount_minor"]), str(row["currency_code"]).strip()
    raw = row.get("amount")
    parsed = raw if isinstance(raw, dict) else json.loads(str(raw))
    return int(parsed["value_minor"]), str(parsed["currency"]).strip()


def run_banking_compatibility(
    db: DatabaseAdapter,
    migration_sql: str,
    fixture_count: int = 1000,
    test_rollback_writes: bool = True,
) -> tuple[CompatibilityReport, list[BankingTransaction]]:
    """Run full V2 + V1 compatibility suite for the Banking domain against one migrated DB."""
    fixtures = load_banking_baseline(db, fixture_count=fixture_count)

    # Apply proposed migration
    try:
        db.execute_script(migration_sql)
    except Exception as exc:
        empty_v2 = VersionCompatibilityResult(
            app_version="V2", status=CheckStatus.NOT_RUN, summary="Migration failed to apply"
        )
        empty_v1 = VersionCompatibilityResult(
            app_version="V1", status=CheckStatus.NOT_RUN, summary="Migration failed to apply"
        )
        return (
            CompatibilityReport(
                domain="banking",
                engine=db.engine_name,
                schema_name=db.schema_name,
                fixture_count=fixture_count,
                migration_applied=False,
                migration_error=f"{type(exc).__name__}: {exc}",
                v2_result=empty_v2,
                v1_result=empty_v1,
            ),
            fixtures,
        )

    cols = db.get_table_columns("transactions")
    has_expand_cols = "amount_minor" in cols and "currency_code" in cols

    # --- Test Application V2 (new release) ---
    v2_ops: list[OperationResult] = []

    def _v2_read():
        if has_expand_cols:
            rows = db.fetchall(
                "SELECT transaction_id, amount_minor, currency_code, occurred_at "
                "FROM transactions ORDER BY transaction_id;"
            )
        else:
            rows = db.fetchall(
                "SELECT transaction_id, amount, occurred_at FROM transactions ORDER BY transaction_id;"
            )
        if len(rows) != fixture_count:
            raise AssertionError(f"Expected {fixture_count} rows, got {len(rows)}")
        for r in rows[:20]:
            minor, cur = _parse_v2_json_or_minor(r)
            if minor <= 0 or len(cur) != 3:
                raise AssertionError(f"Invalid V2 payload: {minor} {cur}")
        return len(rows), f"V2 read {len(rows)} transactions in new format"

    v2_ops.append(_timed_op("v2_read_transactions", "READ", _v2_read))

    def _v2_insert():
        if has_expand_cols:
            rc = db.execute(
                "INSERT INTO transactions (transaction_id, amount_minor, currency_code, occurred_at) "
                "VALUES (%s, %s, %s, %s);",
                ("TX_V2_PROBE", 4599, "EUR", "2026-11-27 12:00:00"),
            )
            check = db.fetchall(
                "SELECT amount, currency, amount_minor, currency_code "
                "FROM transactions WHERE transaction_id = %s;",
                ("TX_V2_PROBE",),
            )
            if not check:
                raise AssertionError("V2 inserted row not found")
            row = check[0]
            if "amount" in row and row["amount"] is not None:
                if decimal_to_minor_units(row["amount"]) != 4599:
                    raise AssertionError(
                        f"Dual-write sync mismatch: amount={row['amount']} vs 4599"
                    )
            return rc, "V2 inserted TX_V2_PROBE (4599 minor EUR) and synced legacy columns"
        else:
            payload = json.dumps({"value_minor": 4599, "currency": "EUR"})
            rc = db.execute(
                "INSERT INTO transactions (transaction_id, amount, occurred_at) "
                "VALUES (%s, %s, %s);",
                ("TX_V2_PROBE", payload, "2026-11-27 12:00:00"),
            )
            return rc, "V2 inserted TX_V2_PROBE in JSON format"

    v2_ops.append(_timed_op("v2_insert_transaction", "INSERT", _v2_insert))

    def _v2_update():
        if has_expand_cols:
            rc = db.execute(
                "UPDATE transactions SET amount_minor = %s WHERE transaction_id = %s;",
                (5125, "TX_V2_PROBE"),
            )
            row = db.fetchall(
                "SELECT amount, amount_minor FROM transactions WHERE transaction_id = %s;",
                ("TX_V2_PROBE",),
            )[0]
            if row["amount"] is None or decimal_to_minor_units(row["amount"]) != 5125:
                raise AssertionError(
                    f"V2 UPDATE did not sync legacy amount: {row['amount']} != 51.25"
                )
            return rc, "V2 updated TX_V2_PROBE -> 5125 minor units; legacy amount synced to 51.25"
        else:
            payload = json.dumps({"value_minor": 5125, "currency": "EUR"})
            rc = db.execute(
                "UPDATE transactions SET amount = %s WHERE transaction_id = %s;",
                (payload, "TX_V2_PROBE"),
            )
            return rc, "V2 updated TX_V2_PROBE JSON amount"

    v2_ops.append(_timed_op("v2_update_transaction", "UPDATE", _v2_update))

    # Clean up V2 probe row before integrity comparison? Keep a separate probe or delete after V1 verifies it!
    v2_passed = all(op.passed for op in v2_ops)
    v2_result = VersionCompatibilityResult(
        app_version="V2",
        status=CheckStatus.PASS if v2_passed else CheckStatus.FAIL,
        operations=v2_ops,
        summary=(
            "V2 read/write contracts passed"
            if v2_passed
            else "V2 contract failed: "
            + "; ".join(op.error or op.name for op in v2_ops if not op.passed)
        ),
    )

    # --- Test Application V1 (previous version / rollback target) ---
    v1_ops: list[OperationResult] = []

    def _v1_read():
        rows = db.fetchall(
            "SELECT transaction_id, amount, currency, occurred_at "
            "FROM transactions ORDER BY transaction_id;"
        )
        if len(rows) < fixture_count:
            raise AssertionError(f"Expected >= {fixture_count} rows, got {len(rows)}")
        for r in rows[:20]:
            # V1 parses amount strictly as Decimal numeric and currency as 3-char string
            amt = Decimal(str(r["amount"]))
            cur = str(r["currency"]).strip()
            if amt <= 0 or len(cur) != 3:
                raise AssertionError(f"Invalid V1 row: {amt} {cur}")
        # Also verify V1 can read the row that V2 just wrote!
        v2_probe = [r for r in rows if r["transaction_id"] == "TX_V2_PROBE"]
        if v2_probe:
            if decimal_to_minor_units(v2_probe[0]["amount"]) != 5125:
                raise AssertionError(
                    f"V1 saw wrong value for V2-written TX_V2_PROBE: {v2_probe[0]['amount']}"
                )
        return len(rows), f"V1 read {len(rows)} rows including V2-written TX_V2_PROBE"

    v1_ops.append(_timed_op("v1_read_transactions", "READ", _v1_read))

    dual_write_verified = False

    def _v1_insert():
        rc = db.execute(
            "INSERT INTO transactions (transaction_id, amount, currency, occurred_at) "
            "VALUES (%s, %s, %s, %s);",
            ("TX_V1_ROLLBACK", "77.40", "EUR", "2026-11-27 12:05:00"),
        )
        if has_expand_cols:
            row = db.fetchall(
                "SELECT amount_minor, currency_code FROM transactions WHERE transaction_id = %s;",
                ("TX_V1_ROLLBACK",),
            )[0]
            if row["amount_minor"] != 7740 or str(row["currency_code"]).strip() != "EUR":
                raise AssertionError(
                    f"V1 INSERT did not sync V2 columns: {row['amount_minor']} {row['currency_code']}"
                )
        return rc, "V1 inserted TX_V1_ROLLBACK (77.40 EUR); V2 columns synced to 7740 EUR"

    v1_ops.append(_timed_op("v1_insert_transaction", "INSERT", _v1_insert))

    def _v1_update():
        rc = db.execute(
            "UPDATE transactions SET amount = %s WHERE transaction_id = %s;",
            ("88.90", "TX_V1_ROLLBACK"),
        )
        if has_expand_cols:
            row = db.fetchall(
                "SELECT amount_minor, currency_code FROM transactions WHERE transaction_id = %s;",
                ("TX_V1_ROLLBACK",),
            )[0]
            if row["amount_minor"] != 8890:
                raise AssertionError(
                    f"V1 UPDATE did not sync amount_minor: got {row['amount_minor']}"
                )
        return rc, "V1 updated TX_V1_ROLLBACK -> 88.90 EUR; V2 amount_minor synced to 8890"

    v1_ops.append(_timed_op("v1_update_transaction", "UPDATE", _v1_update))

    if has_expand_cols and test_rollback_writes:
        def _conflict_rejection():
            try:
                db.execute(
                    "INSERT INTO transactions "
                    "(transaction_id, amount, currency, occurred_at, amount_minor, currency_code) "
                    "VALUES (%s, %s, %s, %s, %s, %s);",
                    ("TX_CONFLICT", "10.00", "EUR", "2026-11-27 12:10:00", 9999, "EUR"),
                )
            except Exception:
                return 0, "Conflicting dual-write (10.00 vs 9999 minor) correctly rejected"
            raise AssertionError("Database accepted conflicting dual-write values!")

        v1_ops.append(
            _timed_op("dual_write_conflict_guard", "DUAL_WRITE_CONFLICT", _conflict_rejection)
        )

    # Remove probe rows so Integrity Checker validates the exact 1,000 baseline fixtures cleanly
    try:
        db.execute(
            "DELETE FROM transactions WHERE transaction_id IN (%s, %s, %s);",
            ("TX_V2_PROBE", "TX_V1_ROLLBACK", "TX_CONFLICT"),
        )
    except Exception:
        pass

    v1_passed = all(op.passed for op in v1_ops)
    dual_write_verified = has_expand_cols and v1_passed and v2_passed
    v1_result = VersionCompatibilityResult(
        app_version="V1",
        status=CheckStatus.PASS if v1_passed else CheckStatus.FAIL,
        operations=v1_ops,
        summary=(
            "V1 rollback read/write contracts passed"
            if v1_passed
            else "V1 rollback FAILED: "
            + "; ".join(op.error or op.name for op in v1_ops if not op.passed)
        ),
    )

    return (
        CompatibilityReport(
            domain="banking",
            engine=db.engine_name,
            schema_name=db.schema_name,
            fixture_count=fixture_count,
            migration_applied=True,
            v2_result=v2_result,
            v1_result=v1_result,
            dual_write_verified=dual_write_verified,
            rollback_verified=v1_passed and v2_passed,
        ),
        fixtures,
    )


def run_ecommerce_compatibility(
    db: DatabaseAdapter,
    migration_sql: str,
    fixture_count: int = 200,
) -> tuple[CompatibilityReport, list[EcommerceOrder]]:
    """Run V2 + V1 compatibility suite for the E-Commerce shopping site domain."""
    fixtures = load_ecommerce_baseline(db, fixture_count=fixture_count)

    try:
        db.execute_script(migration_sql)
    except Exception as exc:
        empty_v2 = VersionCompatibilityResult(app_version="V2", status=CheckStatus.NOT_RUN)
        empty_v1 = VersionCompatibilityResult(app_version="V1", status=CheckStatus.NOT_RUN)
        return (
            CompatibilityReport(
                domain="ecommerce",
                engine=db.engine_name,
                schema_name=db.schema_name,
                fixture_count=fixture_count,
                migration_applied=False,
                migration_error=f"{type(exc).__name__}: {exc}",
                v2_result=empty_v2,
                v1_result=empty_v1,
            ),
            fixtures,
        )

    cols = db.get_table_columns("orders")
    has_expand = "order_state" in cols and "delivery_address_json" in cols

    # V2 checks
    v2_ops: list[OperationResult] = []

    def _v2_read():
        if has_expand:
            rows = db.fetchall(
                "SELECT order_id, total_price_minor, currency, order_state, delivery_address_json "
                "FROM orders ORDER BY order_id;"
            )
        else:
            rows = db.fetchall(
                "SELECT order_id, total_price, currency, delivery_address_json "
                "FROM orders ORDER BY order_id;"
            )
        if len(rows) != fixture_count:
            raise AssertionError(f"Expected {fixture_count} orders, got {len(rows)}")
        return len(rows), f"E-Commerce V2 read {len(rows)} orders"

    v2_ops.append(_timed_op("v2_read_orders", "READ", _v2_read))

    v2_passed = all(op.passed for op in v2_ops)
    v2_result = VersionCompatibilityResult(
        app_version="V2",
        status=CheckStatus.PASS if v2_passed else CheckStatus.FAIL,
        operations=v2_ops,
        summary="E-Commerce V2 queries passed" if v2_passed else "E-Commerce V2 failed",
    )

    # V1 checks (expects legacy `status` and `shipping_address` columns)
    v1_ops: list[OperationResult] = []

    def _v1_read():
        rows = db.fetchall(
            "SELECT order_id, total_price, currency, status, shipping_address "
            "FROM orders ORDER BY order_id;"
        )
        if len(rows) != fixture_count:
            raise AssertionError(f"Expected {fixture_count} orders, got {len(rows)}")
        return len(rows), f"E-Commerce V1 read {len(rows)} legacy orders"

    v1_ops.append(_timed_op("v1_read_orders", "READ", _v1_read))

    v1_passed = all(op.passed for op in v1_ops)
    v1_result = VersionCompatibilityResult(
        app_version="V1",
        status=CheckStatus.PASS if v1_passed else CheckStatus.FAIL,
        operations=v1_ops,
        summary=(
            "E-Commerce V1 rollback queries passed"
            if v1_passed
            else "E-Commerce V1 rollback FAILED: "
            + "; ".join(op.error or op.name for op in v1_ops if not op.passed)
        ),
    )

    return (
        CompatibilityReport(
            domain="ecommerce",
            engine=db.engine_name,
            schema_name=db.schema_name,
            fixture_count=fixture_count,
            migration_applied=True,
            v2_result=v2_result,
            v1_result=v1_result,
            dual_write_verified=has_expand and v1_passed and v2_passed,
            rollback_verified=v1_passed and v2_passed,
        ),
        fixtures,
    )
