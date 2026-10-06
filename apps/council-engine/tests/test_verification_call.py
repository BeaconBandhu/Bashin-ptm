"""Tests for the post-escalation verification call (app/domains/support/
verification.py + verification_routes.py) — the n8n-orchestrated,
locally-controlled "phone call" that tries to close an already-escalated
ticket via a short Q&A instead of leaving it for a human, but only when
the answers are both consistent AND concrete enough to justify a specific
action. Route handlers are called directly as plain async functions
(the same style the rest of this test suite uses for nodes) rather than
through a FastAPI TestClient/HTTP stack.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.config import Settings
from app.domains.support import verification_routes as vr
from app.domains.support.verification import (
    VerificationCallState,
    attempt_resolution,
    check_discrepancy,
    deterministic_amount_discrepancy,
)
from app.mocks.bank import MockBankClient
from app.spend.tracker import InMemorySpendTracker
from app.tickets.memory_store import InMemoryTicketStore


class StubRawLLM:
    """Canned-response stand-in for RawJSONLLM. Routes on the exact JSON
    field name each prompt's own response schema declares (not its prose,
    which mentions "discrepancy" in more than one prompt) so tests don't
    churn when wording changes: "discrepancy_found" for the consistency
    check, "resolvable" for the resolution attempt, else question
    generation."""

    def __init__(self, *, question=None, discrepancy=None, resolution=None) -> None:
        self._question = question or {"question": "Can you describe what happened?"}
        self._discrepancy = discrepancy or {"discrepancy_found": False, "reason": None}
        self._resolution = resolution or {"resolvable": False, "action": "none", "summary": "", "escalate_reason": "n/a"}
        self.calls: list[str] = []

    async def call_json(self, system_prompt: str, user_prompt: str) -> dict:
        if "discrepancy_found" in system_prompt.lower():
            self.calls.append("discrepancy")
            return self._discrepancy
        if "resolvable" in system_prompt.lower():
            self.calls.append("resolution")
            return self._resolution
        self.calls.append("question")
        return self._question


def _state(**overrides) -> VerificationCallState:
    defaults = {
        "call_id": "vcall_test",
        "session_id": "sess-1",
        "ticket_id": "12345678",
        "customer_id": "cust-1",
        "original_query": "I never authorized a payment of 5000 rupees",
        "extracted_amount": Decimal(5000),
        "fraud_score": 0.4,
        "fraud_reasons": ["new payee + high amount"],
        "diagnosis": "Possible unauthorized transaction",
    }
    defaults.update(overrides)
    return VerificationCallState(**defaults)


# ---- deterministic_amount_discrepancy ---------------------------------------


def test_deterministic_discrepancy_flags_large_amount_drift():
    from app.domains.support.verification import VerificationTurn

    state = _state()
    state.transcript.append(VerificationTurn(question="How much was taken?", answer="Around 20000 rupees"))
    reason = deterministic_amount_discrepancy(state)
    assert reason is not None
    assert "5000" in reason and "20000" in reason


def test_deterministic_discrepancy_tolerates_small_rounding():
    from app.domains.support.verification import VerificationTurn

    state = _state()
    state.transcript.append(VerificationTurn(question="How much was taken?", answer="About 5100 rupees"))
    assert deterministic_amount_discrepancy(state) is None


def test_deterministic_discrepancy_is_none_with_fewer_than_two_amounts():
    state = _state(extracted_amount=None)
    assert deterministic_amount_discrepancy(state) is None


# ---- check_discrepancy: deterministic check short-circuits the LLM ---------


async def test_check_discrepancy_short_circuits_llm_on_amount_mismatch():
    from app.domains.support.verification import VerificationTurn

    state = _state()
    state.transcript.append(VerificationTurn(question="How much?", answer="Actually it was 90000 rupees"))
    llm = StubRawLLM(discrepancy={"discrepancy_found": False, "reason": None})
    result = await check_discrepancy(llm, state)
    assert result.discrepancy_found is True
    assert llm.calls == []  # never reached the model — the cheap check caught it first


async def test_check_discrepancy_defers_to_llm_when_amounts_agree():
    from app.domains.support.verification import VerificationTurn

    state = _state()
    state.transcript.append(VerificationTurn(question="Do you recognize the payee?", answer="No, never heard of them, I order from them every week though"))
    llm = StubRawLLM(
        discrepancy={
            "discrepancy_found": True,
            "reason": "customer denies knowing payee then describes ordering from them",
            "quote_a": "never heard of them",
            "quote_b": "order from them every week",
        }
    )
    result = await check_discrepancy(llm, state)
    assert result.discrepancy_found is True
    assert llm.calls == ["discrepancy"]


async def test_check_discrepancy_rejects_an_ungrounded_llm_claim():
    """The exact failure mode observed live against a real provider: a
    nano-tier model claiming a contradiction with a paraphrased/hallucinated
    quote rather than something actually said. Not grounded in the real
    transcript -> not trusted, regardless of how confident the model sounds."""
    llm = StubRawLLM(
        discrepancy={
            "discrepancy_found": True,
            "reason": "sounds contradictory",
            "quote_a": "this phrase was never actually said by anyone",
            "quote_b": "neither was this one",
        }
    )
    result = await check_discrepancy(llm, _state())
    assert result.discrepancy_found is False


# ---- attempt_resolution: guardrail against a positive-bias resolution ------


async def test_attempt_resolution_rejects_file_dispute_with_no_evidence():
    """Mirrors protective_action_has_evidence: even if the model claims
    resolvable=True with file_dispute, no amount/payee ever having been
    established downgrades it to escalate rather than trusting the claim."""
    state = _state(extracted_amount=None)
    llm = StubRawLLM(
        resolution={"resolvable": True, "action": "file_dispute", "summary": "Filing now", "escalate_reason": None}
    )
    result = await attempt_resolution(llm, state)
    assert result.resolvable is False
    assert result.action == "none"
    assert "no amount or payee" in (result.escalate_reason or "")


async def test_attempt_resolution_accepts_file_dispute_with_amount_evidence():
    llm = StubRawLLM(
        resolution={
            "resolvable": True, "action": "file_dispute",
            "summary": "Confirmed 5000 rupees to an unrecognized payee", "escalate_reason": None,
        }
    )
    result = await attempt_resolution(llm, _state())
    assert result.resolvable is True
    assert result.action == "file_dispute"


async def test_attempt_resolution_honors_model_declining_to_resolve():
    """The system prompt tells the model not to default positive; this
    just confirms that disposition passes through untouched."""
    llm = StubRawLLM(
        resolution={
            "resolvable": False, "action": "none", "summary": "",
            "escalate_reason": "Customer's story about the timeline is still vague.",
        }
    )
    result = await attempt_resolution(llm, _state())
    assert result.resolvable is False
    assert result.escalate_reason == "Customer's story about the timeline is still vague."


# ---- route-level integration: full call walked through both outcomes -------


def _null_settings() -> Settings:
    return Settings(groq_api_key=None, openai_api_key=None, anthropic_api_key=None)


async def _seed_escalated_ticket(ticket_store: InMemoryTicketStore, session_id: str) -> None:
    await ticket_store.upsert_ticket(
        {
            "session_id": session_id,
            "customer_id": "cust-1",
            "ticket_id": "87654321",
            "type": "customer_support",
            "status": "AWAITING_HUMAN",
            "query": {"text": "I never authorized a payment of 5000 rupees"},
            "council": {
                "triage": {"diagnosis": "Possible unauthorized transaction", "resolvable": False},
                "verifier": None,
            },
            "nodes": [
                {
                    "template_slot_id": "lookup_transaction_context",
                    "outputs": {
                        "extracted_amount": "5000",
                        "fraud_score": 0.4,
                        "fraud_reasons": ["new payee + high amount"],
                    },
                }
            ],
        }
    )


async def test_start_verification_rejects_a_ticket_that_is_not_awaiting_human():
    from fastapi import HTTPException

    ticket_store = InMemoryTicketStore()
    await ticket_store.upsert_ticket(
        {"session_id": "sess-resolved", "customer_id": "cust-1", "status": "SUCCEEDED", "query": {"text": "x"}}
    )
    vr.configure(
        bank=MockBankClient(), ticket_store=ticket_store, spend_tracker=InMemorySpendTracker(),
        settings=_null_settings(),
    )
    with pytest.raises(HTTPException) as exc_info:
        await vr.start_verification(vr.StartVerificationRequest(session_id="sess-resolved"))
    assert exc_info.value.status_code == 409


async def test_full_call_resolves_when_answers_are_consistent_and_concrete():
    ticket_store = InMemoryTicketStore()
    bank = MockBankClient()
    await _seed_escalated_ticket(ticket_store, "sess-resolve")
    vr.configure(bank=bank, ticket_store=ticket_store, spend_tracker=InMemorySpendTracker(), settings=_null_settings())
    vr._question_llm = StubRawLLM(question={"question": "When did you notice the transaction?"})
    vr._careful_llm = StubRawLLM(
        discrepancy={"discrepancy_found": False, "reason": None},
        resolution={
            "resolvable": True, "action": "file_dispute",
            "summary": "Confirmed unauthorized 5000 rupee transfer to an unknown payee.", "escalate_reason": None,
        },
    )

    start = await vr.start_verification(vr.StartVerificationRequest(session_id="sess-resolve"))
    call_id = start.call_id

    last = None
    for _ in range(4):
        last = await vr.answer_verification(call_id, vr.AnswerVerificationRequest(answer="It was 5000 rupees, I don't recognize the payee."))
        if last.status != "next_question":
            break

    assert last.status == "resolved"
    assert last.resolution["action"] == "file_dispute"

    ticket = await ticket_store.get_ticket("sess-resolve")
    assert ticket["status"] == "RESOLVED_BY_VERIFICATION_CALL"
    assert ticket["verification_call"]["outcome"]["type"] == "resolved"


async def test_full_call_escalates_on_a_real_discrepancy():
    ticket_store = InMemoryTicketStore()
    bank = MockBankClient()
    await _seed_escalated_ticket(ticket_store, "sess-escalate")
    vr.configure(bank=bank, ticket_store=ticket_store, spend_tracker=InMemorySpendTracker(), settings=_null_settings())
    vr._question_llm = StubRawLLM(question={"question": "How much was taken?"})
    vr._careful_llm = StubRawLLM(discrepancy={"discrepancy_found": False, "reason": None})

    start = await vr.start_verification(vr.StartVerificationRequest(session_id="sess-escalate"))

    result = await vr.answer_verification(start.call_id, vr.AnswerVerificationRequest(answer="It was actually 90000 rupees, not 5000."))

    assert result.status == "escalated"
    assert "5000" in result.reason and "90000" in result.reason

    ticket = await ticket_store.get_ticket("sess-escalate")
    assert ticket["status"] == "AWAITING_HUMAN"  # unchanged — still needs the human
    assert ticket["verification_call"]["outcome"]["type"] == "discrepancy"


async def test_answering_a_concluded_call_is_rejected():
    from fastapi import HTTPException

    ticket_store = InMemoryTicketStore()
    await _seed_escalated_ticket(ticket_store, "sess-double")
    vr.configure(bank=MockBankClient(), ticket_store=ticket_store, spend_tracker=InMemorySpendTracker(), settings=_null_settings())
    vr._question_llm = StubRawLLM()
    vr._careful_llm = StubRawLLM(
        discrepancy={
            "discrepancy_found": True, "reason": "mismatch",
            "quote_a": "never authorized a payment of 5000 rupees", "quote_b": "whatever",
        }
    )

    start = await vr.start_verification(vr.StartVerificationRequest(session_id="sess-double"))
    await vr.answer_verification(start.call_id, vr.AnswerVerificationRequest(answer="whatever"))

    with pytest.raises(HTTPException) as exc_info:
        await vr.answer_verification(start.call_id, vr.AnswerVerificationRequest(answer="again"))
    assert exc_info.value.status_code == 409
