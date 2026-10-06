"""Dependency injection for the customer-support template. Mirrors
app/domains/payment/wiring.py's pattern exactly: bind concrete clients to
the node types that need them, in one place, so swapping a mock/provider
later never touches node or guardrail code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.config import Settings
from app.domains.support.nodes import (
    CouncilTriageNode,
    CouncilVerifierNode,
    CreateSupportTicketNode,
    DeclineOutOfScopeNode,
    EscalateToHumanNode,
    LookupTransactionContextNode,
    RagConfidenceGateNode,
    ResolutionDecisionNode,
    RespondToUserNode,
    RetrieveContextNode,
    ScopeGateNode,
    TakeProtectiveActionNode,
    TriageRouteGateNode,
)
from app.integrations.protocols import BankClient
from app.llm.client import LLMClient, NullLLMClient
from app.mocks.human_agents import MockHumanAgentQueue
from app.nodes.types import NodeExecutable
from app.rag.retriever import Retriever
from app.spend.tracker import SpendTracker
from app.tickets.store import TicketStore

Provider = Literal["groq", "openai", "anthropic", "null"]


def select_council_providers(settings: Settings) -> tuple[Provider, str, Provider, str]:
    """Returns (triage_provider, triage_model, verifier_provider,
    verifier_model). Confirmed default: Triage on Groq (fast, effectively
    free-tier), Verifier on OpenAI (only called for high-stakes tickets —
    see triage_route_gate). Each role independently falls back to whatever
    IS configured, then to the null client, if its preferred provider
    isn't set — so a single configured key still gets you a working
    (single-provider) Council rather than nothing."""
    if settings.has_groq:
        triage: tuple[Provider, str] = ("groq", settings.groq_model)
    elif settings.has_openai:
        triage = ("openai", settings.openai_model)
    elif settings.has_anthropic:
        triage = ("anthropic", settings.anthropic_model)
    else:
        triage = ("null", "null")

    if settings.has_openai:
        verifier: tuple[Provider, str] = ("openai", settings.openai_model)
    elif settings.has_groq:
        verifier = ("groq", settings.groq_model)
    elif settings.has_anthropic:
        verifier = ("anthropic", settings.anthropic_model)
    else:
        verifier = ("null", "null")

    return triage[0], triage[1], verifier[0], verifier[1]


def build_llm_client(provider: Provider, settings: Settings) -> LLMClient:
    if provider == "groq":
        from app.llm.groq_client import GroqClient  # local import: no SDK cost/dep unless used

        return GroqClient(api_key=settings.groq_api_key, model=settings.groq_model)
    if provider == "openai":
        from app.llm.openai_client import OpenAIClient

        return OpenAIClient(api_key=settings.openai_api_key, model=settings.openai_model)
    if provider == "anthropic":
        from app.llm.anthropic_client import AnthropicClient

        return AnthropicClient(api_key=settings.anthropic_api_key, model=settings.anthropic_model)
    return NullLLMClient()


@dataclass(frozen=True, slots=True)
class SupportDependencies:
    retriever: Retriever
    ticket_store: TicketStore
    bank_client: BankClient
    triage_llm_client: LLMClient
    verifier_llm_client: LLMClient
    spend_tracker: SpendTracker
    agent_queue: MockHumanAgentQueue
    rag_confidence_threshold: float


def build_node_instances(deps: SupportDependencies) -> dict[str, NodeExecutable]:
    return {
        "scope_gate": ScopeGateNode(),
        "decline_out_of_scope": DeclineOutOfScopeNode(),
        "retrieve_context": RetrieveContextNode(deps.retriever),
        "rag_confidence_gate": RagConfidenceGateNode(deps.rag_confidence_threshold),
        "create_support_ticket": CreateSupportTicketNode(deps.ticket_store),
        "lookup_transaction_context": LookupTransactionContextNode(deps.ticket_store),
        "council_triage": CouncilTriageNode(deps.triage_llm_client, deps.spend_tracker),
        "triage_route_gate": TriageRouteGateNode(),
        "council_verifier": CouncilVerifierNode(deps.verifier_llm_client, deps.spend_tracker),
        "resolution_decision": ResolutionDecisionNode(),
        "take_protective_action": TakeProtectiveActionNode(deps.bank_client),
        "escalate_to_human": EscalateToHumanNode(deps.agent_queue),
        "respond_to_user": RespondToUserNode(),
    }
