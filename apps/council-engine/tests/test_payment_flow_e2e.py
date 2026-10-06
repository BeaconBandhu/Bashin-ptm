"""End-to-end AST execution through the real LangGraph-backed graph builder,
against mocks. Covers the Phase 0 exit criteria from the architecture plan:
a full deterministic payment tree with audit trail, a guardrail block, a
human-escalation path, and chaos handling (timeouts, duplicate idempotency
key) with no double-execution.
"""

from decimal import Decimal

from app.domains.payment.guardrails import HUMAN_APPROVAL_AMOUNT_THRESHOLD
from app.domains.payment.template import build_payment_authorization_plan
from app.graph.builder import build_graph
from app.graph.state import initial_state
from app.mocks.bank import TIMEOUT_TEST_ACCOUNT
from app.mocks.payment_rail import TIMEOUT_TEST_PAYEE
from app.nodes.types import NodeStatus
from app.planning.validator import validate_compiled_plan


async def _run(plan, node_instances, audit_store, *, session_id, customer_id="cust-1"):
    validate_compiled_plan(plan)
    graph = build_graph(plan, node_instances, audit_store)
    state = initial_state(
        session_id=session_id, tenant_id="t1", customer_id=customer_id, tree_id=session_id
    )
    config = {"configurable": {"thread_id": session_id}}
    return await graph.ainvoke(state, config=config)


def _record(final_state, slot_id: str) -> dict:
    return next(r for r in final_state["node_records"] if r["template_slot_id"] == slot_id)


async def test_happy_path_completes_and_pays(node_instances, audit_store):
    plan = build_payment_authorization_plan(
        account_id="cust-1", payee_id="payee-1", amount=Decimal(1000), currency="INR",
        payee_verified=True, upi_pin_verified=True, daily_limit=Decimal(10000),
    )
    final_state = await _run(plan, node_instances, audit_store, session_id="s1")

    assert final_state["last_status"] == NodeStatus.SUCCEEDED.value
    assert final_state["halted"] is False
    assert final_state["outputs"]["execute_payment"]["status"] == "SETTLED"

    chain = await audit_store.get_chain("s1")
    assert len(chain) == 5
    assert all(e["template_slot_id"] for e in chain)


async def test_insufficient_balance_blocks_and_skips_downstream(node_instances, audit_store):
    # cust-1's seeded balance is 75000 (see conftest.bank). 80000 is chosen
    # to be under both MAX_SINGLE_REQUEST_AMOUNT and, deliberately, ALSO
    # over HUMAN_APPROVAL_AMOUNT_THRESHOLD — this proves the business-rule
    # (insufficient-balance) guardrail wins over the ask-human guardrail
    # when both would independently fail, since it's declared first in
    # payment_authorization_gate_guardrails and pre-guardrails resolve to
    # the first failing verdict in declaration order.
    plan = build_payment_authorization_plan(
        account_id="cust-1", payee_id="payee-1", amount=Decimal(80000), currency="INR",
        payee_verified=True, upi_pin_verified=True, daily_limit=Decimal(1000000),
    )
    final_state = await _run(plan, node_instances, audit_store, session_id="s2")

    assert _record(final_state, "payment_authorization_gate")["status"] == NodeStatus.BLOCKED_BY_GUARDRAIL.value
    assert final_state["halted"] is True
    assert _record(final_state, "execute_payment")["status"] == NodeStatus.SKIPPED.value
    assert _record(final_state, "notify_user")["status"] == NodeStatus.SKIPPED.value


async def test_high_value_payment_requires_human(node_instances, audit_store):
    plan = build_payment_authorization_plan(
        account_id="cust-1", payee_id="payee-1",
        amount=HUMAN_APPROVAL_AMOUNT_THRESHOLD + Decimal(1), currency="INR",
        payee_verified=True, upi_pin_verified=True, daily_limit=Decimal(10000000),
    )
    final_state = await _run(plan, node_instances, audit_store, session_id="s3")

    assert _record(final_state, "payment_authorization_gate")["status"] == NodeStatus.AWAITING_HUMAN.value
    assert final_state["halted"] is True
    assert _record(final_state, "execute_payment")["status"] == NodeStatus.SKIPPED.value


async def test_unverified_payee_requires_human(node_instances, audit_store):
    plan = build_payment_authorization_plan(
        account_id="cust-1", payee_id="payee-unverified", amount=Decimal(100), currency="INR",
        payee_verified=False, upi_pin_verified=True, daily_limit=Decimal(10000),
    )
    final_state = await _run(plan, node_instances, audit_store, session_id="s3b")
    assert _record(final_state, "payment_authorization_gate")["status"] == NodeStatus.AWAITING_HUMAN.value


async def test_duplicate_idempotency_key_is_rejected_on_replay(node_instances, audit_store):
    plan = build_payment_authorization_plan(
        account_id="cust-1", payee_id="payee-1", amount=Decimal(1000), currency="INR",
        payee_verified=True, upi_pin_verified=True, daily_limit=Decimal(10000),
    )
    first = await _run(plan, node_instances, audit_store, session_id="s4")
    assert first["outputs"]["execute_payment"]["status"] == "SETTLED"

    # Identical session_id + identical plan/input => identical derived
    # idempotency key, so a naive retry must NOT pay twice.
    second = await _run(plan, node_instances, audit_store, session_id="s4")
    assert _record(second, "execute_payment")["status"] == NodeStatus.FAILED.value
    assert "already claimed" in second["halt_reason"]


async def test_bank_timeout_fails_the_node_and_halts(node_instances, audit_store):
    plan = build_payment_authorization_plan(
        account_id=TIMEOUT_TEST_ACCOUNT, payee_id="payee-1", amount=Decimal(100), currency="INR",
        payee_verified=True, upi_pin_verified=True, daily_limit=Decimal(10000),
    )
    final_state = await _run(
        plan, node_instances, audit_store, session_id="s5", customer_id=TIMEOUT_TEST_ACCOUNT
    )
    assert _record(final_state, "verify_balance")["status"] == NodeStatus.FAILED.value
    assert final_state["halted"] is True
    assert _record(final_state, "execute_payment")["status"] == NodeStatus.SKIPPED.value


async def test_payment_rail_timeout_fails_execute_node_without_partial_state(
    node_instances, audit_store
):
    plan = build_payment_authorization_plan(
        account_id="cust-1", payee_id=TIMEOUT_TEST_PAYEE, amount=Decimal(1000), currency="INR",
        payee_verified=True, upi_pin_verified=True, daily_limit=Decimal(10000),
    )
    final_state = await _run(plan, node_instances, audit_store, session_id="s6")

    assert _record(final_state, "execute_payment")["status"] == NodeStatus.FAILED.value
    assert final_state["halted"] is True
    assert _record(final_state, "notify_user")["status"] == NodeStatus.SKIPPED.value
    # The idempotency key was already claimed by the failed attempt; a retry
    # of the identical request must not silently re-attempt payment.
    retry = await _run(plan, node_instances, audit_store, session_id="s6")
    assert _record(retry, "execute_payment")["status"] == NodeStatus.FAILED.value
    assert "already claimed" in _record(retry, "execute_payment")["outputs"]["error"]
