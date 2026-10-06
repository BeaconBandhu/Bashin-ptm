"""Abstract interfaces for every external system the engine acts on.

Node code depends only on these Protocols, never on a concrete client. Phase
0 wires the `mocks/` implementations; swapping in a real bank/CRM/payment
sandbox later is a dependency-injection change (app/graph/builder.py), not a
rewrite of node logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol


class BankClient(Protocol):
    async def get_balance(self, account_id: str) -> tuple[Decimal, str]:
        """Returns (balance, currency). Raises TimeoutError / ConnectionError
        on transport failure so node code can distinguish retryable faults."""
        ...

    async def get_daily_spent(self, account_id: str) -> Decimal: ...

    async def freeze_account(self, account_id: str, reason: str) -> object:
        """Protective, reversible — the customer-support domain's
        take_protective_action node calls this; the payment domain's
        VerifyBalanceNode checks is_frozen() before allowing a payment."""
        ...

    async def is_frozen(self, account_id: str) -> object | None:
        """Returns a truthy freeze record if frozen, else None."""
        ...


class PaymentRailClient(Protocol):
    async def execute_payment(
        self, *, idempotency_key: str, account_id: str, payee_id: str, amount: Decimal, currency: str
    ) -> PaymentReceipt:
        """Must reject a reused idempotency_key (raises DuplicatePaymentError)
        — the rail's own contract, defense-in-depth alongside the app-level
        idempotency store."""
        ...


class CRMClient(Protocol):
    async def is_payee_verified(self, account_id: str, payee_id: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class PaymentReceipt:
    payment_id: str
    status: str
    executed_at: datetime


class DuplicatePaymentError(Exception):
    pass
