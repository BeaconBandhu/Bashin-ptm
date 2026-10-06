"""AuditStore: persists one hash-chained event per node transition. Phase 0
ships the in-memory implementation so the engine runs with zero infra; the
Postgres implementation (SQLAlchemy models in app/db/models.py + migrations
in migrations/) is the production target and implements the same Protocol.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, Protocol

from app.audit.hash_chain import GENESIS_HASH, NodeEventRecord, compute_event_hash, hash_payload


class AuditStore(Protocol):
    async def append_node_event(
        self,
        *,
        session_id: str,
        node_id: str,
        template_slot_id: str,
        status: str,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
        guardrail_verdicts: list[dict[str, Any]],
    ) -> dict[str, Any]: ...

    async def get_chain(self, session_id: str) -> list[dict[str, Any]]: ...


class InMemoryAuditStore:
    def __init__(self) -> None:
        self._chains: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def append_node_event(
        self,
        *,
        session_id: str,
        node_id: str,
        template_slot_id: str,
        status: str,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
        guardrail_verdicts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        async with self._lock:
            chain = self._chains[session_id]
            prev_hash = chain[-1]["hash"] if chain else GENESIS_HASH
            inputs_hash = hash_payload(inputs)
            outputs_hash = hash_payload(outputs)
            verdicts_hash = hash_payload(guardrail_verdicts)
            timestamp = datetime.now(UTC).isoformat()

            record = NodeEventRecord(
                node_id=node_id,
                template_slot_id=template_slot_id,
                status=status,
                inputs_hash=inputs_hash,
                outputs_hash=outputs_hash,
                guardrail_verdicts_hash=verdicts_hash,
                timestamp=timestamp,
            )
            event_hash = compute_event_hash(prev_hash, record)

            event: dict[str, Any] = {
                "session_id": session_id,
                "node_id": node_id,
                "template_slot_id": template_slot_id,
                "status": status,
                "inputs_hash": inputs_hash,
                "outputs_hash": outputs_hash,
                "guardrail_verdicts_hash": verdicts_hash,
                "timestamp": timestamp,
                "prev_hash": prev_hash,
                "hash": event_hash,
                # Raw payloads travel alongside the hashes for the human
                # console / debugging; only the hashes are chain-verified.
                "inputs": inputs,
                "outputs": outputs,
                "guardrail_verdicts": guardrail_verdicts,
            }
            chain.append(event)
            return event

    async def get_chain(self, session_id: str) -> list[dict[str, Any]]:
        return list(self._chains.get(session_id, []))
