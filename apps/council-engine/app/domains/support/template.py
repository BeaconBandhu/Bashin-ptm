"""The customer-support domain template — a branching AST with five
decision points. See app/domains/support/nodes.py for the full shape:

scope_gate -+-> decline_out_of_scope                                     ($0)
            +-> retrieve_context -> rag_confidence_gate -+-> respond_to_user   ($0)
                                                          +-> create_support_ticket
                                                               -> lookup_transaction_context   ($0, fraud score)
                                                               -> council_triage (Groq)
                                                                     -> triage_route_gate -+-> council_verifier (OpenAI)
                                                                                            +-> resolution_decision
                                                                     council_verifier -> resolution_decision
                                                                     resolution_decision -+-> respond_to_user
                                                                                           +-> take_protective_action -+-> respond_to_user
                                                                                           |                          +-> escalate_to_human
                                                                                           +-> escalate_to_human

Every branch point puts its own decision in its output and routes via
`router_from_node_output` — decision logic lives once, in the node, not
duplicated in a router lambda.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from app.domains.support.schemas import (
    CouncilTriageInput,
    CouncilVerifierInput,
    CreateSupportTicketInput,
    DeclineOutOfScopeInput,
    EscalateToHumanInput,
    LookupTransactionContextInput,
    RagConfidenceGateInput,
    ResolutionDecisionInput,
    RespondToUserInput,
    RetrievalMatchDTO,
    RetrieveContextInput,
    ScopeGateInput,
    TakeProtectiveActionInput,
    TriageRouteGateInput,
)
from app.graph.plan import CompiledPlan, PlanStep, router_from_node_output, terminal_step
from app.graph.state import TeammateState
from app.llm.pricing import estimate_cost_usd


def build_customer_support_plan(
    *,
    query: str,
    customer_id: str,
    provider: Literal["groq", "openai", "anthropic", "null"],
    model: str,
    verifier_provider: Literal["groq", "openai", "anthropic", "null"] | None = None,
    verifier_model: str | None = None,
) -> CompiledPlan:
    # Triage and Verifier may run on different providers (Groq/OpenAI split
    # — see wiring.py's select_council_providers); default the verifier to
    # the same provider/model as triage only if the caller doesn't specify
    # one, so single-provider tests/demos still work with one argument.
    v_provider = verifier_provider or provider
    v_model = verifier_model or model

    def scope_gate_input(state: TeammateState) -> ScopeGateInput:
        return ScopeGateInput(query=query, customer_id=customer_id)

    def decline_input(state: TeammateState) -> DeclineOutOfScopeInput:
        gate = state["outputs"]["scope_gate"]
        return DeclineOutOfScopeInput(query=query, reason=gate.get("reason"))

    def retrieve_context_input(state: TeammateState) -> RetrieveContextInput:
        return RetrieveContextInput(query=query, customer_id=customer_id)

    def rag_confidence_gate_input(state: TeammateState) -> RagConfidenceGateInput:
        retrieval = state["outputs"]["retrieve_context"]
        matches = retrieval["matches"]
        top_match = RetrievalMatchDTO(**matches[0]) if matches else None
        return RagConfidenceGateInput(
            query=retrieval["query"],
            customer_id=retrieval["customer_id"],
            best_score=retrieval["best_score"],
            top_match=top_match,
        )

    def create_ticket_input(state: TeammateState) -> CreateSupportTicketInput:
        retrieval = state["outputs"]["retrieve_context"]
        matches = [RetrievalMatchDTO(**m) for m in retrieval["matches"]]
        return CreateSupportTicketInput(
            query=retrieval["query"],
            customer_id=retrieval["customer_id"],
            matches=matches,
            best_score=retrieval["best_score"],
        )

    def lookup_transaction_context_input(state: TeammateState) -> LookupTransactionContextInput:
        retrieval = state["outputs"]["retrieve_context"]
        return LookupTransactionContextInput(
            query=retrieval["query"], customer_id=customer_id, account_id=customer_id
        )

    def _retrieved_context_lines(state: TeammateState) -> list[str]:
        retrieval = state["outputs"]["retrieve_context"]
        return [f"[{m['kind']}] {m['question']}: {m['answer']}" for m in retrieval["matches"]]

    def council_triage_input(state: TeammateState) -> CouncilTriageInput:
        retrieval = state["outputs"]["retrieve_context"]
        ticket = state["outputs"]["create_support_ticket"]
        lookup = state["outputs"]["lookup_transaction_context"]
        return CouncilTriageInput(
            ticket_id=ticket["ticket_id"],
            query=retrieval["query"],
            retrieved_context=_retrieved_context_lines(state),
            fraud_score=lookup["fraud_score"],
            fraud_reasons=lookup["fraud_reasons"],
            is_repeat_complaint=lookup["is_repeat_complaint"],
            is_within_golden_hour=lookup["is_within_golden_hour"],
            provider=provider,
            model=model,
            estimated_cost_usd=Decimal(str(estimate_cost_usd(model))) if provider != "null" else Decimal(0),
        )

    def triage_route_gate_input(state: TeammateState) -> TriageRouteGateInput:
        triage = state["outputs"].get("council_triage") or {}
        return TriageRouteGateInput(halted_upstream=state["halted"], risk_class=triage.get("risk_class"))

    def council_verifier_input(state: TeammateState) -> CouncilVerifierInput:
        retrieval = state["outputs"]["retrieve_context"]
        lookup = state["outputs"]["lookup_transaction_context"]
        triage = state["outputs"]["council_triage"]
        return CouncilVerifierInput(
            ticket_id=state["outputs"]["create_support_ticket"]["ticket_id"],
            query=retrieval["query"],
            retrieved_context=_retrieved_context_lines(state),
            fraud_score=lookup["fraud_score"],
            fraud_reasons=lookup["fraud_reasons"],
            triage_diagnosis=triage["diagnosis"],
            triage_resolvable=triage["resolvable"],
            triage_resolution_steps=triage.get("resolution_steps"),
            triage_requested_action=triage.get("requested_action", "none"),
            provider=v_provider,
            model=v_model,
            estimated_cost_usd=Decimal(str(estimate_cost_usd(v_model))) if v_provider != "null" else Decimal(0),
        )

    def resolution_decision_input(state: TeammateState) -> ResolutionDecisionInput:
        ticket = state["outputs"]["create_support_ticket"]
        retrieval = state["outputs"]["retrieve_context"]
        verifier = state["outputs"].get("council_verifier")
        triage = state["outputs"].get("council_triage") or {}
        if verifier is not None:
            return ResolutionDecisionInput(
                ticket_id=ticket["ticket_id"], query=retrieval["query"], halted_upstream=state["halted"],
                resolvable=verifier.get("approved", False),
                resolution_steps=verifier.get("final_resolution_steps"),
                diagnosis=triage.get("diagnosis"),
                requested_action=verifier.get("requested_action", "none"),
                source="verifier",
            )
        return ResolutionDecisionInput(
            ticket_id=ticket["ticket_id"], query=retrieval["query"], halted_upstream=state["halted"],
            resolvable=triage.get("resolvable", False),
            resolution_steps=triage.get("resolution_steps"),
            diagnosis=triage.get("diagnosis"),
            requested_action=triage.get("requested_action", "none"),
            source="triage" if triage else "none",
        )

    def take_protective_action_input(state: TeammateState) -> TakeProtectiveActionInput:
        ticket = state["outputs"]["create_support_ticket"]
        lookup = state["outputs"]["lookup_transaction_context"]
        decision = state["outputs"]["resolution_decision"]
        verifier = state["outputs"].get("council_verifier")
        triage = state["outputs"].get("council_triage") or {}
        source = verifier if verifier is not None else triage
        action = (source or {}).get("requested_action", "none")
        reason_parts = (source or {}).get("diagnosis") or "; ".join(lookup["fraud_reasons"]) or "Council request"
        matched = lookup["matched_payment_tickets"]
        payee_id = matched[0]["payee_id"] if matched else None
        return TakeProtectiveActionInput(
            ticket_id=ticket["ticket_id"],
            account_id=customer_id,
            action=action,
            reason=str(reason_parts),
            amount=lookup["extracted_amount"],
            payee_id=payee_id,
            is_within_golden_hour=lookup["is_within_golden_hour"],
            follow_up=decision["follow_up"],
        )

    def respond_to_user_input(state: TeammateState) -> RespondToUserInput:
        ticket = state["outputs"].get("create_support_ticket")
        action = state["outputs"].get("take_protective_action")
        verifier = state["outputs"].get("council_verifier")
        triage = state["outputs"].get("council_triage")

        if action is not None and action.get("action_taken") not in (None, "none"):
            base_message = None
            if verifier is not None and verifier.get("approved"):
                base_message = verifier.get("final_resolution_steps")
            elif triage is not None and triage.get("resolvable"):
                base_message = triage.get("resolution_steps")
            message = f"{base_message} {action['details']}".strip() if base_message else action["details"]
            return RespondToUserInput(
                message=message, ticket_id=ticket["ticket_id"] if ticket else None, source="protective_action"
            )
        if verifier is not None and verifier.get("approved"):
            return RespondToUserInput(
                message=verifier.get("final_resolution_steps") or "Your issue has been resolved.",
                ticket_id=ticket["ticket_id"] if ticket else None,
                source="council_verifier",
            )
        if triage is not None and triage.get("resolvable"):
            return RespondToUserInput(
                message=triage.get("resolution_steps") or "Your issue has been resolved.",
                ticket_id=ticket["ticket_id"] if ticket else None,
                source="council_triage",
            )
        gate = state["outputs"]["rag_confidence_gate"]
        return RespondToUserInput(
            message=gate.get("top_match_answer") or "Here's what I found.",
            ticket_id=None,
            source="rag_direct",
        )

    def escalate_to_human_input(state: TeammateState) -> EscalateToHumanInput:
        ticket = state["outputs"]["create_support_ticket"]
        retrieval = state["outputs"]["retrieve_context"]
        triage = state["outputs"].get("council_triage")
        verifier = state["outputs"].get("council_verifier")
        action = state["outputs"].get("take_protective_action")
        diagnosis: str | None = None
        if verifier is not None:
            diagnosis = verifier.get("veto_reason") or (triage.get("diagnosis") if triage else None)
        elif triage is not None:
            diagnosis = triage.get("diagnosis") or triage.get("escalate_reason")
        protective_summary = (
            action["details"] if action and action.get("action_taken") not in (None, "none") else None
        )
        return EscalateToHumanInput(
            ticket_id=ticket["ticket_id"], query=retrieval["query"], diagnosis=diagnosis,
            protective_action_summary=protective_summary,
        )

    steps = (
        PlanStep(
            "scope_gate",
            "scope_gate",
            scope_gate_input,
            next_slots=("retrieve_context", "decline_out_of_scope"),
            router=router_from_node_output("scope_gate"),
        ),
        PlanStep("retrieve_context", "retrieve_context", retrieve_context_input),
        PlanStep(
            "rag_confidence_gate",
            "rag_confidence_gate",
            rag_confidence_gate_input,
            next_slots=("respond_to_user", "create_support_ticket"),
            router=router_from_node_output("rag_confidence_gate"),
        ),
        PlanStep("create_support_ticket", "create_support_ticket", create_ticket_input),
        PlanStep(
            "lookup_transaction_context", "lookup_transaction_context", lookup_transaction_context_input
        ),
        PlanStep("council_triage", "council_triage", council_triage_input),
        PlanStep(
            "triage_route_gate",
            "triage_route_gate",
            triage_route_gate_input,
            next_slots=("council_verifier", "resolution_decision"),
            router=router_from_node_output("triage_route_gate"),
            skip_on_halt=False,
        ),
        PlanStep("council_verifier", "council_verifier", council_verifier_input),
        PlanStep(
            "resolution_decision",
            "resolution_decision",
            resolution_decision_input,
            next_slots=("respond_to_user", "take_protective_action", "escalate_to_human"),
            router=router_from_node_output("resolution_decision"),
            skip_on_halt=False,
            clears_halt=True,
        ),
        PlanStep(
            "take_protective_action",
            "take_protective_action",
            take_protective_action_input,
            next_slots=("respond_to_user", "escalate_to_human"),
            # default="escalate_to_human": if protective_action_has_evidence
            # blocks this node (insufficient evidence to act on), there's no
            # output to read a route from — fail safe to a human rather than
            # silently treating it as resolved.
            router=router_from_node_output("take_protective_action", default="escalate_to_human"),
        ),
        terminal_step("decline_out_of_scope", "decline_out_of_scope", decline_input),
        terminal_step("respond_to_user", "respond_to_user", respond_to_user_input),
        # skip_on_halt=False: this is the designated handler when
        # take_protective_action's own guardrail blocks it (halted=True with
        # no intervening clears_halt step) — it must actually run and assign
        # a human, not be skipped as a passthrough.
        terminal_step(
            "escalate_to_human", "escalate_to_human", escalate_to_human_input, skip_on_halt=False
        ),
    )
    return CompiledPlan(domain="customer_support", steps=steps)
