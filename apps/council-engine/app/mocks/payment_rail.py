"""Fully simulated UPI/payment rail: no real money movement. Implements
integrations.protocols.PaymentRailClient, including the rail's OWN duplicate-
idempotency-key rejection (defense-in-depth alongside the app-level
IdempotencyStore — see app/concurrency/idempotency.py and the architecture
plan's idempotency section).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from app.integrations.protocols import DuplicatePaymentError, PaymentReceipt

TIMEOUT_TEST_PAYEE = "payee-timeout-test"


class MockPaymentRailClient:
    def __init__(self) -> None:
        self._receipts_by_key: dict[str, PaymentReceipt] = {}

    async def execute_payment(
        self, *, idempotency_key: str, account_id: str, payee_id: str, amount: Decimal, currency: str
    ) -> PaymentReceipt:
        if payee_id == TIMEOUT_TEST_PAYEE:
            raise TimeoutError(f"mock payment rail timed out executing payment to {payee_id}")
        if idempotency_key in self._receipts_by_key:
            raise DuplicatePaymentError(
                f"idempotency_key '{idempotency_key}' already used — refusing to pay twice"
            )
        receipt = PaymentReceipt(
            payment_id=f"pay_{uuid.uuid4().hex[:16]}",
            status="SETTLED",
            executed_at=datetime.now(UTC),
        )
        self._receipts_by_key[idempotency_key] = receipt
        return receipt
