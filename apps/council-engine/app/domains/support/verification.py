"""Post-escalation verification call: a simulated, locally-controlled
"phone call" back to a customer whose ticket already reached
escalate_to_human. Asks targeted follow-up questions one at a time,
cross-checks each new answer against everything said so far (and against
the original complaint) for genuine contradictions, and only leaves the
ticket with a human if a real discrepancy is found OR the gathered
answers still aren't enough to justify a specific action. Orchestrated
externally by an n8n workflow (apps/n8n/) that drives the
question-by-question loop through the endpoints in
verification_routes.py, one HTTP round-trip per conversational turn, with
a human (in this controlled local environment) typing the "customer's"
answer into an n8n form each turn.

Deliberately NOT wired into the LangGraph AST plan itself. escalate_to_human
is a genuine terminal AST state and its audit trail is never rewritten —
this module operates strictly AFTER that, on the ticket record, as a
second-pass "can this actually be closed without the human" check. If it
can't, nothing is lost: the ticket is exactly as escalated as it already
was.

Deliberately biased toward escalating, not resolving: `attempt_resolution`
requires concrete, consistent evidence for a specific action; "no
discrepancy found" is necessary but never sufficient on its own to close
a ticket. See ATTEMPT_RESOLUTION_SYSTEM_PROMPT.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal, Protocol

from openai import AsyncOpenAI
from pydantic import BaseModel

from app.domains.support.extraction import extract_amount
from app.domains.support.schemas import ProtectiveAction
from app.llm.pricing import compute_cost_usd
from app.spend.tracker import SpendTracker

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
MAX_QUESTIONS = 4
AMOUNT_MISMATCH_TOLERANCE = Decimal("0.05")
_VALID_ACTIONS = ("freeze_card", "file_dispute", "none")


def _safe_action(raw: object) -> ProtectiveAction:
    return raw if raw in _VALID_ACTIONS else "none"  # type: ignore[return-value]


class VerificationTurn(BaseModel):
    question: str
    answer: str


@dataclass(slots=True)
class VerificationCallState:
    call_id: str
    session_id: str
    ticket_id: str | None
    customer_id: str
    original_query: str
    extracted_amount: Decimal | None
    fraud_score: float
    fraud_reasons: list[str]
    diagnosis: str | None
    transcript: list[VerificationTurn] = field(default_factory=list)
    pending_question: str | None = None
    status: Literal["in_progress", "resolved", "escalated"] = "in_progress"
    outcome: dict | None = None


class VerificationCallStore:
    """In-memory only, by design — this is a local controlled-environment
    tool for conducting the call itself, not a durable record. The
    concluding outcome gets folded back into the real TicketStore (see
    verification_routes.py), which IS durable."""

    def __init__(self) -> None:
        self._calls: dict[str, VerificationCallState] = {}

    def create(self, **kwargs: object) -> VerificationCallState:
        call_id = f"vcall_{secrets.token_hex(4)}"
        state = VerificationCallState(call_id=call_id, **kwargs)  # type: ignore[arg-type]
        self._calls[call_id] = state
        return state

    def get(self, call_id: str) -> VerificationCallState | None:
        return self._calls.get(call_id)


class DiscrepancyResult(BaseModel):
    discrepancy_found: bool
    reason: str | None = None
    quote_a: str | None = None
    quote_b: str | None = None


class ResolutionResult(BaseModel):
    resolvable: bool
    action: ProtectiveAction = "none"
    summary: str
    escalate_reason: str | None = None


class SpendCapExceeded(Exception):
    """Raised instead of guessing when a verification call can't afford
    another LLM turn — fails safe to escalation, never to a free guess."""


class RawJSONLLM(Protocol):
    async def call_json(self, system_prompt: str, user_prompt: str) -> dict: ...


class OpenAICompatibleRawLLM:
    """Minimal OpenAI-compatible JSON-mode client, generic over provider.
    Reused here instead of extending the Triage/Verifier-specific
    LLMClient Protocol (app/llm/client.py), which is tightly coupled to
    those two schemas and doesn't fit these three ad-hoc prompts. Records
    spend against the SAME tracker/provider caps as the rest of the
    Council (app/domains/support/guardrails.py's _ai_spend_limit_check) —
    a verification call is not a separate budget."""

    def __init__(
        self,
        *,
        provider: Literal["groq", "openai"],
        model: str,
        api_key: str,
        spend_tracker: SpendTracker,
        spend_cap_usd: float,
    ) -> None:
        self.provider = provider
        self.model = model
        self._spend_tracker = spend_tracker
        self._spend_cap_usd = spend_cap_usd
        base_url = GROQ_BASE_URL if provider == "groq" else None
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def call_json(self, system_prompt: str, user_prompt: str) -> dict:
        current = await self._spend_tracker.total(self.provider)
        if current >= self._spend_cap_usd:
            raise SpendCapExceeded(f"{self.provider} spend cap (${self._spend_cap_usd:.2f}) reached")

        response = await self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=500,
            reasoning_effort="low" if self.provider == "groq" else "minimal",
        )
        raw = response.choices[0].message.content or "{}"
        parsed = json.loads(raw)
        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0
        cost_usd = compute_cost_usd(self.model, input_tokens, output_tokens)
        await self._spend_tracker.record(self.provider, cost_usd)
        return parsed


class NullRawLLM:
    """Deterministic $0 stand-in for tests and for a call with no
    configured provider — asks a generic question, never claims a
    discrepancy, never resolves. Fails safe: with no real model behind
    it, the honest answer is "I can't verify this," not a guess."""

    async def call_json(self, system_prompt: str, user_prompt: str) -> dict:
        if "discrepancy" in system_prompt.lower():
            return {"discrepancy_found": False, "reason": None}
        if "resolution" in system_prompt.lower():
            return {
                "resolvable": False,
                "action": "none",
                "summary": "",
                "escalate_reason": "No AI provider configured for the verification call.",
            }
        return {"question": "Can you describe, in your own words, exactly what happened?"}


def _transcript_block(state: VerificationCallState) -> str:
    if not state.transcript:
        return "(no questions asked yet)"
    return "\n".join(f"Q{i + 1}: {t.question}\nA{i + 1}: {t.answer}" for i, t in enumerate(state.transcript))


GENERATE_QUESTION_SYSTEM_PROMPT = (
    "You are conducting a short verification phone call with a customer whose support ticket "
    "was about to be escalated to a human agent. Your job is to ask ONE clear, specific "
    "follow-up question that would help confirm whether their account of events is accurate and "
    "complete — favor questions that would reveal an inconsistency if the story were fabricated "
    "or misremembered (exact time noticed, how they noticed, whether they recognize the "
    "payee/merchant by name, whether they've shared an OTP or PIN with anyone recently, what "
    "device/app they were using). Never repeat a question already asked. Ask only ONE question. "
    'Respond ONLY as JSON: {"question": string}'
)

CHECK_DISCREPANCY_SYSTEM_PROMPT = (
    "You are reviewing a verification call transcript for INTERNAL CONSISTENCY, not for whether "
    "fraud occurred. Compare the customer's original complaint against every answer given so far. "
    "Flag discrepancy_found=true ONLY for a genuine factual contradiction — e.g. the amount changes "
    "between statements beyond what a rough first estimate would explain, the timeline is "
    "impossible, or they deny recognizing a payee/merchant they then describe having personally "
    "transacted with, used, or knowingly chosen before. Do NOT flag natural elaboration, additional "
    "detail, or a customer simply being unsure/vague as a discrepancy.\n\n"
    "WORKED EXAMPLE — this is NOT a discrepancy, do not flag it:\n"
    'Q: "What merchant name appears on your statement?" A: "QuickMart Traders."\n'
    'Q: "Do you recognize this merchant?" A: "No, I have never shopped there, I don\'t know who they are."\n'
    "Why not: naming a merchant that appears on a statement/notification, and separately not "
    "recognizing that same merchant, are fully compatible facts — that combination is the literal "
    "definition of an unrecognized/unauthorized charge, not a contradiction. Reading a name off a "
    "statement is not the same claim as having chosen or used that merchant.\n\n"
    "WORKED EXAMPLE — this IS a discrepancy, flag it:\n"
    'Q: "Do you recognize this merchant?" A: "No, never heard of them."\n'
    'Q: "Have you ever ordered from them before?" A: "Yes, I order from them every week."\n'
    "Why: the second answer describes actually engaging with the same merchant, directly reversing "
    "the first answer's claim of never having heard of them — a real contradiction about the "
    "customer's own actions, not just which name is on a statement.\n\n"
    "If you flag discrepancy_found=true, you MUST quote the two exact conflicting phrases verbatim, "
    "copied character-for-character from the original complaint or an answer above — do not "
    "paraphrase or summarize them. If you cannot find two literal quotes that directly conflict, "
    "set discrepancy_found=false instead of describing a vaguer inconsistency. "
    'Respond ONLY as JSON: {"discrepancy_found": bool, "reason": string or null, '
    '"quote_a": string or null, "quote_b": string or null}'
)

ATTEMPT_RESOLUTION_SYSTEM_PROMPT = (
    "You are deciding whether a verification call gathered ENOUGH concrete, consistent evidence "
    "to close this ticket without a human. Do NOT default to resolving positively — resolving is "
    "the exception, not the assumption. Set resolvable=true ONLY if the answers give you a "
    "specific, actionable picture: either enough detail to justify a protective action "
    "(freeze_card for a plausible unauthorized-access/fraud account, file_dispute for a specific "
    "identifiable transaction with an amount and/or payee), or a clear, coherent explanation that "
    "no action is needed at all (action=\"none\", e.g. the customer now recalls making the "
    "transaction themselves). If information is still missing, vague, or the story doesn't fully "
    "add up even without a hard discrepancy, set resolvable=false and explain in escalate_reason "
    "what's still missing — when genuinely in doubt, escalate. "
    'Respond ONLY as JSON: {"resolvable": bool, "action": "freeze_card"|"file_dispute"|"none", '
    '"summary": string, "escalate_reason": string or null}'
)


def _ticket_context_block(state: VerificationCallState) -> str:
    return (
        f"Original complaint: {state.original_query}\n"
        f"Extracted amount from original complaint: {state.extracted_amount or 'none stated'}\n"
        f"Deterministic fraud_score: {state.fraud_score:.2f}\n"
        f"Fraud signal reasons: {', '.join(state.fraud_reasons) or 'none'}\n"
        f"Prior diagnosis: {state.diagnosis or 'none'}"
    )


async def generate_question(llm: RawJSONLLM, state: VerificationCallState) -> str:
    user_prompt = f"{_ticket_context_block(state)}\n\nTranscript so far:\n{_transcript_block(state)}"
    parsed = await llm.call_json(GENERATE_QUESTION_SYSTEM_PROMPT, user_prompt)
    question = str(parsed.get("question") or "").strip()
    return question or "Can you walk me through exactly what happened, step by step?"


def deterministic_amount_discrepancy(state: VerificationCallState) -> str | None:
    """A cheap, reliable check run BEFORE the LLM discrepancy check, on the
    same footing as the rest of this codebase's guardrails: don't trust a
    model to catch an arithmetic contradiction a regex can catch for
    free. Compares every amount mentioned (original complaint + each
    answer) pairwise; >5% drift is treated as a real mismatch, not
    rounding."""
    amounts: list[tuple[str, Decimal]] = []
    if state.extracted_amount is not None:
        amounts.append(("original complaint", state.extracted_amount))
    for i, turn in enumerate(state.transcript):
        amt = extract_amount(turn.answer)
        if amt is not None:
            amounts.append((f"answer {i + 1}", amt))
    if len(amounts) < 2:
        return None
    base_label, base_amt = amounts[0]
    for label, amt in amounts[1:]:
        if base_amt == 0:
            continue
        drift = abs(amt - base_amt) / base_amt
        if drift > AMOUNT_MISMATCH_TOLERANCE:
            return f"Amount mismatch: {base_amt} ({base_label}) vs {amt} ({label})"
    return None


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _quote_is_grounded(quote: str | None, state: VerificationCallState) -> bool:
    """A quote counts as real evidence only if it's an actual substring of
    something the customer said — not a paraphrase, not a summary."""
    if not quote or not quote.strip():
        return False
    haystacks = [state.original_query, *(t.answer for t in state.transcript)]
    needle = _normalize(quote)
    return any(needle and needle in _normalize(h) for h in haystacks)


async def check_discrepancy(llm: RawJSONLLM, state: VerificationCallState) -> DiscrepancyResult:
    deterministic_reason = deterministic_amount_discrepancy(state)
    if deterministic_reason is not None:
        return DiscrepancyResult(discrepancy_found=True, reason=deterministic_reason)

    user_prompt = f"{_ticket_context_block(state)}\n\nTranscript so far:\n{_transcript_block(state)}"
    parsed = await llm.call_json(CHECK_DISCREPANCY_SYSTEM_PROMPT, user_prompt)
    discrepancy_found = bool(parsed.get("discrepancy_found", False))
    quote_a, quote_b = parsed.get("quote_a"), parsed.get("quote_b")

    # Don't trust a bare claim of contradiction — same "verify, don't just
    # trust the model's claim" principle as every guardrail in this
    # codebase (e.g. protective_action_has_evidence). Observed live: a
    # nano-tier model repeatedly "found" contradictions in perfectly
    # sequential elaboration when asked to judge narratively with no
    # grounding requirement. Requiring two verbatim quotes that actually
    # appear in the transcript, and rejecting the verdict when they don't,
    # catches that without needing a stronger (costlier) model.
    if discrepancy_found and not (_quote_is_grounded(quote_a, state) and _quote_is_grounded(quote_b, state)):
        discrepancy_found = False

    return DiscrepancyResult(
        discrepancy_found=discrepancy_found,
        reason=parsed.get("reason") if discrepancy_found else None,
        quote_a=quote_a if discrepancy_found else None,
        quote_b=quote_b if discrepancy_found else None,
    )


async def attempt_resolution(llm: RawJSONLLM, state: VerificationCallState) -> ResolutionResult:
    user_prompt = f"{_ticket_context_block(state)}\n\nFull transcript:\n{_transcript_block(state)}"
    parsed = await llm.call_json(ATTEMPT_RESOLUTION_SYSTEM_PROMPT, user_prompt)
    resolvable = bool(parsed.get("resolvable", False))
    action = _safe_action(parsed.get("action"))
    summary = str(parsed.get("summary") or "").strip()

    # Guardrail, not just prompt-following: never accept a "resolved" that
    # can't point at a specific action AND evidence for it (mirrors
    # protective_action_has_evidence for the AST take_protective_action
    # node) — a summary alone is not evidence.
    if resolvable and action == "file_dispute":
        has_amount = state.extracted_amount is not None or any(
            extract_amount(t.answer) is not None for t in state.transcript
        )
        has_payee = any("payee" in t.question.lower() or "merchant" in t.question.lower() for t in state.transcript)
        if not (has_amount or has_payee):
            return ResolutionResult(
                resolvable=False,
                action="none",
                summary=summary,
                escalate_reason="Model proposed file_dispute but no amount or payee was ever established.",
            )

    return ResolutionResult(
        resolvable=resolvable,
        action=action if resolvable else "none",
        summary=summary,
        escalate_reason=parsed.get("escalate_reason") if not resolvable else None,
    )
