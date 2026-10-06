"""Pydantic input/output schemas for the customer-support template's node
types. See app/domains/support/template.py for how they're wired into a
branching CompiledPlan:

scope_gate -> retrieve_context | decline_out_of_scope
retrieve_context -> rag_confidence_gate -> respond_to_user | create_support_ticket
create_support_ticket -> lookup_transaction_context -> council_triage -> triage_route_gate
triage_route_gate -> council_verifier (high-stakes) | resolution_decision (routine)
council_verifier -> resolution_decision
resolution_decision -> respond_to_user | take_protective_action | escalate_to_human
take_protective_action -> respond_to_user
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

# The Council's action vocabulary is deliberately closed to two members,
# both reversible and non-monetary — see the plan's Council-Ecosystem-v2
# increment. Neither type can move money or issue a refund; that boundary
# is structural, not a prompt instruction that could be argued around.
ProtectiveAction = Literal["freeze_card", "file_dispute", "none"]


class RetrievalMatchDTO(BaseModel):
    entry_id: str
    kind: Literal["faq", "policy"]
    question: str
    answer: str
    score: float


# ---- scope / harness gate --------------------------------------------------


class ScopeGateInput(BaseModel):
    query: str
    customer_id: str


class ScopeGateOutput(BaseModel):
    in_scope: bool
    route: Literal["retrieve_context", "decline_out_of_scope"]
    reason: str | None


class DeclineOutOfScopeInput(BaseModel):
    query: str
    reason: str | None


class DeclineOutOfScopeOutput(BaseModel):
    message: str


# ---- RAG tier ---------------------------------------------------------------


class RetrieveContextInput(BaseModel):
    query: str
    customer_id: str


class RetrieveContextOutput(BaseModel):
    query: str
    customer_id: str
    matches: list[RetrievalMatchDTO]
    best_score: float


class RagConfidenceGateInput(BaseModel):
    query: str
    customer_id: str
    best_score: float
    top_match: RetrievalMatchDTO | None


class RagConfidenceGateOutput(BaseModel):
    confident: bool
    route: Literal["respond_to_user", "create_support_ticket"]
    top_match_answer: str | None


# ---- ticketing ----------------------------------------------------------------


class CreateSupportTicketInput(BaseModel):
    query: str
    customer_id: str
    matches: list[RetrievalMatchDTO]
    best_score: float


class CreateSupportTicketOutput(BaseModel):
    ticket_id: str


# ---- transaction lookup + fraud scoring (deterministic, $0) -------------------


class TicketSummaryDTO(BaseModel):
    """A trimmed view of a prior ticket — just enough for the Council to
    reason from, not the full record."""

    session_id: str
    ticket_id: str | None
    amount: Decimal | None
    payee_id: str | None
    status: str
    created_at: str
    query_text: str


class LookupTransactionContextInput(BaseModel):
    query: str
    customer_id: str
    account_id: str


class LookupTransactionContextOutput(BaseModel):
    extracted_amount: Decimal | None
    fraud_score: float
    fraud_reasons: list[str]
    should_freeze: bool
    is_within_golden_hour: bool
    is_repeat_complaint: bool
    matched_payment_tickets: list[TicketSummaryDTO]
    prior_support_tickets: list[TicketSummaryDTO]


# ---- Council: triage -> (route) -> verifier -> resolution ---------------------


class CouncilTriageInput(BaseModel):
    ticket_id: str
    query: str
    retrieved_context: list[str]
    # Verified internal signals — see LookupTransactionContextOutput. Kept
    # as separate, clearly-labeled fields (not folded into retrieved_context)
    # so the Triage prompt can tell "verified fact" apart from "policy text"
    # apart from "the customer's own claim."
    fraud_score: float
    fraud_reasons: list[str]
    is_repeat_complaint: bool
    is_within_golden_hour: bool
    provider: Literal["groq", "openai", "anthropic", "null"]
    model: str
    estimated_cost_usd: Decimal


class CouncilTriageOutput(BaseModel):
    resolvable: bool
    diagnosis: str
    resolution_steps: str | None
    escalate_reason: str | None
    confidence: float
    risk_class: Literal["routine", "high_stakes"]
    requested_action: ProtectiveAction
    provider: str
    model: str
    actual_cost_usd: Decimal
    input_tokens: int
    output_tokens: int


class TriageRouteGateInput(BaseModel):
    halted_upstream: bool
    risk_class: Literal["routine", "high_stakes"] | None


class TriageRouteGateOutput(BaseModel):
    route: Literal["council_verifier", "resolution_decision"]


class CouncilVerifierInput(BaseModel):
    ticket_id: str
    query: str
    retrieved_context: list[str]
    fraud_score: float
    fraud_reasons: list[str]
    triage_diagnosis: str
    triage_resolvable: bool
    triage_resolution_steps: str | None
    triage_requested_action: ProtectiveAction
    provider: Literal["groq", "openai", "anthropic", "null"]
    model: str
    estimated_cost_usd: Decimal


class CouncilVerifierOutput(BaseModel):
    approved: bool
    final_resolution_steps: str | None
    veto_reason: str | None
    confidence: float
    # The verifier gets the final say on the action too, not just the text
    # — it can confirm, downgrade, or cancel what Triage requested.
    requested_action: ProtectiveAction
    provider: str
    model: str
    actual_cost_usd: Decimal
    input_tokens: int
    output_tokens: int


class ResolutionDecisionInput(BaseModel):
    ticket_id: str
    query: str
    halted_upstream: bool
    resolvable: bool
    resolution_steps: str | None
    diagnosis: str | None
    requested_action: ProtectiveAction
    source: Literal["triage", "verifier", "none"]


class ResolutionDecisionOutput(BaseModel):
    route: Literal["respond_to_user", "take_protective_action", "escalate_to_human"]
    resolvable: bool
    # Where to go AFTER take_protective_action runs — a protective action
    # can precede either outcome (freeze now, still resolved vs. freeze
    # now, still needs a human) so the action node itself must branch on
    # this rather than always dead-ending at one destination.
    follow_up: Literal["respond_to_user", "escalate_to_human"]


# ---- protective action (freeze / dispute-file — never monetary) ---------------


class TakeProtectiveActionInput(BaseModel):
    ticket_id: str
    account_id: str
    action: ProtectiveAction
    reason: str
    amount: Decimal | None
    payee_id: str | None
    is_within_golden_hour: bool
    follow_up: Literal["respond_to_user", "escalate_to_human"]


class TakeProtectiveActionOutput(BaseModel):
    action_taken: ProtectiveAction
    reference_number: str | None
    details: str
    route: Literal["respond_to_user", "escalate_to_human"]


class EscalateToHumanInput(BaseModel):
    ticket_id: str
    query: str
    diagnosis: str | None
    protective_action_summary: str | None = None


class EscalateToHumanOutput(BaseModel):
    ticket_id: str
    assigned: bool
    agent_id: str | None
    agent_name: str | None


class RespondToUserInput(BaseModel):
    message: str
    ticket_id: str | None
    source: Literal["rag_direct", "council_triage", "council_verifier", "protective_action"]


class RespondToUserOutput(BaseModel):
    message: str
    ticket_id: str | None
    source: Literal["rag_direct", "council_triage", "council_verifier", "protective_action"]
