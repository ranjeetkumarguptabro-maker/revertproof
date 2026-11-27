"""Unit & reconciliation tests for Module 3: Transaction Integrity Checker."""

from __future__ import annotations

from revertproof.fixtures import generate_banking_transactions
from revertproof.integrity import verify_banking_records
from revertproof.models import CheckStatus


def _to_rows(fixtures):
    return [
        {
            "transaction_id": tx.transaction_id,
            "amount": str(tx.amount),
            "currency": tx.currency,
            "occurred_at": tx.occurred_at,
            "amount_minor": tx.amount_minor,
            "currency_code": tx.currency,
        }
        for tx in fixtures
    ]


def test_integrity_passes_on_exact_1000_transactions() -> None:
    fixtures = generate_banking_transactions(1000)
    report = verify_banking_records(fixtures, _to_rows(fixtures))
    assert report.status == CheckStatus.PASS
    assert report.records_preserved == 1000
    assert report.mismatches == []


def test_integrity_catches_amount_conversion_error() -> None:
    fixtures = generate_banking_transactions(10)
    rows = _to_rows(fixtures)
    # Blueprint example: EUR 125.50 becomes 12505 minor units instead of 12550
    rows[0]["amount_minor"] = 12505
    report = verify_banking_records(fixtures, rows)
    assert report.status == CheckStatus.FAIL
    assert report.amount_mismatches == 1
    assert any(m.record_id == "TX0001" and m.invariant == "AMOUNT" for m in report.mismatches)


def test_integrity_catches_missing_transaction() -> None:
    fixtures = generate_banking_transactions(10)
    rows = [r for r in _to_rows(fixtures) if r["transaction_id"] != "TX0002"]
    report = verify_banking_records(fixtures, rows)
    assert report.status == CheckStatus.FAIL
    assert "TX0002" in report.missing_ids


def test_integrity_catches_duplicate_transaction() -> None:
    fixtures = generate_banking_transactions(10)
    rows = _to_rows(fixtures)
    rows.append(dict(rows[0]))  # Duplicate TX0001
    report = verify_banking_records(fixtures, rows)
    assert report.status == CheckStatus.FAIL
    assert "TX0001" in report.duplicate_ids


def test_integrity_catches_currency_mismatch() -> None:
    fixtures = generate_banking_transactions(10)
    rows = _to_rows(fixtures)
    rows[1]["currency_code"] = "USD"  # TX0002 was EUR
    report = verify_banking_records(fixtures, rows)
    assert report.status == CheckStatus.FAIL
    assert report.currency_mismatches == 1


def test_integrity_catches_null_value_violation() -> None:
    fixtures = generate_banking_transactions(10)
    rows = _to_rows(fixtures)
    rows[2]["currency_code"] = None
    report = verify_banking_records(fixtures, rows)
    assert report.status == CheckStatus.FAIL
    assert report.null_violations >= 1
