"""Real MongoDB-backed TicketStore (motor, the official async driver).
Ticket documents are already fully JSON-safe (Decimal amounts and datetimes
are pre-serialized to strings by the caller — see app/api/main.py's
_build_ticket) so no BSON-specific encoding is needed here.

Keyed by session_id as Mongo's _id so upsert-by-session is a single
replace_one(upsert=True) — a ticket is a mutable, denormalized *summary* of
a session, unlike the immutable, hash-chained AuditStore events it's built
from.
"""

from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient

TICKETS_COLLECTION = "tickets"


class MongoTicketStore:
    def __init__(self, uri: str, db_name: str) -> None:
        self._client: AsyncIOMotorClient = AsyncIOMotorClient(uri)
        self._collection = self._client[db_name][TICKETS_COLLECTION]

    async def ensure_indexes(self) -> None:
        await self._collection.create_index("created_at")
        await self._collection.create_index("customer_id")

    async def upsert_ticket(self, ticket: dict[str, Any]) -> None:
        doc = {**ticket, "_id": ticket["session_id"]}
        await self._collection.replace_one({"_id": doc["_id"]}, doc, upsert=True)

    async def get_ticket(self, session_id: str) -> dict[str, Any] | None:
        doc = await self._collection.find_one({"_id": session_id})
        if doc is not None:
            doc.pop("_id", None)
        return doc

    async def list_tickets(self, limit: int = 50) -> list[dict[str, Any]]:
        cursor = self._collection.find().sort("created_at", -1).limit(limit)
        docs = await cursor.to_list(length=limit)
        for doc in docs:
            doc.pop("_id", None)
        return docs

    def close(self) -> None:
        self._client.close()
