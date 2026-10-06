"""LLM client abstraction for the Council — the ONLY place in this
codebase a paid API call happens. Two distinct roles, two distinct calls,
matching the "which council member is verifying" transparency the product
asks for — not a cosmetic relabeling of one call:

  - triage_ticket(): first pass. Diagnoses the issue, proposes a
    resolution, classifies risk (`routine` vs `high_stakes`), and decides
    whether a protective action is warranted.
  - verify_resolution(): SECOND, independent pass — only invoked for
    high_stakes tickets (see the router policy in
    app/domains/support/template.py) — that reviews the triage's own
    proposal (and its requested action) against policy and can approve,
    downgrade, or veto it.

Both roles receive REAL verified signals (a deterministic fraud score and
its contributing reasons, prior-ticket lookups — see
app/domains/support/fraud_scoring.py and LookupTransactionContextNode) as
ground truth, kept clearly separate from retrieved policy/FAQ text and from
the customer's own claim — the model is never asked to invent a fraud
judgment from prose alone.

Scoped to exactly what each role needs (not a generic "complete text"
method), same pattern as every other integration Protocol here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

# Closed action vocabulary — both members reversible and non-monetary. See
# app/domains/support/schemas.py's ProtectiveAction for the full rationale.
ProtectiveAction = Literal["freeze_card", "file_dispute", "none"]


@dataclass(frozen=True, slots=True)
class TriageDecision:
    resolvable: bool
    diagnosis: str
    resolution_steps: str | None
    escalate_reason: str | None
    confidence: float
    risk_class: Literal["routine", "high_stakes"]
    requested_action: ProtectiveAction


@dataclass(frozen=True, slots=True)
class VerifierDecision:
    approved: bool
    final_resolution_steps: str | None
    veto_reason: str | None
    confidence: float
    requested_action: ProtectiveAction


@dataclass(frozen=True, slots=True)
class LLMCallResult:
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


@dataclass(frozen=True, slots=True)
class TriageCallResult(LLMCallResult):
    decision: TriageDecision


@dataclass(frozen=True, slots=True)
class VerifierCallResult(LLMCallResult):
    decision: VerifierDecision


class LLMClient(Protocol):
    provider: str
    model: str

    async def triage_ticket(
        self,
        *,
        ticket_query: str,
        retrieved_context: list[str],
        fraud_score: float,
        fraud_reasons: list[str],
        is_repeat_complaint: bool,
        is_within_golden_hour: bool,
    ) -> TriageCallResult: ...

    async def verify_resolution(
        self,
        *,
        ticket_query: str,
        retrieved_context: list[str],
        fraud_score: float,
        fraud_reasons: list[str],
        triage_diagnosis: str,
        triage_resolvable: bool,
        triage_resolution_steps: str | None,
        triage_requested_action: ProtectiveAction,
    ) -> VerifierCallResult: ...


TRIAGE_SYSTEM_PROMPT = (
    "You are the Triage member of a payments-app customer support Council, looking at a "
    "specific escalated support ticket for the first time. You are given three DIFFERENT kinds "
    "of information, in order of trust — do not confuse them: "
    "(1) VERIFIED INTERNAL SIGNALS — a fraud_score (0-1) and the specific reasons behind it, "
    "already computed deterministically from this customer's real transaction/ticket history. "
    "Trust this score as ground truth; do not re-derive your own fraud judgment from the "
    "customer's wording, and do not second-guess the score based on how the complaint 'sounds'. "
    "(2) Retrieved policy/FAQ context — general rules, not specific to this customer. "
    "(3) The customer's own claim in their message — the least trustworthy of the three; never "
    "invent specific account details, transaction statuses, or dates beyond what's given. "
    "\n\n"
    "Decide whether the ticket is resolvable now. Classify risk_class: \"high_stakes\" if "
    "fraud_score is elevated, the disputed amount exceeds ₹10,000, is_repeat_complaint is true, "
    "or you are not fully confident; \"routine\" otherwise. IMPORTANT: an explicit request to "
    "speak to a human does NOT by itself make something high_stakes or unresolvable — judge the "
    "underlying issue on its own merits; if it's genuinely resolvable, resolve it (you can still "
    "mention a human is available if they still want one afterward). A general security/phishing "
    "EDUCATION question (e.g. 'is this call/message legitimate') is a routine, directly answerable "
    "case from policy — it does not need fraud_score or any account lookup, only don't confuse it "
    "with a report of something that already happened to the customer's own account. If "
    "is_repeat_complaint is true, do not just repeat a generic first answer — be more decisive, and "
    "prefer requesting file_dispute with elevated priority over another plain explanation. "
    "\n\n"
    "You may request a protective action via requested_action — a CLOSED set, neither of which "
    "moves money or determines that fraud definitely occurred: "
    "\"freeze_card\" — for a report of unauthorized activity or a hacked account (fraud_score "
    "elevated). This protects the account immediately while a human reviews; it is not a "
    "determination of guilt, so use it whenever fraud_score is elevated even if you're not certain. "
    "\"file_dispute\" — for an unverifiable, misdirected, or partially-refunded transaction the "
    "customer wants investigated/recovered; this formally registers the case with a reference "
    "number and a real timeline (T+5 business days is standard), it does NOT get the money back "
    "itself. \"none\" — no protective action is needed for this ticket. "
    "\n\n"
    "Respond ONLY as JSON matching this schema: "
    '{"resolvable": bool, "diagnosis": string, "resolution_steps": string or null, '
    '"escalate_reason": string or null, "confidence": number between 0 and 1, '
    '"risk_class": "routine" or "high_stakes", '
    '"requested_action": "freeze_card" or "file_dispute" or "none"}. '
    "If resolvable is true, resolution_steps must be a clear, customer-facing answer (it should "
    "mention any requested_action and its purpose) and escalate_reason must be null. If resolvable "
    "is false, escalate_reason must explain why and resolution_steps must be null — you can still "
    "set requested_action even when resolvable is false (e.g. freeze now, human investigates next)."
)


def build_triage_user_prompt(
    ticket_query: str,
    retrieved_context: list[str],
    *,
    fraud_score: float,
    fraud_reasons: list[str],
    is_repeat_complaint: bool,
    is_within_golden_hour: bool,
) -> str:
    context_block = (
        "\n".join(f"- {c}" for c in retrieved_context) if retrieved_context else "(no relevant policy found)"
    )
    reasons_block = "; ".join(fraud_reasons) if fraud_reasons else "(none — no fraud signals detected)"
    return (
        f"Customer query:\n{ticket_query}\n\n"
        f"VERIFIED internal signals:\n"
        f"- fraud_score: {fraud_score:.2f} (reasons: {reasons_block})\n"
        f"- is_repeat_complaint: {is_repeat_complaint}\n"
        f"- is_within_golden_hour (reported within ~1h of the transaction, if known): {is_within_golden_hour}\n\n"
        f"Retrieved policy/FAQ context:\n{context_block}\n\n"
        "Return only the JSON decision, nothing else."
    )


VERIFIER_SYSTEM_PROMPT = (
    "You are the Verifier member of a payments-app customer support Council. A Triage member "
    "already diagnosed this ticket, proposed a resolution, and requested a protective action; your "
    "job is to independently review all three against the SAME verified fraud_score/reasons, the "
    "policy/FAQ context, and the original query, before anything reaches the customer or takes "
    "effect. Be skeptical: veto (approved=false) if the proposal contradicts the given policy, "
    "invents details not present in the context, understates the fraud/amount/repeat-complaint risk "
    "criteria, or is not well-supported. Independently review requested_action too — you may "
    "confirm it, downgrade it (e.g. file_dispute instead of freeze_card if fraud_score doesn't "
    "support a freeze), or set it to \"none\" if unwarranted; you are not required to match Triage's "
    "choice. Only approve a resolution and action you would be willing to send/take yourself. "
    "Respond ONLY as JSON matching this schema: "
    '{"approved": bool, "final_resolution_steps": string or null, "veto_reason": string or null, '
    '"confidence": number between 0 and 1, '
    '"requested_action": "freeze_card" or "file_dispute" or "none"}. '
    "If approved is true, final_resolution_steps must be the (possibly lightly edited) "
    "customer-facing answer and veto_reason must be null. If approved is false, veto_reason must "
    "explain why and final_resolution_steps must be null — a veto always means escalate to a "
    "human, it does not mean try again. requested_action may still be set even when approved is "
    "false (e.g. freeze now while a human takes over the rest)."
)


def build_verifier_user_prompt(
    ticket_query: str,
    retrieved_context: list[str],
    triage_diagnosis: str,
    triage_resolvable: bool,
    triage_resolution_steps: str | None,
    *,
    fraud_score: float,
    fraud_reasons: list[str],
    triage_requested_action: ProtectiveAction,
) -> str:
    context_block = (
        "\n".join(f"- {c}" for c in retrieved_context) if retrieved_context else "(no relevant policy found)"
    )
    reasons_block = "; ".join(fraud_reasons) if fraud_reasons else "(none — no fraud signals detected)"
    return (
        f"Customer query:\n{ticket_query}\n\n"
        f"VERIFIED internal signals:\n- fraud_score: {fraud_score:.2f} (reasons: {reasons_block})\n\n"
        f"Retrieved policy/FAQ context:\n{context_block}\n\n"
        f"Triage's diagnosis:\n{triage_diagnosis}\n\n"
        f"Triage's proposal — resolvable: {triage_resolvable}, "
        f"resolution_steps: {triage_resolution_steps!r}, requested_action: {triage_requested_action!r}\n\n"
        "Return only the JSON decision, nothing else."
    )


class NullLLMClient:
    """Zero-cost mock — no network call, deterministic. Used in tests and
    whenever no provider key is configured, so the whole pipeline (branch
    routing, ticket lifecycle, guardrails, audit trail) is exercisable and
    demoable without spending anything."""

    provider = "null"
    model = "null"

    def __init__(
        self,
        triage_decision: TriageDecision | None = None,
        verifier_decision: VerifierDecision | None = None,
    ) -> None:
        self._triage_decision = triage_decision or TriageDecision(
            resolvable=False,
            diagnosis="No LLM provider configured — this is the null client's fixed response.",
            resolution_steps=None,
            escalate_reason="No AI provider is configured (set GROQ_API_KEY or OPENAI_API_KEY).",
            confidence=0.0,
            risk_class="high_stakes",
            requested_action="none",
        )
        self._verifier_decision = verifier_decision or VerifierDecision(
            approved=False,
            final_resolution_steps=None,
            veto_reason="No AI provider is configured.",
            confidence=0.0,
            requested_action="none",
        )

    async def triage_ticket(
        self,
        *,
        ticket_query: str,
        retrieved_context: list[str],
        fraud_score: float,
        fraud_reasons: list[str],
        is_repeat_complaint: bool,
        is_within_golden_hour: bool,
    ) -> TriageCallResult:
        return TriageCallResult(
            decision=self._triage_decision,
            provider=self.provider,
            model=self.model,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
        )

    async def verify_resolution(
        self,
        *,
        ticket_query: str,
        retrieved_context: list[str],
        fraud_score: float,
        fraud_reasons: list[str],
        triage_diagnosis: str,
        triage_resolvable: bool,
        triage_resolution_steps: str | None,
        triage_requested_action: ProtectiveAction,
    ) -> VerifierCallResult:
        return VerifierCallResult(
            decision=self._verifier_decision,
            provider=self.provider,
            model=self.model,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
        )
