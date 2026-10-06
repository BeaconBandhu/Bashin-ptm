"""App-level idempotency claims, checked immediately before the external
call — the first layer of defense-in-depth described in the architecture
plan (the mock/real payment rail's own duplicate-key rejection is the
second, closer to the actual side effect).

Phase 0 uses the in-memory implementation (fine for a single-process dev
server and for tests). Production swaps this for a Redis-backed store
(SETNX with a TTL) — same Protocol, no caller changes.
"""

from __future__ import annotations

import asyncio
from typing import Protocol


class IdempotencyStore(Protocol):
    async def claim(self, key: str) -> bool:
        """Atomically claims `key`. Returns True if this call newly claimed
        it, False if it was already claimed (i.e. this is a replay)."""
        ...


class InMemoryIdempotencyStore:
    def __init__(self) -> None:
        self._claimed: set[str] = set()
        self._lock = asyncio.Lock()

    async def claim(self, key: str) -> bool:
        async with self._lock:
            if key in self._claimed:
                return False
            self._claimed.add(key)
            return True


def derive_idempotency_key(session_id: str, template_slot_id: str, input_hash: str) -> str:
    """Deterministic key derivation so the *same* logical action, retried
    (e.g. after a client-side timeout), reuses the same key rather than
    minting a new one — the retry then correctly collides in the store."""
    return f"idem:{session_id}:{template_slot_id}:{input_hash}"
