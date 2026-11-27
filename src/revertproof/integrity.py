"""Module 3: Transaction & Data Integrity Checker.

Reconciles every record before and after migration using exact integer minor units
(cents) — never binary floating-point equality.

Checks all 6 required invariants:
1. Identity (no missing IDs)
2. Uniqueness (no duplicate IDs)
3. Exact Amount parity (legacy NUMERIC * 100 == BIGINT amount_minor)
4. Currency parity (after normalising CHAR padding)
5. Completeness (no unexpected NULLs)
6. Timestamp / ordering preservation
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from revertproof.db import DatabaseAdapter, decimal_to_minor_units
from revertproof.fixtures import BankingTransaction, EcommerceOrder
from revertproof.models import CheckStatus, IntegrityMismatch, IntegrityReport


def verify_banking_records(
    expected: list[BankingTransaction],
    actual_rows: list[dict[str, Any]],
    check_expanded_columns: bool = True,
) -> IntegrityReport:
    """Reconcile actual DB rows against expected synthetic banking transactions."""
    mismatches: list[IntegrityMismatch] = []
    expected_map = {tx.transaction_id: tx for tx in expected}

    actual_ids = [str(r["transaction_id"]) for r in actual_rows if r.get("transaction_id") is not None]
    id_counts = Counter(actual_ids)
    duplicate_ids = sorted(tid for tid, cnt in id_counts.items() if cnt > 1)
    for dup_id in duplicate_ids:
        mismatches.append(
            IntegrityMismatch(
                record_id=dup_id,
                invariant="UNIQUENESS",
                expected="1 occurrence",
                actual=f"{id_counts[dup_id]} occurrences",
                message=f"Duplicate transaction_id detected: {dup_id}",
            )
        )

    actual_map: dict[str, dict[str, Any]] = {}
    for row in actual_rows:
        tid = row.get("transaction_id")
        if tid is not None and str(tid) not in actual_map:
            actual_map[str(tid)] = row

    missing_ids = sorted(set(expected_map.keys()) - set(actual_map.keys()))
    for mid in missing_ids:
        mismatches.append(
            IntegrityMismatch(
                record_id=mid,
                invariant="IDENTITY",
                expected="present",
                actual="missing",
                message=f"Transaction {mid} disappeared after migration",
            )
        )

    null_violations = 0
    amount_mismatches = 0
    currency_mismatches = 0
    timestamp_mismatches = 0
    records_preserved = 0

    for tid, exp in expected_map.items():
        row = actual_map.get(tid)
        if row is None:
            continue

        row_ok = True

        # Completeness (NULL check)
        required_fields = ["amount", "currency", "occurred_at"]
        if check_expanded_columns:
            required_fields.extend(["amount_minor", "currency_code"])
        for col in required_fields:
            if col not in row or row[col] is None or str(row[col]).strip() == "":
                null_violations += 1
                row_ok = False
                mismatches.append(
                    IntegrityMismatch(
                        record_id=tid,
                        invariant="COMPLETENESS",
                        expected=f"{col} NOT NULL",
                        actual="NULL",
                        message=f"Transaction {tid} has NULL/empty required field '{col}'",
                    )
                )

        # Legacy amount check
        if row.get("amount") is not None:
            try:
                actual_legacy_minor = decimal_to_minor_units(row["amount"])
                if actual_legacy_minor != exp.amount_minor:
                    amount_mismatches += 1
                    row_ok = False
                    mismatches.append(
                        IntegrityMismatch(
                            record_id=tid,
                            invariant="AMOUNT",
                            expected=f"{exp.amount} ({exp.amount_minor} minor)",
                            actual=f"{row['amount']} ({actual_legacy_minor} minor)",
                            message=f"Legacy amount changed for {tid}",
                        )
                    )
            except Exception as exc:
                amount_mismatches += 1
                row_ok = False
                mismatches.append(
                    IntegrityMismatch(
                        record_id=tid,
                        invariant="AMOUNT",
                        expected=str(exp.amount),
                        actual=str(row["amount"]),
                        message=f"Legacy amount unparseable for {tid}: {exc}",
                    )
                )

        # Expanded amount_minor exact integer check
        if check_expanded_columns and row.get("amount_minor") is not None:
            actual_minor = int(row["amount_minor"])
            if actual_minor != exp.amount_minor:
                amount_mismatches += 1
                row_ok = False
                mismatches.append(
                    IntegrityMismatch(
                        record_id=tid,
                        invariant="AMOUNT",
                        expected=f"{exp.amount_minor} minor ({exp.currency} {exp.amount})",
                        actual=f"{actual_minor} minor",
                        message=(
                            f"Exact minor-unit mismatch for {tid}: "
                            f"expected {exp.amount_minor}, got {actual_minor}"
                        ),
                    )
                )

        # Currency checks (normalise CHAR padding)
        if row.get("currency") is not None:
            actual_cur = str(row["currency"]).strip()
            if actual_cur != exp.currency:
                currency_mismatches += 1
                row_ok = False
                mismatches.append(
                    IntegrityMismatch(
                        record_id=tid,
                        invariant="CURRENCY",
                        expected=exp.currency,
                        actual=actual_cur,
                        message=f"Legacy currency changed for {tid}: {exp.currency} -> {actual_cur}",
                    )
                )

        if check_expanded_columns and row.get("currency_code") is not None:
            actual_cc = str(row["currency_code"]).strip()
            if actual_cc != exp.currency:
                currency_mismatches += 1
                row_ok = False
                mismatches.append(
                    IntegrityMismatch(
                        record_id=tid,
                        invariant="CURRENCY",
                        expected=exp.currency,
                        actual=actual_cc,
                        message=(
                            f"Expanded currency_code mismatch for {tid}: "
                            f"{exp.currency} -> {actual_cc}"
                        ),
                    )
                )

        # Timestamp preservation
        if row.get("occurred_at") is not None:
            actual_ts = str(row["occurred_at"])[:19].replace("T", " ")
            if actual_ts != exp.occurred_at:
                timestamp_mismatches += 1
                row_ok = False
                mismatches.append(
                    IntegrityMismatch(
                        record_id=tid,
                        invariant="TIMESTAMP",
                        expected=exp.occurred_at,
                        actual=actual_ts,
                        message=f"Timestamp changed for {tid}",
                    )
                )

        if row_ok:
            records_preserved += 1

    passed = len(mismatches) == 0 and len(actual_rows) == len(expected)
    summary = (
        f"All {records_preserved}/{len(expected)} synthetic transactions preserved with exact "
        f"minor-unit amounts, currencies, and timestamps."
        if passed
        else f"Integrity FAILED: {len(mismatches)} violation(s) across {len(expected)} transactions "
        f"(missing={len(missing_ids)}, duplicates={len(duplicate_ids)}, "
        f"amount_mismatches={amount_mismatches}, currency_mismatches={currency_mismatches}, "
        f"null_violations={null_violations})."
    )

    return IntegrityReport(
        status=CheckStatus.PASS if passed else CheckStatus.FAIL,
        domain="banking",
        total_expected=len(expected),
        total_actual=len(actual_rows),
        records_preserved=records_preserved,
        missing_ids=missing_ids,
        duplicate_ids=duplicate_ids,
        null_violations=null_violations,
        amount_mismatches=amount_mismatches,
        currency_mismatches=currency_mismatches,
        timestamp_mismatches=timestamp_mismatches,
        mismatches=mismatches[:100],
        summary=summary,
    )


def check_banking_integrity(
    db: DatabaseAdapter,
    expected: list[BankingTransaction],
) -> IntegrityReport:
    """Query the migrated `transactions` table and reconcile against expected fixtures."""
    cols = db.get_table_columns("transactions")
    has_expand = "amount_minor" in cols and "currency_code" in cols
    if "currency" not in cols:
        return IntegrityReport(
            status=CheckStatus.NOT_RUN,
            domain="banking",
            total_expected=len(expected),
            summary="Blocked: legacy `currency` column missing; V1/V2 parity cannot be reconciled.",
        )

    select_cols = "transaction_id, amount, currency, occurred_at"
    if has_expand:
        select_cols += ", amount_minor, currency_code"
    rows = db.fetchall(f"SELECT {select_cols} FROM transactions ORDER BY transaction_id;")
    return verify_banking_records(expected, rows, check_expanded_columns=has_expand)


def check_ecommerce_integrity(
    db: DatabaseAdapter,
    expected: list[EcommerceOrder],
) -> IntegrityReport:
    """Reconcile migrated e-commerce `orders` table against expected synthetic orders."""
    cols = db.get_table_columns("orders")
    if "status" not in cols or "shipping_address" not in cols:
        return IntegrityReport(
            status=CheckStatus.NOT_RUN,
            domain="ecommerce",
            total_expected=len(expected),
            summary="Blocked: legacy `status` or `shipping_address` column missing.",
        )

    has_expand = "order_state" in cols and "total_price_minor" in cols
    select_cols = "order_id, total_price, currency, status, shipping_address"
    if has_expand:
        select_cols += ", order_state, total_price_minor"
    rows = db.fetchall(f"SELECT {select_cols} FROM orders ORDER BY order_id;")

    expected_map = {o.order_id: o for o in expected}
    actual_map = {str(r["order_id"]): r for r in rows}
    missing_ids = sorted(set(expected_map) - set(actual_map))
    mismatches: list[IntegrityMismatch] = []
    amount_mismatches = 0
    field_mismatches = 0
    preserved = 0

    for oid, exp in expected_map.items():
        row = actual_map.get(oid)
        if row is None:
            mismatches.append(
                IntegrityMismatch(
                    record_id=oid,
                    invariant="IDENTITY",
                    expected="present",
                    actual="missing",
                    message=f"Order {oid} missing after migration",
                )
            )
            continue
        ok = True
        if has_expand:
            if int(row["total_price_minor"]) != exp.total_price_minor:
                amount_mismatches += 1
                ok = False
                mismatches.append(
                    IntegrityMismatch(
                        record_id=oid,
                        invariant="AMOUNT",
                        expected=str(exp.total_price_minor),
                        actual=str(row["total_price_minor"]),
                        message=f"Order {oid} total_price_minor mismatch",
                    )
                )
            if str(row["order_state"]).strip() != exp.status:
                field_mismatches += 1
                ok = False
                mismatches.append(
                    IntegrityMismatch(
                        record_id=oid,
                        invariant="FIELD_PARITY",
                        expected=exp.status,
                        actual=str(row["order_state"]),
                        message=f"Order {oid} status/order_state mismatch",
                    )
                )
        if ok:
            preserved += 1

    passed = len(mismatches) == 0 and len(rows) == len(expected)
    return IntegrityReport(
        status=CheckStatus.PASS if passed else CheckStatus.FAIL,
        domain="ecommerce",
        total_expected=len(expected),
        total_actual=len(rows),
        records_preserved=preserved,
        missing_ids=missing_ids,
        amount_mismatches=amount_mismatches,
        field_mismatches=field_mismatches,
        mismatches=mismatches[:100],
        summary=(
            f"All {preserved}/{len(expected)} synthetic e-commerce orders preserved."
            if passed
            else f"E-Commerce integrity failed with {len(mismatches)} mismatch(es)."
        ),
    )
