"""Node types for the customer-support template:

scope_gate -+-> decline_out_of_scope (terminal, $0 — off-topic/scheme request declined)
            +-> retrieve_context -> rag_confidence_gate -+-> respond_to_user (RAG answered it, $0)
                                                          +-> create_support_ticket
                                                               -> lookup_transaction_context (fraud score, $0)
                                                               -> council_triage (Groq)
                                                                     -> triage_route_gate -+-> council_verifier (OpenAI, high-stakes)
                                                                                            +-> resolution_decision (routine, skips verifier)
                                                                     council_verifier -> resolution_decision
                                                                     resolution_decision -+-> respond_to_user
                                                                                           +-> take_protective_action -+-> respond_to_user
                                                                                           |                          +-> escalate_to_human
                                                                                           +-> escalate_to_human

council_triage and council_verifier are each `risk_tier="single_model"` — two
distinct roles, two distinct calls, now on two distinct providers (Groq for
Triage, OpenAI for Verifier — see app/domains/support/wiring.py). Both
reason over a deterministic fraud_score computed by
lookup_transaction_context, never inventing a fraud judgment from prose.
take_protective_action's action vocabulary is closed to two reversible,
non-monetary members (freeze_card, file_dispute) — see
app/domains/support/schemas.py's ProtectiveAction.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domains.support.dispute_ids import generate_dispute_reference
from app.domains.support.extraction import extract_amount
from app.domains.support.fraud_scoring import score_fraud, utcnow
from app.domains.support.guardrails import (
    build_council_triage_guardrails,
    build_council_verifier_guardrails,
    build_take_protective_action_guardrails,
    retrieve_context_guardrails,
)
from app.domains.support.schemas import (
    CouncilTriageInput,
    CouncilTriageOutput,
    CouncilVerifierInput,
    CouncilVerifierOutput,
    CreateSupportTicketInput,
    CreateSupportTicketOutput,
    DeclineOutOfScopeInput,
    DeclineOutOfScopeOutput,
    EscalateToHumanInput,
    EscalateToHumanOutput,
    LookupTransactionContextInput,
    LookupTransactionContextOutput,
    RagConfidenceGateInput,
    RagConfidenceGateOutput,
    ResolutionDecisionInput,
    ResolutionDecisionOutput,
    RespondToUserInput,
    RespondToUserOutput,
    RetrievalMatchDTO,
    RetrieveContextInput,
    RetrieveContextOutput,
    ScopeGateInput,
    ScopeGateOutput,
    TakeProtectiveActionInput,
    TakeProtectiveActionOutput,
    TicketSummaryDTO,
    TriageRouteGateInput,
    TriageRouteGateOutput,
)
from app.domains.support.scope import check_scope
from app.domains.support.ticket_ids import generate_unique_ticket_id
from app.integrations.protocols import BankClient
from app.llm.client import LLMClient
from app.mocks.human_agents import MockHumanAgentQueue
from app.nodes.registry import register_node_type
from app.nodes.types import NodeContext, NodeResult, NodeStatus
from app.rag.retriever import Retriever
from app.spend.tracker import SpendTracker
from app.tickets.store import TicketStore


@register_node_type(
    "scope_gate",
    input_schema=ScopeGateInput,
    output_schema=ScopeGateOutput,
    risk_tier="deterministic",
    reversible=True,
    side_effecting=False,
    guardrails=(),
)
class ScopeGateNode:
    """The harness layer: catches off-topic/general-purpose requests and
    'earn money' scheme asks before they reach RAG or the Council. $0,
    pattern-based — see app/domains/support/scope.py."""

    async def execute(self, ctx: NodeContext, input: ScopeGateInput) -> NodeResult:
        result = check_scope(input.query)
        route = "retrieve_context" if result.in_scope else "decline_out_of_scope"
        output = ScopeGateOutput(in_scope=result.in_scope, route=route, reason=result.reason)
        return NodeResult(output=output, status=NodeStatus.SUCCEEDED)


@register_node_type(
    "decline_out_of_scope",
    input_schema=DeclineOutOfScopeInput,
    output_schema=DeclineOutOfScopeOutput,
    risk_tier="deterministic",
    reversible=True,
    guardrails=(),
)
class DeclineOutOfScopeNode:
    async def execute(self, ctx: NodeContext, input: DeclineOutOfScopeInput) -> NodeResult:
        reason_clause = f" {input.reason}" if input.reason else ""
        message = (
            "I can only help with Paytm account, payment, and transaction support questions."
            f"{reason_clause} If this is actually about your Paytm account or a transaction, "
            "please rephrase it with more detail."
        )
        return NodeResult(
            output=DeclineOutOfScopeOutput(message=message), status=NodeStatus.SUCCEEDED
        )


@register_node_type(
    "retrieve_context",
    input_schema=RetrieveContextInput,
    output_schema=RetrieveContextOutput,
    risk_tier="deterministic",
    reversible=True,
    guardrails=retrieve_context_guardrails,
)
class RetrieveContextNode:
    def __init__(self, retriever: Retriever) -> None:
        self._retriever = retriever

    async def execute(self, ctx: NodeContext, input: RetrieveContextInput) -> NodeResult:
        matches = self._retriever.search(input.query, top_k=3)
        match_dtos = [
            RetrievalMatchDTO(
                entry_id=m.entry.id, kind=m.entry.kind, question=m.entry.question,
                answer=m.entry.answer, score=m.score,
            )
            for m in matches
        ]
        best_score = match_dtos[0].score if match_dtos else 0.0
        output = RetrieveContextOutput(
            query=input.query, customer_id=input.customer_id, matches=match_dtos, best_score=best_score
        )
        return NodeResult(output=output, status=NodeStatus.SUCCEEDED)


@register_node_type(
    "rag_confidence_gate",
    input_schema=RagConfidenceGateInput,
    output_schema=RagConfidenceGateOutput,
    risk_tier="deterministic",
    reversible=True,
    side_effecting=False,
    guardrails=(),
)
class RagConfidenceGateNode:
    def __init__(self, threshold: float) -> None:
        self._threshold = threshold

    async def execute(self, ctx: NodeContext, input: RagConfidenceGateInput) -> NodeResult:
        confident = input.best_score >= self._threshold
        route = "respond_to_user" if confident else "create_support_ticket"
        output = RagConfidenceGateOutput(
            confident=confident,
            route=route,
            top_match_answer=(input.top_match.answer if confident and input.top_match else None),
        )
        return NodeResult(output=output, status=NodeStatus.SUCCEEDED)


@register_node_type(
    "create_support_ticket",
    input_schema=CreateSupportTicketInput,
    output_schema=CreateSupportTicketOutput,
    risk_tier="deterministic",
    reversible=True,
    guardrails=(),
)
class CreateSupportTicketNode:
    """Assigns the 8-digit ticket number the moment a query is escalated
    past RAG. Only checks uniqueness against the ticket store (a light
    read) — the ticket document itself is written once, at the end of the
    run, by the API layer (same pattern as the payment domain), carrying
    this id forward as a field."""

    def __init__(self, ticket_store: TicketStore) -> None:
        self._ticket_store = ticket_store

    async def execute(self, ctx: NodeContext, input: CreateSupportTicketInput) -> NodeResult:
        ticket_id = await generate_unique_ticket_id(self._ticket_store)
        return NodeResult(
            output=CreateSupportTicketOutput(ticket_id=ticket_id), status=NodeStatus.SUCCEEDED
        )


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


@register_node_type(
    "lookup_transaction_context",
    input_schema=LookupTransactionContextInput,
    output_schema=LookupTransactionContextOutput,
    risk_tier="deterministic",
    reversible=True,
    guardrails=(),
)
class LookupTransactionContextNode:
    """The real-data layer the Council reasons over instead of guessing —
    extracts an amount from the query, computes a deterministic fraud score
    (see app/domains/support/fraud_scoring.py) from this customer's own
    ticket history, and checks whether this looks like a repeat complaint
    against their own prior support tickets. $0, no LLM call. Only knows
    about transactions that went through our own simulated payment domain —
    "no internal record found" is itself an honest, meaningful signal, not
    a failure."""

    _RECENT_TICKETS_TO_SCAN = 500
    _AMOUNT_MATCH_TOLERANCE = Decimal("0.02")  # 2% — allows for rounding

    def __init__(self, ticket_store: TicketStore) -> None:
        self._ticket_store = ticket_store

    async def execute(
        self, ctx: NodeContext, input: LookupTransactionContextInput
    ) -> NodeResult:
        extracted_amount = extract_amount(input.query)
        all_tickets = await self._ticket_store.list_tickets(limit=self._RECENT_TICKETS_TO_SCAN)

        payment_tickets = [
            t
            for t in all_tickets
            if t.get("type") == "payment_authorization" and t.get("customer_id") == input.customer_id
        ]
        support_tickets = [
            t
            for t in all_tickets
            if t.get("type") == "customer_support" and t.get("customer_id") == input.customer_id
        ]

        known_payee_ids = {
            t["query"]["payee_id"]
            for t in payment_tickets
            if isinstance(t.get("query"), dict) and "payee_id" in t["query"]
        }

        matched_ticket: dict[str, Any] | None = None
        if extracted_amount is not None:
            for t in payment_tickets:
                t_amount = _decimal_or_none((t.get("query") or {}).get("amount"))
                if t_amount is None:
                    continue
                if abs(t_amount - extracted_amount) <= extracted_amount * self._AMOUNT_MATCH_TOLERANCE:
                    matched_ticket = t
                    break

        report_time = utcnow()
        transaction_time = _parse_datetime(matched_ticket["created_at"]) if matched_ticket else None

        fraud_result = score_fraud(
            customer_id=input.customer_id,
            amount=extracted_amount,
            payee_id=matched_ticket["query"].get("payee_id") if matched_ticket else None,
            transaction_time=transaction_time,
            report_time=report_time,
            recent_payment_tickets=payment_tickets,
            known_payee_ids=known_payee_ids,
        )

        # Repeat-complaint heuristic: token-overlap against this customer's
        # own prior support tickets, not the customer's self-report.
        query_tokens = set(input.query.lower().split())
        is_repeat = False
        for t in support_tickets:
            prior_text = str((t.get("query") or {}).get("text", ""))
            prior_tokens = set(prior_text.lower().split())
            if not query_tokens or not prior_tokens:
                continue
            overlap = len(query_tokens & prior_tokens) / len(query_tokens | prior_tokens)
            if overlap >= 0.3:
                is_repeat = True
                break

        def summarize_payment(t: dict[str, Any]) -> TicketSummaryDTO:
            q = t.get("query") or {}
            return TicketSummaryDTO(
                session_id=str(t.get("session_id", "")),
                ticket_id=t.get("ticket_id"),
                amount=_decimal_or_none(q.get("amount")),
                payee_id=q.get("payee_id"),
                status=str(t.get("status", "")),
                created_at=str(t.get("created_at", "")),
                query_text=f"{q.get('account_id', '')} -> {q.get('payee_id', '')}",
            )

        def summarize_support(t: dict[str, Any]) -> TicketSummaryDTO:
            q = t.get("query") or {}
            return TicketSummaryDTO(
                session_id=str(t.get("session_id", "")),
                ticket_id=t.get("ticket_id"),
                amount=None,
                payee_id=None,
                status=str(t.get("status", "")),
                created_at=str(t.get("created_at", "")),
                query_text=str(q.get("text", ""))[:200],
            )

        output = LookupTransactionContextOutput(
            extracted_amount=extracted_amount,
            fraud_score=fraud_result.score,
            fraud_reasons=fraud_result.reasons,
            should_freeze=fraud_result.should_freeze,
            is_within_golden_hour=(
                fraud_result.signals.is_within_golden_hour if fraud_result.signals else False
            ),
            is_repeat_complaint=is_repeat,
            matched_payment_tickets=[summarize_payment(t) for t in payment_tickets[:5]],
            prior_support_tickets=[summarize_support(t) for t in support_tickets[:5]],
        )
        return NodeResult(output=output, status=NodeStatus.SUCCEEDED)


@register_node_type(
    "council_triage",
    input_schema=CouncilTriageInput,
    output_schema=CouncilTriageOutput,
    risk_tier="single_model",
    reversible=True,
    guardrails=build_council_triage_guardrails(),
)
class CouncilTriageNode:
    def __init__(self, llm_client: LLMClient, spend_tracker: SpendTracker) -> None:
        self._llm = llm_client
        self._spend_tracker = spend_tracker

    async def execute(self, ctx: NodeContext, input: CouncilTriageInput) -> NodeResult:
        try:
            result = await self._llm.triage_ticket(
                ticket_query=input.query,
                retrieved_context=input.retrieved_context,
                fraud_score=input.fraud_score,
                fraud_reasons=input.fraud_reasons,
                is_repeat_complaint=input.is_repeat_complaint,
                is_within_golden_hour=input.is_within_golden_hour,
            )
        except Exception as exc:  # noqa: BLE001 — deliberately broad: any provider
            # failure (rate limit, network, malformed response) must degrade to
            # FAILED -> halted -> escalate_to_human, never crash the request.
            return NodeResult(
                output=None, status=NodeStatus.FAILED, error=f"council triage call failed: {exc!r}"
            )

        if result.cost_usd > 0:
            await self._spend_tracker.record(result.provider, result.cost_usd)

        d = result.decision
        output = CouncilTriageOutput(
            resolvable=d.resolvable,
            diagnosis=d.diagnosis,
            resolution_steps=d.resolution_steps,
            escalate_reason=d.escalate_reason,
            confidence=d.confidence,
            risk_class=d.risk_class,
            requested_action=d.requested_action,
            provider=result.provider,
            model=result.model,
            actual_cost_usd=Decimal(str(result.cost_usd)),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
        return NodeResult(output=output, status=NodeStatus.SUCCEEDED)


@register_node_type(
    "triage_route_gate",
    input_schema=TriageRouteGateInput,
    output_schema=TriageRouteGateOutput,
    risk_tier="deterministic",
    reversible=True,
    side_effecting=False,
    guardrails=(),
)
class TriageRouteGateNode:
    """The budget lever: routine tickets skip the verifier entirely (one
    paid call, not two); high-stakes ones get a second, independent
    opinion. If triage itself halted (e.g. blocked by the spend guardrail),
    routes straight to resolution_decision, which reads `halted_upstream`
    and escalates — no verifier call is attempted on a ticket we couldn't
    even triage."""

    async def execute(self, ctx: NodeContext, input: TriageRouteGateInput) -> NodeResult:
        if input.halted_upstream or input.risk_class != "high_stakes":
            route = "resolution_decision"
        else:
            route = "council_verifier"
        return NodeResult(output=TriageRouteGateOutput(route=route), status=NodeStatus.SUCCEEDED)


@register_node_type(
    "council_verifier",
    input_schema=CouncilVerifierInput,
    output_schema=CouncilVerifierOutput,
    risk_tier="single_model",
    reversible=True,
    guardrails=build_council_verifier_guardrails(),
)
class CouncilVerifierNode:
    def __init__(self, llm_client: LLMClient, spend_tracker: SpendTracker) -> None:
        self._llm = llm_client
        self._spend_tracker = spend_tracker

    async def execute(self, ctx: NodeContext, input: CouncilVerifierInput) -> NodeResult:
        try:
            result = await self._llm.verify_resolution(
                ticket_query=input.query,
                retrieved_context=input.retrieved_context,
                fraud_score=input.fraud_score,
                fraud_reasons=input.fraud_reasons,
                triage_diagnosis=input.triage_diagnosis,
                triage_resolvable=input.triage_resolvable,
                triage_resolution_steps=input.triage_resolution_steps,
                triage_requested_action=input.triage_requested_action,
            )
        except Exception as exc:  # noqa: BLE001 — same rationale as CouncilTriageNode
            return NodeResult(
                output=None, status=NodeStatus.FAILED, error=f"council verifier call failed: {exc!r}"
            )

        if result.cost_usd > 0:
            await self._spend_tracker.record(result.provider, result.cost_usd)

        d = result.decision
        output = CouncilVerifierOutput(
            approved=d.approved,
            final_resolution_steps=d.final_resolution_steps,
            veto_reason=d.veto_reason,
            confidence=d.confidence,
            requested_action=d.requested_action,
            provider=result.provider,
            model=result.model,
            actual_cost_usd=Decimal(str(result.cost_usd)),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
        return NodeResult(output=output, status=NodeStatus.SUCCEEDED)


@register_node_type(
    "resolution_decision",
    input_schema=ResolutionDecisionInput,
    output_schema=ResolutionDecisionOutput,
    risk_tier="deterministic",
    reversible=True,
    side_effecting=False,
    guardrails=(),
)
class ResolutionDecisionNode:
    async def execute(self, ctx: NodeContext, input: ResolutionDecisionInput) -> NodeResult:
        follow_up = "respond_to_user" if (input.resolvable and not input.halted_upstream) else "escalate_to_human"
        if input.halted_upstream:
            route = "escalate_to_human"
        elif input.requested_action != "none":
            route = "take_protective_action"
        elif input.resolvable:
            route = "respond_to_user"
        else:
            route = "escalate_to_human"
        output = ResolutionDecisionOutput(
            route=route, resolvable=(route != "escalate_to_human"), follow_up=follow_up
        )
        return NodeResult(output=output, status=NodeStatus.SUCCEEDED)


@register_node_type(
    "take_protective_action",
    input_schema=TakeProtectiveActionInput,
    output_schema=TakeProtectiveActionOutput,
    risk_tier="deterministic",
    reversible=True,
    guardrails=build_take_protective_action_guardrails(),
)
class TakeProtectiveActionNode:
    """Closed action vocabulary, both members reversible and non-monetary
    — see ProtectiveAction. Branches to whichever outcome
    resolution_decision determined (`input.follow_up`) — a protective
    action can precede either a full resolution or a human handoff, it
    doesn't decide that on its own."""

    def __init__(self, bank_client: BankClient) -> None:
        self._bank = bank_client

    async def execute(self, ctx: NodeContext, input: TakeProtectiveActionInput) -> NodeResult:
        if input.action == "freeze_card":
            await self._bank.freeze_account(input.account_id, input.reason)
            details = (
                f"Account access has been frozen as a protective measure ({input.reason}). "
                "A human agent will review and can lift this after re-verifying your identity."
            )
            output = TakeProtectiveActionOutput(
                action_taken="freeze_card", reference_number=None, details=details, route=input.follow_up
            )
        elif input.action == "file_dispute":
            reference = generate_dispute_reference()
            urgency = (
                "expedited — reported within the golden hour after the transaction"
                if input.is_within_golden_hour
                else "standard NPCI dispute-resolution window (up to 5 business days)"
            )
            amount_clause = f"₹{input.amount}" if input.amount is not None else "the reported amount"
            payee_clause = f" to {input.payee_id}" if input.payee_id else ""
            details = (
                f"A formal dispute has been filed on your behalf — reference {reference} — for "
                f"{amount_clause}{payee_clause}. Timeline: {urgency}."
            )
            output = TakeProtectiveActionOutput(
                action_taken="file_dispute", reference_number=reference, details=details, route=input.follow_up
            )
        else:
            output = TakeProtectiveActionOutput(
                action_taken="none", reference_number=None, details="", route=input.follow_up
            )
        return NodeResult(output=output, status=NodeStatus.SUCCEEDED)


@register_node_type(
    "escalate_to_human",
    input_schema=EscalateToHumanInput,
    output_schema=EscalateToHumanOutput,
    risk_tier="deterministic",
    reversible=True,
    guardrails=(),
)
class EscalateToHumanNode:
    def __init__(self, agent_queue: MockHumanAgentQueue) -> None:
        self._agents = agent_queue

    async def execute(self, ctx: NodeContext, input: EscalateToHumanInput) -> NodeResult:
        hint = f"{input.query} {input.diagnosis or ''}"
        agent = await self._agents.assign_next_available(hint)
        output = EscalateToHumanOutput(
            ticket_id=input.ticket_id,
            assigned=agent is not None,
            agent_id=agent.agent_id if agent else None,
            agent_name=agent.name if agent else None,
        )
        # A ticket parked with a human is, by definition, not resolved by
        # the AI — AWAITING_HUMAN is the correct terminal status here, not
        # SUCCEEDED, even though this node did exactly what it should.
        return NodeResult(output=output, status=NodeStatus.AWAITING_HUMAN)


@register_node_type(
    "respond_to_user",
    input_schema=RespondToUserInput,
    output_schema=RespondToUserOutput,
    risk_tier="deterministic",
    reversible=True,
    guardrails=(),
)
class RespondToUserNode:
    async def execute(self, ctx: NodeContext, input: RespondToUserInput) -> NodeResult:
        output = RespondToUserOutput(message=input.message, ticket_id=input.ticket_id, source=input.source)
        return NodeResult(output=output, status=NodeStatus.SUCCEEDED)
