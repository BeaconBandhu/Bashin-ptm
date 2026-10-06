"""HTTP surface for the verification call (app/domains/support/verification.py).
Deliberately a thin, mostly-mechanical layer over that module — one
endpoint per conversational step, designed to be driven turn-by-turn by
an external orchestrator (the n8n workflow in apps/n8n/) rather than
called all at once. See verification.py's module docstring for why this
is a separate post-escalation pass rather than an AST node.

Wiring: `configure(...)` is called once from app/api/main.py's startup,
mirroring how the rest of main.py wires singletons — this module has no
FastAPI dependency-injection machinery of its own, matching the codebase's
existing convention of module-level singletons for these mock/local-infra
pieces.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import Settings
from app.domains.support.dispute_ids import generate_dispute_reference
from app.domains.support.verification import (
    MAX_QUESTIONS,
    DiscrepancyResult,
    NullRawLLM,
    OpenAICompatibleRawLLM,
    RawJSONLLM,
    ResolutionResult,
    SpendCapExceeded,
    VerificationCallState,
    VerificationCallStore,
    VerificationTurn,
    attempt_resolution,
    check_discrepancy,
    generate_question,
)
from app.integrations.protocols import BankClient
from app.spend.tracker import SpendTracker
from app.tickets.store import TicketStore

router = APIRouter(prefix="/v1/domains/support/verification", tags=["verification-call"])

_call_store = VerificationCallStore()
_bank: BankClient | None = None
_ticket_store: TicketStore | None = None
_question_llm: RawJSONLLM = NullRawLLM()
_careful_llm: RawJSONLLM = NullRawLLM()


def _select_provider(settings: Settings, prefer: Literal["fast", "careful"]) -> tuple[str, str]:
    """fast: Groq first (cheap/quick, matches Triage's role). careful: OpenAI
    first (matches Verifier's role — this is the consistency-checking and
    final-resolution judgment, the two places accuracy matters most).
    Anthropic isn't supported here (only two providers implement the raw
    JSON-mode client below) — falls to Null rather than guessing."""
    order = ("groq", "openai") if prefer == "fast" else ("openai", "groq")
    for provider in order:
        if provider == "groq" and settings.has_groq:
            return "groq", settings.groq_model
        if provider == "openai" and settings.has_openai:
            return "openai", settings.openai_model
    return "null", "null"


def _build_raw_llm(
    provider: str, model: str, settings: Settings, spend_tracker: SpendTracker, caps: dict[str, float]
) -> RawJSONLLM:
    if provider == "groq":
        return OpenAICompatibleRawLLM(
            provider="groq", model=model, api_key=settings.groq_api_key or "",
            spend_tracker=spend_tracker, spend_cap_usd=caps["groq"],
        )
    if provider == "openai":
        return OpenAICompatibleRawLLM(
            provider="openai", model=model, api_key=settings.openai_api_key or "",
            spend_tracker=spend_tracker, spend_cap_usd=caps["openai"],
        )
    return NullRawLLM()


def configure(
    *, bank: BankClient, ticket_store: TicketStore, spend_tracker: SpendTracker, settings: Settings
) -> None:
    global _bank, _ticket_store, _question_llm, _careful_llm
    _bank = bank
    _ticket_store = ticket_store
    caps = {"groq": settings.max_groq_spend_usd, "openai": settings.max_openai_spend_usd}
    fast_provider, fast_model = _select_provider(settings, "fast")
    careful_provider, careful_model = _select_provider(settings, "careful")
    _question_llm = _build_raw_llm(fast_provider, fast_model, settings, spend_tracker, caps)
    _careful_llm = _build_raw_llm(careful_provider, careful_model, settings, spend_tracker, caps)


def _find_node_output(ticket: dict[str, Any], slot_id: str) -> dict[str, Any] | None:
    for record in ticket.get("nodes") or []:
        if record.get("template_slot_id") == slot_id:
            return record.get("outputs")
    return None


def _to_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


class StartVerificationRequest(BaseModel):
    session_id: str


class StartVerificationResponse(BaseModel):
    call_id: str
    question: str


class AnswerVerificationRequest(BaseModel):
    answer: str


class AnswerVerificationResponse(BaseModel):
    status: Literal["next_question", "escalated", "resolved"]
    question: str | None = None
    reason: str | None = None
    resolution: dict[str, Any] | None = None


class VerificationCallView(BaseModel):
    call_id: str
    session_id: str
    ticket_id: str | None
    status: Literal["in_progress", "resolved", "escalated"]
    transcript: list[VerificationTurn]
    pending_question: str | None
    outcome: dict[str, Any] | None


@router.post("/start", response_model=StartVerificationResponse)
async def start_verification(req: StartVerificationRequest) -> StartVerificationResponse:
    assert _ticket_store is not None, "verification_routes.configure() was never called"
    ticket = await _ticket_store.get_ticket(req.session_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    if ticket.get("status") != "AWAITING_HUMAN":
        raise HTTPException(
            status_code=409,
            detail=f"ticket is not awaiting a human (status={ticket.get('status')!r}); nothing to verify",
        )

    lookup = _find_node_output(ticket, "lookup_transaction_context") or {}
    council = ticket.get("council") or {}
    triage = council.get("triage") or {}
    verifier = council.get("verifier") or {}
    diagnosis = (verifier.get("veto_reason") if verifier else None) or triage.get("diagnosis")

    state = _call_store.create(
        session_id=req.session_id,
        ticket_id=ticket.get("ticket_id"),
        customer_id=ticket.get("customer_id"),
        original_query=(ticket.get("query") or {}).get("text", ""),
        extracted_amount=_to_decimal(lookup.get("extracted_amount")),
        fraud_score=float(lookup.get("fraud_score", 0.0)),
        fraud_reasons=list(lookup.get("fraud_reasons") or []),
        diagnosis=diagnosis,
    )
    try:
        question = await generate_question(_question_llm, state)
    except SpendCapExceeded:
        question = "Can you walk me through exactly what happened, step by step?"
    state.pending_question = question
    return StartVerificationResponse(call_id=state.call_id, question=question)


async def _take_action_if_needed(state: VerificationCallState, result: ResolutionResult) -> str | None:
    assert _bank is not None, "verification_routes.configure() was never called"
    if result.action == "freeze_card":
        await _bank.freeze_account(state.customer_id, result.summary or "Verification call confirmed risk")
        return "Account frozen as a protective measure pending human review."
    if result.action == "file_dispute":
        reference = generate_dispute_reference()
        return f"Formal dispute filed on the customer's behalf — reference {reference}."
    return None


async def _persist_outcome(state: VerificationCallState) -> None:
    assert _ticket_store is not None, "verification_routes.configure() was never called"
    ticket = await _ticket_store.get_ticket(state.session_id)
    if ticket is None:
        return
    if state.status == "resolved":
        ticket["status"] = "RESOLVED_BY_VERIFICATION_CALL"
    ticket["verification_call"] = {
        "call_id": state.call_id,
        "status": state.status,
        "transcript": [t.model_dump() for t in state.transcript],
        "outcome": state.outcome,
    }
    await _ticket_store.upsert_ticket(ticket)


async def _conclude(state: VerificationCallState) -> AnswerVerificationResponse:
    try:
        result = await attempt_resolution(_careful_llm, state)
    except SpendCapExceeded:
        result = ResolutionResult(
            resolvable=False, action="none", summary="",
            escalate_reason="AI spend cap reached before a resolution could be reached.",
        )

    if not result.resolvable:
        state.status = "escalated"
        state.outcome = {"type": "insufficient_evidence", "reason": result.escalate_reason}
        await _persist_outcome(state)
        return AnswerVerificationResponse(status="escalated", reason=result.escalate_reason)

    action_detail = await _take_action_if_needed(state, result)
    state.status = "resolved"
    state.outcome = {
        "type": "resolved", "action": result.action, "summary": result.summary, "detail": action_detail,
    }
    await _persist_outcome(state)
    return AnswerVerificationResponse(status="resolved", resolution=state.outcome)


@router.post("/{call_id}/answer", response_model=AnswerVerificationResponse)
async def answer_verification(call_id: str, req: AnswerVerificationRequest) -> AnswerVerificationResponse:
    state = _call_store.get(call_id)
    if state is None:
        raise HTTPException(status_code=404, detail="verification call not found")
    if state.status != "in_progress":
        raise HTTPException(status_code=409, detail=f"call already concluded with status={state.status}")
    if not state.pending_question:
        raise HTTPException(status_code=409, detail="no pending question to answer")

    state.transcript.append(VerificationTurn(question=state.pending_question, answer=req.answer))
    state.pending_question = None

    try:
        discrepancy: DiscrepancyResult = await check_discrepancy(_careful_llm, state)
    except SpendCapExceeded:
        discrepancy = DiscrepancyResult(discrepancy_found=False, reason=None)

    if discrepancy.discrepancy_found:
        state.status = "escalated"
        state.outcome = {"type": "discrepancy", "reason": discrepancy.reason}
        await _persist_outcome(state)
        return AnswerVerificationResponse(status="escalated", reason=discrepancy.reason)

    if len(state.transcript) >= MAX_QUESTIONS:
        return await _conclude(state)

    try:
        question = await generate_question(_question_llm, state)
    except SpendCapExceeded:
        return await _conclude(state)
    state.pending_question = question
    return AnswerVerificationResponse(status="next_question", question=question)


@router.get("/{call_id}", response_model=VerificationCallView)
async def get_verification_call(call_id: str) -> VerificationCallView:
    state = _call_store.get(call_id)
    if state is None:
        raise HTTPException(status_code=404, detail="verification call not found")
    return VerificationCallView(
        call_id=state.call_id,
        session_id=state.session_id,
        ticket_id=state.ticket_id,
        status=state.status,
        transcript=state.transcript,
        pending_question=state.pending_question,
        outcome=state.outcome,
    )
