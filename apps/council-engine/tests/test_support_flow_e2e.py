"""End-to-end tests for the customer-support domain's branching AST:
scope harness, RAG-confident direct answers, the triage/verifier Council
split (Groq-shaped/OpenAI-shaped roles, risk-based routing), fraud scoring
+ protective actions (freeze/dispute), the payment-domain cross-domain
freeze wire, human escalation, and specialty-aware agent assignment — all
against the real LangGraph-backed graph builder with a zero-cost
NullLLMClient.
"""

from decimal import Decimal

from app.domains.payment.wiring import PaymentDependencies
from app.domains.payment.wiring import build_node_instances as build_payment_node_instances
from app.domains.support import guardrails as support_guardrails
from app.domains.support.template import build_customer_support_plan
from app.domains.support.wiring import SupportDependencies, build_node_instances
from app.graph.builder import build_graph
from app.graph.state import initial_state
from app.llm.client import NullLLMClient, TriageDecision, VerifierDecision
from app.nodes.types import NodeStatus
from app.planning.validator import validate_compiled_plan

CONFIDENT_QUERY = "how can I reset my upi pin"
AMBIGUOUS_QUERY = (
    "I made a payment of 60000 rupees yesterday to a merchant but it is not showing in my "
    "transaction history despite the money being debited from my account, what should I do"
)
FRAUD_QUERY = "someone made an unauthorized payment of 15000 rupees from my account, I think it was hacked"
OFF_TOPIC_QUERY = "write me a python script to read and write a file"
EARNING_SCHEME_QUERY = "how can I earn 50000 rs using paytm"


def _instances(retriever, support_ticket_store, spend_tracker, agent_queue, bank, llm_client, threshold=0.55):
    deps = SupportDependencies(
        retriever=retriever,
        ticket_store=support_ticket_store,
        bank_client=bank,
        triage_llm_client=llm_client,
        verifier_llm_client=llm_client,
        spend_tracker=spend_tracker,
        agent_queue=agent_queue,
        rag_confidence_threshold=threshold,
    )
    return build_node_instances(deps)


async def _run(node_instances, audit_store, *, query, session_id, customer_id="cust-support-1", provider="null", model="null"):
    plan = build_customer_support_plan(
        query=query, customer_id=customer_id, provider=provider, model=model
    )
    validate_compiled_plan(plan)
    graph = build_graph(plan, node_instances, audit_store)
    state = initial_state(
        session_id=session_id, tenant_id="t1", customer_id=customer_id, tree_id=session_id
    )
    config = {"configurable": {"thread_id": session_id}}
    return await graph.ainvoke(state, config=config)


def _record(final_state, slot_id: str) -> dict:
    return next(r for r in final_state["node_records"] if r["template_slot_id"] == slot_id)


def _visited(final_state) -> set[str]:
    return {r["template_slot_id"] for r in final_state["node_records"]}


async def test_out_of_scope_query_is_declined_before_any_retrieval(
    retriever, support_ticket_store, spend_tracker, agent_queue, bank, audit_store
):
    instances = _instances(retriever, support_ticket_store, spend_tracker, agent_queue, bank, NullLLMClient())
    final_state = await _run(instances, audit_store, query=OFF_TOPIC_QUERY, session_id="chat-1")

    assert _visited(final_state) == {"scope_gate", "decline_out_of_scope"}
    decline = _record(final_state, "decline_out_of_scope")
    assert "Paytm" in decline["outputs"]["message"]
    assert final_state["halted"] is False


async def test_earning_scheme_query_is_declined(
    retriever, support_ticket_store, spend_tracker, agent_queue, bank, audit_store
):
    instances = _instances(retriever, support_ticket_store, spend_tracker, agent_queue, bank, NullLLMClient())
    final_state = await _run(instances, audit_store, query=EARNING_SCHEME_QUERY, session_id="chat-2")
    assert _visited(final_state) == {"scope_gate", "decline_out_of_scope"}


async def test_confident_rag_match_answers_directly_with_no_ticket(
    retriever, support_ticket_store, spend_tracker, agent_queue, bank, audit_store
):
    instances = _instances(retriever, support_ticket_store, spend_tracker, agent_queue, bank, NullLLMClient())
    final_state = await _run(instances, audit_store, query=CONFIDENT_QUERY, session_id="chat-3")

    assert _visited(final_state) == {"scope_gate", "retrieve_context", "rag_confidence_gate", "respond_to_user"}
    respond = _record(final_state, "respond_to_user")
    assert respond["outputs"]["source"] == "rag_direct"
    assert respond["outputs"]["ticket_id"] is None


async def test_routine_risk_skips_verifier_and_resolves_via_triage_alone(
    retriever, support_ticket_store, spend_tracker, agent_queue, bank, audit_store
):
    routine_decision = TriageDecision(
        resolvable=True,
        diagnosis="Routine policy question, fully answerable from context.",
        resolution_steps="Here is the daily UPI limit policy answer.",
        escalate_reason=None,
        confidence=0.9,
        risk_class="routine",
        requested_action="none",
    )
    instances = _instances(
        retriever, support_ticket_store, spend_tracker, agent_queue, bank, NullLLMClient(routine_decision)
    )
    final_state = await _run(instances, audit_store, query=AMBIGUOUS_QUERY, session_id="chat-4")

    visited = _visited(final_state)
    assert "lookup_transaction_context" in visited
    assert "council_triage" in visited
    assert "triage_route_gate" in visited
    assert "council_verifier" not in visited  # the whole point of the risk split
    assert "take_protective_action" not in visited  # requested_action was "none"
    assert "resolution_decision" in visited

    respond = _record(final_state, "respond_to_user")
    assert respond["outputs"]["source"] == "council_triage"


async def test_high_stakes_risk_routes_through_verifier_and_approves(
    retriever, support_ticket_store, spend_tracker, agent_queue, bank, audit_store
):
    high_stakes_decision = TriageDecision(
        resolvable=True,
        diagnosis="Standard debited-but-not-credited case within the auto-reversal window.",
        resolution_steps="Your amount will auto-reverse within 5 business days.",
        escalate_reason=None,
        confidence=0.8,
        risk_class="high_stakes",
        requested_action="none",
    )
    approve_decision = VerifierDecision(
        approved=True,
        final_resolution_steps="Confirmed: your amount will auto-reverse within 5 business days per policy.",
        veto_reason=None,
        confidence=0.85,
        requested_action="none",
    )
    instances = _instances(
        retriever, support_ticket_store, spend_tracker, agent_queue, bank,
        NullLLMClient(high_stakes_decision, approve_decision),
    )
    final_state = await _run(instances, audit_store, query=AMBIGUOUS_QUERY, session_id="chat-5")

    visited = _visited(final_state)
    assert "council_verifier" in visited
    assert "escalate_to_human" not in visited

    respond = _record(final_state, "respond_to_user")
    assert respond["outputs"]["source"] == "council_verifier"
    assert "Confirmed" in respond["outputs"]["message"]


async def test_verifier_veto_escalates_to_human_even_though_triage_said_resolvable(
    retriever, support_ticket_store, spend_tracker, agent_queue, bank, audit_store
):
    optimistic_triage = TriageDecision(
        resolvable=True,
        diagnosis="Looks routine but flagged high stakes for a second opinion.",
        resolution_steps="Draft answer that the verifier will scrutinize.",
        escalate_reason=None,
        confidence=0.6,
        risk_class="high_stakes",
        requested_action="none",
    )
    veto_decision = VerifierDecision(
        approved=False,
        final_resolution_steps=None,
        veto_reason="Triage's proposal isn't well-supported by the retrieved policy context.",
        confidence=0.7,
        requested_action="none",
    )
    instances = _instances(
        retriever, support_ticket_store, spend_tracker, agent_queue, bank,
        NullLLMClient(optimistic_triage, veto_decision),
    )
    final_state = await _run(instances, audit_store, query=AMBIGUOUS_QUERY, session_id="chat-6")

    assert "escalate_to_human" in _visited(final_state)
    assert "respond_to_user" not in _visited(final_state)
    escalate = _record(final_state, "escalate_to_human")
    assert escalate["status"] == NodeStatus.AWAITING_HUMAN.value
    assert final_state["last_status"] == NodeStatus.AWAITING_HUMAN.value


async def test_fraud_query_escalates_to_the_fraud_specialist(
    retriever, support_ticket_store, spend_tracker, agent_queue, bank, audit_store
):
    # NullLLMClient's default decision is resolvable=False, high_stakes, requested_action=none.
    instances = _instances(retriever, support_ticket_store, spend_tracker, agent_queue, bank, NullLLMClient())
    final_state = await _run(instances, audit_store, query=FRAUD_QUERY, session_id="chat-7")

    escalate = _record(final_state, "escalate_to_human")
    assert escalate["outputs"]["agent_name"] == "Aranya Bandhu"


async def test_kyc_query_escalates_to_the_account_specialist(
    retriever, support_ticket_store, spend_tracker, agent_queue, bank, audit_store
):
    instances = _instances(retriever, support_ticket_store, spend_tracker, agent_queue, bank, NullLLMClient())
    final_state = await _run(
        instances, audit_store,
        query="my kyc got approved three days ago but my account still shows as unverified and I cannot send money",
        session_id="chat-8",
    )
    escalate = _record(final_state, "escalate_to_human")
    assert escalate["outputs"]["agent_name"] == "Vanshika Yadav"


async def test_spend_cap_blocks_triage_and_escalates_without_calling_verifier(
    retriever, support_ticket_store, spend_tracker, agent_queue, bank, audit_store, monkeypatch
):
    await spend_tracker.record("openai", 3.999999)
    monkeypatch.setattr(support_guardrails.spend_dependencies, "get_spend_tracker", lambda: spend_tracker)

    instances = _instances(retriever, support_ticket_store, spend_tracker, agent_queue, bank, NullLLMClient())
    final_state = await _run(
        instances, audit_store, query=AMBIGUOUS_QUERY, session_id="chat-9", provider="openai", model="gpt-5-nano"
    )

    triage_record = _record(final_state, "council_triage")
    assert triage_record["status"] == NodeStatus.AWAITING_HUMAN.value
    assert "spend cap would be exceeded" in triage_record["outputs"]["error"]

    # triage_route_gate must still run (skip_on_halt=False) and route
    # straight to resolution_decision — never attempting the verifier call
    # on a ticket that couldn't even be triaged.
    assert "council_verifier" not in _visited(final_state)
    escalate = _record(final_state, "escalate_to_human")
    assert escalate["status"] == NodeStatus.AWAITING_HUMAN.value
    assert await spend_tracker.total("openai") == 3.999999  # no call was actually made


# ---- fraud scoring, protective actions, and the cross-domain freeze wire -----


async def test_freeze_card_action_is_taken_and_then_still_escalates(
    retriever, support_ticket_store, spend_tracker, agent_queue, bank, audit_store
):
    """resolvable=False AND requested_action=freeze_card: the account must
    actually be frozen (not just talked about) before the ticket still goes
    to a human — protect immediately, human decides the rest."""
    triage = TriageDecision(
        resolvable=False,
        diagnosis="Unauthorized payment reported, fraud_score elevated.",
        resolution_steps=None,
        escalate_reason="Requires human investigation of the actual transaction.",
        confidence=0.8,
        risk_class="high_stakes",
        requested_action="freeze_card",
    )
    verifier = VerifierDecision(
        approved=False,
        final_resolution_steps=None,
        veto_reason="Confirmed: needs human investigation.",
        confidence=0.8,
        requested_action="freeze_card",
    )
    instances = _instances(
        retriever, support_ticket_store, spend_tracker, agent_queue, bank, NullLLMClient(triage, verifier)
    )
    final_state = await _run(
        instances, audit_store, query=FRAUD_QUERY, session_id="chat-freeze-1", customer_id="cust-1"
    )

    visited = _visited(final_state)
    assert "take_protective_action" in visited
    assert "escalate_to_human" in visited
    assert "respond_to_user" not in visited

    action_record = _record(final_state, "take_protective_action")
    assert action_record["outputs"]["action_taken"] == "freeze_card"

    escalate = _record(final_state, "escalate_to_human")
    assert escalate["status"] == NodeStatus.AWAITING_HUMAN.value

    # The real cross-domain wire: the freeze must actually be visible on
    # the shared bank client, not just claimed in a message.
    freeze_record = await bank.is_frozen("cust-1")
    assert freeze_record is not None


async def test_frozen_account_actually_blocks_a_subsequent_payment(
    retriever, support_ticket_store, spend_tracker, agent_queue, bank, audit_store,
    payment_rail, crm, idempotency_store,
):
    """The other half of the cross-domain wire: once support freezes an
    account, the PAYMENT domain's own guardrail must block a payment
    attempt against it — not just record the freeze somewhere inert."""
    await bank.freeze_account("cust-1", "test freeze")

    deps = PaymentDependencies(
        bank=bank, payment_rail=payment_rail, crm=crm, idempotency_store=idempotency_store
    )
    payment_instances = build_payment_node_instances(deps)

    from app.domains.payment.template import build_payment_authorization_plan

    plan = build_payment_authorization_plan(
        account_id="cust-1", payee_id="payee-1", amount=Decimal(100), currency="INR",
        payee_verified=True, upi_pin_verified=True, daily_limit=Decimal(10000),
    )
    validate_compiled_plan(plan)
    graph = build_graph(plan, payment_instances, audit_store)
    state = initial_state(session_id="frozen-payment-test", tenant_id="t1", customer_id="cust-1", tree_id="frozen-payment-test")
    final_state = await graph.ainvoke(state, config={"configurable": {"thread_id": "frozen-payment-test"}})

    verify_balance = _record(final_state, "verify_balance")
    assert verify_balance["status"] == NodeStatus.BLOCKED_BY_GUARDRAIL.value
    assert final_state["halted"] is True
    assert _record(final_state, "execute_payment")["status"] == NodeStatus.SKIPPED.value


async def test_file_dispute_action_produces_a_reference_number(
    retriever, support_ticket_store, spend_tracker, agent_queue, bank, audit_store
):
    triage = TriageDecision(
        resolvable=True,
        diagnosis="Wrong recipient, no internal record found — standard dispute process applies.",
        resolution_steps="I've registered a dispute for this transfer.",
        escalate_reason=None,
        confidence=0.75,
        risk_class="routine",
        requested_action="file_dispute",
    )
    instances = _instances(
        retriever, support_ticket_store, spend_tracker, agent_queue, bank, NullLLMClient(triage)
    )
    final_state = await _run(
        instances, audit_store,
        query="I sent 25000 rupees to the wrong UPI ID by mistake, can you get it back",
        session_id="chat-dispute-1",
    )

    assert "take_protective_action" in _visited(final_state)
    action = _record(final_state, "take_protective_action")
    assert action["outputs"]["action_taken"] == "file_dispute"
    assert action["outputs"]["reference_number"].startswith("DSP-")

    respond = _record(final_state, "respond_to_user")
    assert respond["outputs"]["source"] == "protective_action"
    assert action["outputs"]["reference_number"] in respond["outputs"]["message"]


async def test_take_protective_action_guardrail_rejects_empty_evidence(
    retriever, support_ticket_store, spend_tracker, agent_queue, bank, audit_store
):
    """file_dispute with neither an amount nor a payee must be blocked by
    protective_action_has_evidence, not silently filed on nothing."""
    triage = TriageDecision(
        resolvable=True,
        diagnosis="Vague complaint with no extractable amount or payee.",
        resolution_steps="Filing a dispute for you.",
        escalate_reason=None,
        confidence=0.5,
        risk_class="routine",
        requested_action="file_dispute",
    )
    instances = _instances(
        retriever, support_ticket_store, spend_tracker, agent_queue, bank, NullLLMClient(triage)
    )
    final_state = await _run(
        instances, audit_store,
        query="something went wrong with my payment",
        session_id="chat-dispute-2",
    )

    action = _record(final_state, "take_protective_action")
    assert action["status"] == NodeStatus.BLOCKED_BY_GUARDRAIL.value


async def test_lookup_transaction_context_detects_repeat_complaint(
    retriever, support_ticket_store, spend_tracker, agent_queue, bank, audit_store
):
    repeat_query = "my transaction seems stuck and support has not replied to my earlier message about it"
    await support_ticket_store.upsert_ticket({
        "session_id": "prior-1",
        "customer_id": "cust-support-1",
        "type": "customer_support",
        "status": "AWAITING_HUMAN",
        "created_at": "2026-09-01T00:00:00+00:00",
        "query": {"text": repeat_query},
        "ticket_id": "11112222",
    })
    instances = _instances(retriever, support_ticket_store, spend_tracker, agent_queue, bank, NullLLMClient())
    final_state = await _run(
        instances, audit_store,
        query=repeat_query,
        session_id="chat-repeat-1",
    )
    lookup = _record(final_state, "lookup_transaction_context")
    assert lookup["outputs"]["is_repeat_complaint"] is True


async def test_validator_accepts_the_support_template():
    plan = build_customer_support_plan(
        query="how do I add a beneficiary", customer_id="c1", provider="null", model="null"
    )
    validate_compiled_plan(plan)  # must not raise
