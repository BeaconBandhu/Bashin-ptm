"""Shared LangGraph state. Kept intentionally small and JSON-serializable —
this is what gets checkpointed on every transition, and the architecture
plan flags unbounded state growth as a known risk (mitigated later by
summarizing memory into pgvector rather than keeping raw history here)."""

from __future__ import annotations

from typing import Any, TypedDict


class TeammateState(TypedDict):
    session_id: str
    tenant_id: str
    customer_id: str
    tree_id: str
    # template_slot_id -> serialized (model_dump) node output
    outputs: dict[str, Any]
    # append-only per-turn audit trail mirrored into state for the API
    # response; the durable copy of record lives in the AuditStore.
    node_records: list[dict[str, Any]]
    guardrail_verdicts: list[dict[str, Any]]
    last_status: str
    halted: bool
    halt_reason: str | None


def initial_state(
    *, session_id: str, tenant_id: str, customer_id: str, tree_id: str
) -> TeammateState:
    return TeammateState(
        session_id=session_id,
        tenant_id=tenant_id,
        customer_id=customer_id,
        tree_id=tree_id,
        outputs={},
        node_records=[],
        guardrail_verdicts=[],
        last_status="PENDING",
        halted=False,
        halt_reason=None,
    )
