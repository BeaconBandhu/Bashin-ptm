"""Serializes concurrent actions against the same account so two distinct,
individually-valid payments can never together overdraw a balance (an
idempotency key alone only catches an exact replay, not two different
simultaneous debits — see the architecture plan's race-condition risk).

Production: `SELECT pg_advisory_xact_lock(hashtext($1))`, held for the
transaction spanning balance-check-through-execute. Phase 0 uses an
in-memory per-key asyncio.Lock — correct within one process, which is all a
single FastAPI instance needs; swap to the Postgres implementation the
moment this runs behind more than one instance.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol


class AdvisoryLock(Protocol):
    def lock(self, key: str) -> AsyncIterator[None]: ...


class InMemoryAdvisoryLock:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    @asynccontextmanager
    async def lock(self, key: str) -> AsyncIterator[None]:
        lock = self._locks[key]
        async with lock:
            yield
