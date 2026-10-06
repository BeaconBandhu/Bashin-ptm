"""Fully simulated bank backend: deterministic in-memory balances, no real
money, no real credentials. Implements integrations.protocols.BankClient.

Account ids equal customer ids 1:1 for Phase 0 (one account per customer) —
this keeps the entitlement guardrail's check trivial and legible; a real
integration would resolve account ownership via the bank's own API instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

TIMEOUT_TEST_ACCOUNT = "acct-timeout-test"


@dataclass(frozen=True, slots=True)
class FreezeRecord:
    reason: str
    frozen_at: str


class MockBankClient:
    def __init__(self, seed_balances: dict[str, Decimal] | None = None) -> None:
        self._balances: dict[str, Decimal] = seed_balances or {}
        self._currencies: dict[str, str] = {k: "INR" for k in self._balances}
        self._daily_spent: dict[str, Decimal] = {}
        # The cross-domain wire: a freeze raised from a support conversation
        # (customer_support domain's take_protective_action node) must
        # actually be checked by the payment domain's guardrails — otherwise
        # "we froze the account" doesn't really stop anything. Both domains
        # share this same MockBankClient instance (see app/api/main.py).
        self._frozen: dict[str, FreezeRecord] = {}

    def seed_account(self, account_id: str, balance: Decimal, currency: str = "INR") -> None:
        self._balances[account_id] = balance
        self._currencies[account_id] = currency

    async def get_balance(self, account_id: str) -> tuple[Decimal, str]:
        if account_id == TIMEOUT_TEST_ACCOUNT:
            raise TimeoutError(f"mock bank timed out fetching balance for {account_id}")
        if account_id not in self._balances:
            raise LookupError(f"no such account: {account_id}")
        return self._balances[account_id], self._currencies.get(account_id, "INR")

    async def get_daily_spent(self, account_id: str) -> Decimal:
        return self._daily_spent.get(account_id, Decimal(0))

    def record_spend(self, account_id: str, amount: Decimal) -> None:
        self._balances[account_id] -= amount
        self._daily_spent[account_id] = self._daily_spent.get(account_id, Decimal(0)) + amount

    async def freeze_account(self, account_id: str, reason: str) -> FreezeRecord:
        record = FreezeRecord(reason=reason, frozen_at=datetime.now(UTC).isoformat())
        self._frozen[account_id] = record
        return record

    async def unfreeze_account(self, account_id: str) -> None:
        self._frozen.pop(account_id, None)

    async def is_frozen(self, account_id: str) -> FreezeRecord | None:
        return self._frozen.get(account_id)
