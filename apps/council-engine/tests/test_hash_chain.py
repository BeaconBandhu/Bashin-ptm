"""Verifies the tamper-evident, hash-chained audit log added per the user's
request to make node-execution history verifiable — a single-writer chain,
not a distributed blockchain (see app/audit/hash_chain.py docstring)."""

from app.audit.hash_chain import verify_chain
from app.audit.store import InMemoryAuditStore


async def test_chain_is_valid_for_untampered_events():
    store = InMemoryAuditStore()
    await store.append_node_event(
        session_id="s1", node_id="n1", template_slot_id="a", status="SUCCEEDED",
        inputs={"x": 1}, outputs={"y": 2}, guardrail_verdicts=[],
    )
    await store.append_node_event(
        session_id="s1", node_id="n2", template_slot_id="b", status="SUCCEEDED",
        inputs={"x": 2}, outputs={"y": 3}, guardrail_verdicts=[],
    )
    chain = await store.get_chain("s1")
    valid, broken_at = verify_chain(chain)
    assert valid is True
    assert broken_at is None


async def test_chain_detects_tampering_with_a_historical_row():
    store = InMemoryAuditStore()
    await store.append_node_event(
        session_id="s1", node_id="n1", template_slot_id="a", status="SUCCEEDED",
        inputs={"x": 1}, outputs={"y": 2}, guardrail_verdicts=[],
    )
    await store.append_node_event(
        session_id="s1", node_id="n2", template_slot_id="b", status="SUCCEEDED",
        inputs={"x": 2}, outputs={"y": 3}, guardrail_verdicts=[],
    )
    chain = await store.get_chain("s1")
    chain[0]["status"] = "FAILED"  # simulate a retroactive edit

    valid, broken_at = verify_chain(chain)
    assert valid is False
    assert broken_at == 0


async def test_two_sessions_have_independent_chains():
    store = InMemoryAuditStore()
    await store.append_node_event(
        session_id="s1", node_id="n1", template_slot_id="a", status="SUCCEEDED",
        inputs={}, outputs={}, guardrail_verdicts=[],
    )
    await store.append_node_event(
        session_id="s2", node_id="n1", template_slot_id="a", status="SUCCEEDED",
        inputs={}, outputs={}, guardrail_verdicts=[],
    )
    assert len(await store.get_chain("s1")) == 1
    assert len(await store.get_chain("s2")) == 1
