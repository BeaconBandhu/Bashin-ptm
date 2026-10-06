"""Fully simulated CRM: payee verification lookups only, sufficient for the
payment-authorization flow. Implements integrations.protocols.CRMClient."""

from __future__ import annotations


class MockCRMClient:
    def __init__(self, verified_payees: set[tuple[str, str]] | None = None) -> None:
        # (account_id, payee_id) pairs considered pre-verified.
        self._verified: set[tuple[str, str]] = verified_payees or set()

    def verify_payee(self, account_id: str, payee_id: str) -> None:
        self._verified.add((account_id, payee_id))

    async def is_payee_verified(self, account_id: str, payee_id: str) -> bool:
        return (account_id, payee_id) in self._verified
