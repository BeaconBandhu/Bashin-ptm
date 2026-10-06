"""Tracks real cumulative $ spend per LLM provider, computed from actual
token usage (see app/llm/pricing.py) — not an estimate. This is what the
ai_spend_limit guardrail (app/domains/support/guardrails.py) checks before
every Council call, and it's the only stateful, I/O-performing guardrail
check in this codebase — a deliberate, documented exception to the "every
guardrail is a pure function" rule (see app/guardrails/base.py's
docstring): the whole point of a spend cap is knowing the REAL current
total at decision time, right before the call it might block.

MongoDB-backed when available so the running total survives a server
restart — important given how small the budget is; forgetting prior spend
on every restart would defeat the guardrail. Falls back to in-memory
otherwise, same pattern as every other store here.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Protocol

from motor.motor_asyncio import AsyncIOMotorClient

SPEND_COLLECTION = "ai_spend"


class SpendTracker(Protocol):
    async def record(self, provider: str, cost_usd: float) -> None: ...
    async def total(self, provider: str) -> float: ...


class InMemorySpendTracker:
    def __init__(self) -> None:
        self._totals: dict[str, float] = defaultdict(float)
        self._lock = asyncio.Lock()

    async def record(self, provider: str, cost_usd: float) -> None:
        async with self._lock:
            self._totals[provider] += cost_usd

    async def total(self, provider: str) -> float:
        return self._totals[provider]


class MongoSpendTracker:
    def __init__(self, uri: str, db_name: str) -> None:
        self._client: AsyncIOMotorClient = AsyncIOMotorClient(uri)
        self._collection = self._client[db_name][SPEND_COLLECTION]

    async def record(self, provider: str, cost_usd: float) -> None:
        # Atomic $inc — safe under concurrent requests, unlike a
        # read-then-write pattern which could lose increments.
        await self._collection.update_one(
            {"_id": provider}, {"$inc": {"total_usd": cost_usd}}, upsert=True
        )

    async def total(self, provider: str) -> float:
        doc = await self._collection.find_one({"_id": provider})
        return doc["total_usd"] if doc else 0.0

    def close(self) -> None:
        self._client.close()
