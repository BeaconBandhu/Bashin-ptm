"""TicketStore: the human/dashboard-facing record of every request the
teammate has handled — the query, the final decision, the full governance
(guardrail) trail, and (from Phase 2 on) the council deliberation. This is
distinct from AuditStore (app/audit/store.py): AuditStore is the
tamper-evident, hash-chained node-by-node ledger; TicketStore is a
denormalized, queryable summary built FROM that ledger for the "how is the
money flowing" dashboard and the /v1/tickets API.

Two implementations, same Protocol — the pattern already established for
AuditStore/IdempotencyStore: MongoTicketStore (real, motor-backed) is the
production target; InMemoryTicketStore is the zero-infra local-dev/test
fallback. Selected in app/api/main.py based on Settings.has_mongodb.
"""

from __future__ import annotations

from typing import Any, Protocol


class TicketStore(Protocol):
    async def upsert_ticket(self, ticket: dict[str, Any]) -> None:
        """Replaces the ticket for ticket["session_id"] wholesale — a ticket
        is written once, after its plan finishes executing (Phase 0/1 are
        synchronous; Phase 2+'s streaming/human-escalation paths will call
        this again on each state transition)."""
        ...

    async def get_ticket(self, session_id: str) -> dict[str, Any] | None: ...

    async def list_tickets(self, limit: int = 50) -> list[dict[str, Any]]:
        """Most recent first."""
        ...
