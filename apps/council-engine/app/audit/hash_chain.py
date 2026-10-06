"""Tamper-evident, hash-chained audit log — a single-writer ledger, not a
distributed blockchain (council-engine is the only writer, so consensus
would solve a problem this system doesn't have and would only cost
latency). Each node_events row commits to the previous row's hash, so any
retroactive edit to the trail is detectable by recomputing the chain — see
scripts/verify_audit_chain.py and the "Security" section of the plan doc.

Only the *hashes* of inputs/outputs/verdicts are chained, not the raw
payloads themselves — the raw fields can later be redacted or moved to cold
storage without invalidating the chain, since the chain only proves
"this row's content hasn't changed since it was hashed", not "the raw
content is still attached".
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

GENESIS_HASH = "0" * 64


def _canonical_bytes(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")


def hash_payload(data: Any) -> str:
    return hashlib.sha256(_canonical_bytes(data)).hexdigest()


@dataclass(frozen=True, slots=True)
class NodeEventRecord:
    node_id: str
    template_slot_id: str
    status: str
    inputs_hash: str
    outputs_hash: str
    guardrail_verdicts_hash: str
    timestamp: str


def compute_event_hash(prev_hash: str, record: NodeEventRecord) -> str:
    payload = {
        "prev_hash": prev_hash,
        "node_id": record.node_id,
        "template_slot_id": record.template_slot_id,
        "status": record.status,
        "inputs_hash": record.inputs_hash,
        "outputs_hash": record.outputs_hash,
        "guardrail_verdicts_hash": record.guardrail_verdicts_hash,
        "timestamp": record.timestamp,
    }
    return hash_payload(payload)


def verify_chain(events: list[dict]) -> tuple[bool, int | None]:
    """Recomputes the chain over an ordered list of stored event dicts.
    Returns (is_valid, index_of_first_break) — index is None when valid."""
    prev = GENESIS_HASH
    for i, ev in enumerate(events):
        if ev["prev_hash"] != prev:
            return False, i
        record = NodeEventRecord(
            node_id=ev["node_id"],
            template_slot_id=ev["template_slot_id"],
            status=ev["status"],
            inputs_hash=ev["inputs_hash"],
            outputs_hash=ev["outputs_hash"],
            guardrail_verdicts_hash=ev["guardrail_verdicts_hash"],
            timestamp=ev["timestamp"],
        )
        expected = compute_event_hash(prev, record)
        if expected != ev["hash"]:
            return False, i
        prev = ev["hash"]
    return True, None
