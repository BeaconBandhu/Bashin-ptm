"""Zero-infra fallback for TicketStore — used whenever MONGODB_URI is unset
(local dev, tests). Not durable across process restarts; that trade-off is
exactly why app/tickets/mongo_store.py exists as the real implementation."""

from __future__ import annotations

import asyncio
from typing import Any


class InMemoryTicketStore:
    def __init__(self) -> None:
        self._tickets: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def upsert_ticket(self, ticket: dict[str, Any]) -> None:
        async with self._lock:
            self._tickets[ticket["session_id"]] = ticket

    async def get_ticket(self, session_id: str) -> dict[str, Any] | None:
        return self._tickets.get(session_id)

    async def list_tickets(self, limit: int = 50) -> list[dict[str, Any]]:
        ordered = sorted(self._tickets.values(), key=lambda t: t["created_at"], reverse=True)
        return ordered[:limit]
