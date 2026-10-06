"""8-digit, customer-facing support ticket numbers — distinct from a
ticket's `session_id` (the underlying AST run/thread id used for audit and
LangGraph checkpointing). Stored as a `ticket_id` field on the ticket
document, not as its store key, since the run's session_id already exists
before we know whether this request will even escalate into a ticket.

Collision odds across the 90M possible 8-digit values are negligible at
demo scale; this still checks recent tickets defensively rather than
trusting that alone. A production deployment would instead rely on a
unique index in Mongo and retry on a duplicate-key error.
"""

from __future__ import annotations

import secrets

from app.tickets.store import TicketStore

_MIN = 10_000_000
_MAX = 99_999_999
_MAX_ATTEMPTS = 20
_RECENT_TICKETS_TO_CHECK = 500


async def generate_unique_ticket_id(ticket_store: TicketStore) -> str:
    recent = await ticket_store.list_tickets(limit=_RECENT_TICKETS_TO_CHECK)
    taken = {t.get("ticket_id") for t in recent if t.get("ticket_id")}
    for _ in range(_MAX_ATTEMPTS):
        candidate = str(secrets.randbelow(_MAX - _MIN + 1) + _MIN)
        if candidate not in taken:
            return candidate
    raise RuntimeError("could not generate a unique 8-digit ticket id after 20 attempts")
