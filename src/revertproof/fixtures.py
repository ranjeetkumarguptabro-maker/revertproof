"""Deterministic synthetic fixtures for Banking (1,000 transactions) and E-Commerce (orders).

No real customer, bank account, or production data is ever used.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal


@dataclass(frozen=True)
class BankingTransaction:
    transaction_id: str
    amount: Decimal
    currency: str
    occurred_at: str

    @property
    def amount_minor(self) -> int:
        """Exact integer minor units (cents) — never uses binary float."""
        return int((self.amount * Decimal("100")).to_integral_exact())


@dataclass(frozen=True)
class EcommerceOrder:
    order_id: str
    customer_email: str
    total_price: Decimal
    currency: str
    status: str
    shipping_address: str
    created_at: str

    @property
    def total_price_minor(self) -> int:
        return int((self.total_price * Decimal("100")).to_integral_exact())


def generate_banking_transactions(count: int = 1000) -> list[BankingTransaction]:
    """Generate `count` deterministic synthetic banking transactions.

    Includes blueprint canonical fixtures (TX0001 = 125.50 EUR, TX0002 = 80.00 EUR,
    TX0003 = 250.75 EUR) plus edge cases (0.01, 0.99, 99999.99, multi-currency EUR/USD/GBP).
    """
    base_time = datetime(2026, 11, 1, 9, 0, 0)
    canonical: list[tuple[Decimal, str]] = [
        (Decimal("125.50"), "EUR"),
        (Decimal("80.00"), "EUR"),
        (Decimal("250.75"), "EUR"),
        (Decimal("0.01"), "EUR"),
        (Decimal("99999.99"), "EUR"),
        (Decimal("19.95"), "USD"),
        (Decimal("420.10"), "GBP"),
    ]
    currencies = ("EUR", "EUR", "EUR", "USD", "GBP")
    rows: list[BankingTransaction] = []

    for idx in range(1, count + 1):
        tx_id = f"TX{idx:04d}"
        ts = (base_time + timedelta(minutes=idx * 7)).strftime("%Y-%m-%d %H:%M:%S")
        if idx <= len(canonical):
            amt, cur = canonical[idx - 1]
        else:
            # Deterministic cents value avoiding binary float artifacts
            cents = ((idx * 7919) % 250_000) + 1
            amt = (Decimal(cents) / Decimal("100")).quantize(Decimal("0.01"))
            cur = currencies[idx % len(currencies)]
        rows.append(
            BankingTransaction(
                transaction_id=tx_id,
                amount=amt,
                currency=cur,
                occurred_at=ts,
            )
        )
    return rows


def generate_ecommerce_orders(count: int = 200) -> list[EcommerceOrder]:
    """Generate `count` deterministic synthetic e-commerce shopping site orders."""
    base_time = datetime(2026, 11, 1, 10, 0, 0)
    statuses = ("PENDING", "PAID", "SHIPPED", "DELIVERED")
    currencies = ("EUR", "USD", "GBP")
    cities = ("Berlin", "Dublin", "Amsterdam", "London", "Paris")
    orders: list[EcommerceOrder] = []

    for idx in range(1, count + 1):
        order_id = f"ORD{idx:04d}"
        cents = ((idx * 3571) % 150_000) + 100
        price = (Decimal(cents) / Decimal("100")).quantize(Decimal("0.01"))
        orders.append(
            EcommerceOrder(
                order_id=order_id,
                customer_email=f"shopper{idx:04d}@synthetic.example.test",
                total_price=price,
                currency=currencies[idx % len(currencies)],
                status=statuses[idx % len(statuses)],
                shipping_address=f"{idx} Market Street, {cities[idx % len(cities)]}",
                created_at=(base_time + timedelta(minutes=idx * 11)).strftime("%Y-%m-%d %H:%M:%S"),
            )
        )
    return orders
