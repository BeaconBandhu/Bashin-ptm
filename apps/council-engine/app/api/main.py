"""Phase 0 FastAPI entrypoint: synchronous (non-streaming) execution of the
payment-authorization plan against mocks, with a full hash-chained audit
trail. SSE streaming and JWT auth on the private service binding (per the
architecture plan) land in later phases — this is the vertical slice that
proves the AST + guardrail + audit spine end to end.

Also exposes the TicketStore-backed dashboard API (/v1/tickets, /v1/accounts)
that apps/web's /transfer and /flow pages read from — see
app/tickets/store.py for what a "ticket" is and why it's distinct from the
AuditStore's hash-chained node ledger.

In production this is not reachable from the public internet — deployed
behind Vercel Services with a private binding (see vercel.json at the repo
root). For local dev (`next dev` + `uvicorn` as two separate localhost
ports) CORS is opened to apps/web's origin instead.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.audit.hash_chain import verify_chain
from app.audit.store import InMemoryAuditStore
from app.concurrency.idempotency import InMemoryIdempotencyStore
from app.config import get_settings
from app.domains.payment import nodes as _payment_nodes  # noqa: F401  (import registers node types)
from app.domains.payment.template import build_payment_authorization_plan
from app.domains.payment.wiring import PaymentDependencies
from app.domains.payment.wiring import build_node_instances as build_payment_node_instances
from app.domains.support import nodes as _support_nodes  # noqa: F401  (import registers node types)
from app.domains.support import verification_routes as _verification_routes
from app.domains.support.template import build_customer_support_plan
from app.domains.support.wiring import (
    SupportDependencies,
    build_llm_client,
    select_council_providers,
)
from app.domains.support.wiring import build_node_instances as build_support_node_instances
from app.graph.builder import build_graph
from app.graph.state import initial_state
from app.mocks.bank import MockBankClient
from app.mocks.crm import MockCRMClient
from app.mocks.human_agents import MockHumanAgentQueue
from app.mocks.payment_rail import MockPaymentRailClient
from app.nodes.registry import registry as node_registry
from app.planning.validator import PlanValidationError, validate_compiled_plan
from app.rag.retriever import TfidfRetriever
from app.spend.dependencies import get_spend_tracker
from app.tickets.memory_store import InMemoryTicketStore
from app.tickets.mongo_store import MongoTicketStore
from app.tickets.store import TicketStore

_settings = get_settings()

# Phase 0 process-wide singletons (fine for a single dev instance). Phase 1+
# replaces these with Settings-driven, pooled/Redis-backed clients — see
# app/config.py and the architecture plan's persistence section.
_bank = MockBankClient()
_payment_rail = MockPaymentRailClient()
_crm = MockCRMClient()
_idempotency_store = InMemoryIdempotencyStore()
_audit_store = InMemoryAuditStore()

# TicketStore: real MongoDB when MONGODB_URI is set, in-memory otherwise —
# same "config swap, not a rewrite" pattern as every other store in this
# codebase. See app/tickets/store.py.
_ticket_store: TicketStore = (
    MongoTicketStore(_settings.mongodb_uri, _settings.mongodb_db_name)
    if _settings.has_mongodb
    else InMemoryTicketStore()
)

# Customer-support domain: RAG retriever ($0, TF-IDF), mock human-agent
# queue, and the Council's two LLM clients — the ONLY place a paid call
# happens. select_council_providers() splits Triage (Groq, fast/free) from
# Verifier (OpenAI, only called for high-stakes tickets) — see
# app/domains/support/wiring.py; with no keys configured both resolve to
# the $0 NullLLMClient, so the whole pipeline still runs end to end.
# Shares `_bank` with the payment domain — the cross-domain freeze wire: a
# freeze raised here is enforced by the payment domain's own guardrails.
_support_retriever = TfidfRetriever()
_support_agent_queue = MockHumanAgentQueue()
_support_spend_tracker = get_spend_tracker()
_triage_provider, _triage_model, _verifier_provider, _verifier_model = select_council_providers(_settings)
_triage_llm_client = build_llm_client(_triage_provider, _settings)
_verifier_llm_client = build_llm_client(_verifier_provider, _settings)

# Post-escalation verification call (app/domains/support/verification.py) —
# a local, n8n-orchestrated follow-up pass on tickets that already reached
# escalate_to_human, not a third AST domain. Shares the same bank client,
# ticket store, and spend tracker/caps as the rest of the Council.
_verification_routes.configure(
    bank=_bank, ticket_store=_ticket_store, spend_tracker=_support_spend_tracker, settings=_settings
)


@asynccontextmanager
async def _lifespan(_: FastAPI):
    if isinstance(_ticket_store, MongoTicketStore):
        await _ticket_store.ensure_indexes()
    yield
    if isinstance(_ticket_store, MongoTicketStore):
        _ticket_store.close()


app = FastAPI(title="council-engine", version="0.1.0", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_allow_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(_verification_routes.router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {
        "status": "ok",
        "ticket_store": "mongodb" if _settings.has_mongodb else "in-memory",
        "council_triage": f"{_triage_provider}/{_triage_model}",
        "council_verifier": f"{_verifier_provider}/{_verifier_model}",
    }


# ---- demo seed data (accounts/payees the frontend's dropdowns list) -----------

_DEMO_CUSTOMER_ID = "demo-customer-1"
_DEMO_PAYEES = [
    {"payee_id": "payee-electric-co", "label": "Electric Company", "verified": True},
    {"payee_id": "payee-landlord", "label": "Landlord — Rent", "verified": True},
    {"payee_id": "payee-new-vendor", "label": "New Vendor (unverified)", "verified": False},
]


def seed_demo_data() -> None:
    """Convenience seeding so `uvicorn app.api.main:app --reload` is
    immediately exercisable without a setup call."""
    _bank.seed_account(_DEMO_CUSTOMER_ID, Decimal(75000), "INR")
    for payee in _DEMO_PAYEES:
        if payee["verified"]:
            _crm.verify_payee(_DEMO_CUSTOMER_ID, payee["payee_id"])


seed_demo_data()


@app.get("/v1/accounts")
async def list_accounts() -> dict[str, Any]:
    balance, currency = await _bank.get_balance(_DEMO_CUSTOMER_ID)
    return {
        "accounts": [
            {
                "account_id": _DEMO_CUSTOMER_ID,
                "customer_id": _DEMO_CUSTOMER_ID,
                "label": "Demo Savings Account",
                "balance": str(balance),
                "currency": currency,
            }
        ],
        "payees": _DEMO_PAYEES,
    }


@app.get("/v1/domains/payment/plan-template")
async def get_payment_plan_template() -> dict[str, Any]:
    """The static AST skeleton for the payment-authorization template — node
    types, risk tiers, and the guardrails attached to each, independent of
    any particular request. Lets apps/web's /graph page render the AST
    before (and regardless of whether) a live run is streaming through it."""
    plan = build_payment_authorization_plan(
        account_id="_",
        payee_id="_",
        amount=Decimal(1),
        currency="INR",
        payee_verified=True,
        upi_pin_verified=True,
        daily_limit=Decimal(1),
    )
    nodes = [
        {
            "template_slot_id": step.template_slot_id,
            "node_type": step.node_type,
            "risk_tier": node_def.risk_tier,
            "reversible": node_def.reversible,
            "side_effecting": node_def.side_effecting,
            "guardrails": [
                {"name": g.name, "category": g.category, "phase": g.phase, "on_fail": g.on_fail}
                for g in node_def.guardrails
            ],
        }
        for step in plan.steps
        for node_def in [node_registry.get(step.node_type)]
    ]
    return {"domain": plan.domain, "nodes": nodes, "edges": plan.edges()}


# ---- payment authorization -----------------------------------------------------


class PaymentAuthorizationRequest(BaseModel):
    session_id: str
    tenant_id: str = "demo-tenant"
    customer_id: str
    account_id: str
    payee_id: str
    amount: Decimal
    currency: str = "INR"
    payee_verified: bool = False
    upi_pin_verified: bool = False
    daily_limit: Decimal = Decimal(100000)


def _build_ticket(
    req: PaymentAuthorizationRequest, final_state: dict[str, Any], chain_valid: bool
) -> dict[str, Any]:
    # The "governance layer" view the /flow dashboard reads: every guardrail
    # verdict across every node, flattened with which node it belongs to.
    governance: list[dict[str, Any]] = [
        {"node": record["template_slot_id"], **verdict}
        for record in final_state["node_records"]
        for verdict in record.get("guardrail_verdicts", [])
    ]
    return {
        "session_id": req.session_id,
        "tenant_id": req.tenant_id,
        "customer_id": req.customer_id,
        "created_at": datetime.now(UTC).isoformat(),
        "type": "payment_authorization",
        "query": {
            "account_id": req.account_id,
            "payee_id": req.payee_id,
            "amount": str(req.amount),
            "currency": req.currency,
        },
        "status": final_state["last_status"],
        "halted": final_state["halted"],
        "halt_reason": final_state["halt_reason"],
        "governance": governance,
        # No multi-model council exists yet — Phase 0/1 nodes are
        # deterministic/single_model only (see the council integration
        # policy in the architecture plan). This stays null until a
        # risk_tier="council" node type lands in Phase 2, at which point it
        # carries {member_responses, peer_rankings, chairman_synthesis}.
        "council": None,
        "nodes": final_state["node_records"],
        "chain_valid": chain_valid,
    }


@app.post("/v1/domains/payment/authorize")
async def authorize_payment(req: PaymentAuthorizationRequest) -> dict[str, Any]:
    plan = build_payment_authorization_plan(
        account_id=req.account_id,
        payee_id=req.payee_id,
        amount=req.amount,
        currency=req.currency,
        payee_verified=req.payee_verified,
        upi_pin_verified=req.upi_pin_verified,
        daily_limit=req.daily_limit,
    )
    try:
        validate_compiled_plan(plan)
    except PlanValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc

    deps = PaymentDependencies(
        bank=_bank, payment_rail=_payment_rail, crm=_crm, idempotency_store=_idempotency_store
    )
    node_instances = build_payment_node_instances(deps)
    graph = build_graph(plan, node_instances, _audit_store)

    state = initial_state(
        session_id=req.session_id,
        tenant_id=req.tenant_id,
        customer_id=req.customer_id,
        tree_id=req.session_id,
    )
    config = {"configurable": {"thread_id": req.session_id}}
    final_state = await graph.ainvoke(state, config=config)

    chain = await _audit_store.get_chain(req.session_id)
    chain_valid, _ = verify_chain(chain)

    ticket = _build_ticket(req, final_state, chain_valid)
    await _ticket_store.upsert_ticket(ticket)

    return {
        "session_id": req.session_id,
        "last_status": final_state["last_status"],
        "halted": final_state["halted"],
        "halt_reason": final_state["halt_reason"],
        "outputs": final_state["outputs"],
        "node_records": final_state["node_records"],
    }


# Phase-0 nodes are all deterministic and complete in low-single-digit
# milliseconds — real, but too fast for a human to watch a live animation
# render. This pause is purely a visualization pacing device for
# /v1/domains/payment/authorize/stream, applied AFTER a node has genuinely
# finished (its real latency is unaffected and is exactly what's recorded
# in the audit trail) — it never fakes or slows down the actual decision.
_LIVE_DEMO_STEP_DELAY_SECONDS = 0.35


def _ndjson_line(obj: dict[str, Any]) -> str:
    return json.dumps(obj, default=str) + "\n"


async def _stream_payment_authorization(
    req: PaymentAuthorizationRequest,
) -> AsyncIterator[str]:
    plan = build_payment_authorization_plan(
        account_id=req.account_id,
        payee_id=req.payee_id,
        amount=req.amount,
        currency=req.currency,
        payee_verified=req.payee_verified,
        upi_pin_verified=req.upi_pin_verified,
        daily_limit=req.daily_limit,
    )
    try:
        validate_compiled_plan(plan)
    except PlanValidationError as exc:
        yield _ndjson_line({"event": "error", "detail": exc.errors})
        return

    deps = PaymentDependencies(
        bank=_bank, payment_rail=_payment_rail, crm=_crm, idempotency_store=_idempotency_store
    )
    node_instances = build_payment_node_instances(deps)
    graph = build_graph(plan, node_instances, _audit_store)

    state = initial_state(
        session_id=req.session_id,
        tenant_id=req.tenant_id,
        customer_id=req.customer_id,
        tree_id=req.session_id,
    )
    config = {"configurable": {"thread_id": req.session_id}}

    yield _ndjson_line(
        {
            "event": "session_started",
            "session_id": req.session_id,
            "plan": [s.template_slot_id for s in plan.steps],
        }
    )

    # LangGraph's own streaming, not a simulation: each node's runner (see
    # app/graph/builder.py) already returns a full-replacement value for
    # every state key it touches, so accumulating deltas in arrival order
    # reconstructs the exact same final state `.ainvoke()` would return.
    accumulated: dict[str, Any] = dict(state)
    async for chunk in graph.astream(state, config=config, stream_mode="updates"):
        for node_name, delta in chunk.items():
            accumulated.update(delta)
            record = delta["node_records"][-1]
            yield _ndjson_line({"event": "node_completed", "node": node_name, "record": record})
            await asyncio.sleep(_LIVE_DEMO_STEP_DELAY_SECONDS)

    chain = await _audit_store.get_chain(req.session_id)
    chain_valid, _ = verify_chain(chain)

    ticket = _build_ticket(req, accumulated, chain_valid)
    await _ticket_store.upsert_ticket(ticket)

    yield _ndjson_line(
        {
            "event": "session_completed",
            "session_id": req.session_id,
            "last_status": accumulated["last_status"],
            "halted": accumulated["halted"],
            "halt_reason": accumulated["halt_reason"],
            "chain_valid": chain_valid,
        }
    )


@app.post("/v1/domains/payment/authorize/stream")
async def authorize_payment_stream(req: PaymentAuthorizationRequest) -> StreamingResponse:
    """Newline-delimited JSON (NDJSON), one event per AST node transition —
    what apps/web's /live and /graph pages animate against. Same plan
    compilation, guardrails, execution, audit trail, and ticket write as the
    synchronous /v1/domains/payment/authorize; this only changes how (and
    when) the client learns about each step."""
    return StreamingResponse(
        _stream_payment_authorization(req), media_type="application/x-ndjson"
    )


@app.get("/v1/session/{session_id}/audit-chain")
async def get_audit_chain(session_id: str) -> dict[str, Any]:
    chain = await _audit_store.get_chain(session_id)
    is_valid, break_index = verify_chain(chain)
    return {
        "session_id": session_id,
        "events": chain,
        "chain_valid": is_valid,
        "first_broken_index": break_index,
    }


# ---- customer support (RAG -> Council -> human escalation) ---------------------


@app.get("/v1/domains/support/plan-template")
@app.get("/v1/chatbot/plan-template")
async def get_support_plan_template() -> dict[str, Any]:
    """The static AST skeleton for the customer-support template — the
    first branching plan in this codebase. Structural only (node types,
    risk tiers, guardrails, edges); no query is actually run."""
    plan = build_customer_support_plan(
        query="_", customer_id="_", provider=_triage_provider, model=_triage_model,
        verifier_provider=_verifier_provider, verifier_model=_verifier_model,
    )
    nodes = [
        {
            "template_slot_id": step.template_slot_id,
            "node_type": step.node_type,
            "risk_tier": node_def.risk_tier,
            "reversible": node_def.reversible,
            "side_effecting": node_def.side_effecting,
            "guardrails": [
                {"name": g.name, "category": g.category, "phase": g.phase, "on_fail": g.on_fail}
                for g in node_def.guardrails
            ],
        }
        for step in plan.steps
        for node_def in [node_registry.get(step.node_type)]
    ]
    return {"domain": plan.domain, "nodes": nodes, "edges": plan.edges()}


class SupportAskRequest(BaseModel):
    session_id: str
    tenant_id: str = "demo-tenant"
    customer_id: str
    query: str


def _build_support_ticket(
    req: SupportAskRequest, final_state: dict[str, Any], chain_valid: bool
) -> dict[str, Any]:
    governance: list[dict[str, Any]] = [
        {"node": record["template_slot_id"], **verdict}
        for record in final_state["node_records"]
        for verdict in record.get("guardrail_verdicts", [])
    ]
    triage_output = final_state["outputs"].get("council_triage")
    verifier_output = final_state["outputs"].get("council_verifier")
    ticket_output = final_state["outputs"].get("create_support_ticket")
    return {
        "session_id": req.session_id,
        "tenant_id": req.tenant_id,
        "customer_id": req.customer_id,
        "created_at": datetime.now(UTC).isoformat(),
        "type": "customer_support",
        "ticket_id": ticket_output["ticket_id"] if ticket_output else None,
        "query": {"text": req.query},
        "status": final_state["last_status"],
        "halted": final_state["halted"],
        "halt_reason": final_state["halt_reason"],
        "governance": governance,
        # Populated once the respective Council stage actually ran — both
        # null for a RAG-direct answer or a scope-declined request (no AI
        # reasoning happened), verifier null when triage alone resolved a
        # routine ticket (see triage_route_gate's budget-saving skip).
        "council": {"triage": triage_output, "verifier": verifier_output},
        "nodes": final_state["node_records"],
        "chain_valid": chain_valid,
    }


@app.post("/v1/domains/support/ask")
@app.post("/v1/chatbot/ask")
async def ask_support(req: SupportAskRequest) -> dict[str, Any]:
    plan = build_customer_support_plan(
        query=req.query, customer_id=req.customer_id, provider=_triage_provider, model=_triage_model,
        verifier_provider=_verifier_provider, verifier_model=_verifier_model,
    )
    try:
        validate_compiled_plan(plan)
    except PlanValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc

    deps = SupportDependencies(
        retriever=_support_retriever,
        ticket_store=_ticket_store,
        bank_client=_bank,
        triage_llm_client=_triage_llm_client,
        verifier_llm_client=_verifier_llm_client,
        spend_tracker=_support_spend_tracker,
        agent_queue=_support_agent_queue,
        rag_confidence_threshold=_settings.rag_confidence_threshold,
    )
    node_instances = build_support_node_instances(deps)
    graph = build_graph(plan, node_instances, _audit_store)

    state = initial_state(
        session_id=req.session_id,
        tenant_id=req.tenant_id,
        customer_id=req.customer_id,
        tree_id=req.session_id,
    )
    config = {"configurable": {"thread_id": req.session_id}}
    final_state = await graph.ainvoke(state, config=config)

    chain = await _audit_store.get_chain(req.session_id)
    chain_valid, _ = verify_chain(chain)

    ticket = _build_support_ticket(req, final_state, chain_valid)
    await _ticket_store.upsert_ticket(ticket)

    return {
        "session_id": req.session_id,
        "ticket_id": ticket["ticket_id"],
        "last_status": final_state["last_status"],
        "halted": final_state["halted"],
        "halt_reason": final_state["halt_reason"],
        "outputs": final_state["outputs"],
        "node_records": final_state["node_records"],
    }


async def _stream_support_ask(req: SupportAskRequest) -> AsyncIterator[str]:
    plan = build_customer_support_plan(
        query=req.query, customer_id=req.customer_id, provider=_triage_provider, model=_triage_model,
        verifier_provider=_verifier_provider, verifier_model=_verifier_model,
    )
    try:
        validate_compiled_plan(plan)
    except PlanValidationError as exc:
        yield _ndjson_line({"event": "error", "detail": exc.errors})
        return

    deps = SupportDependencies(
        retriever=_support_retriever,
        ticket_store=_ticket_store,
        bank_client=_bank,
        triage_llm_client=_triage_llm_client,
        verifier_llm_client=_verifier_llm_client,
        spend_tracker=_support_spend_tracker,
        agent_queue=_support_agent_queue,
        rag_confidence_threshold=_settings.rag_confidence_threshold,
    )
    node_instances = build_support_node_instances(deps)
    graph = build_graph(plan, node_instances, _audit_store)

    state = initial_state(
        session_id=req.session_id,
        tenant_id=req.tenant_id,
        customer_id=req.customer_id,
        tree_id=req.session_id,
    )
    config = {"configurable": {"thread_id": req.session_id}}

    yield _ndjson_line(
        {
            "event": "session_started",
            "session_id": req.session_id,
            # The full possible node set (11), not the path this run will
            # take — e.g. a RAG-confident answer visits only 4 of them.
            "plan": [s.template_slot_id for s in plan.steps],
        }
    )

    accumulated: dict[str, Any] = dict(state)
    async for chunk in graph.astream(state, config=config, stream_mode="updates"):
        for node_name, delta in chunk.items():
            accumulated.update(delta)
            record = delta["node_records"][-1]
            yield _ndjson_line({"event": "node_completed", "node": node_name, "record": record})
            await asyncio.sleep(_LIVE_DEMO_STEP_DELAY_SECONDS)

    chain = await _audit_store.get_chain(req.session_id)
    chain_valid, _ = verify_chain(chain)

    ticket = _build_support_ticket(req, accumulated, chain_valid)
    await _ticket_store.upsert_ticket(ticket)

    yield _ndjson_line(
        {
            "event": "session_completed",
            "session_id": req.session_id,
            "ticket_id": ticket["ticket_id"],
            "last_status": accumulated["last_status"],
            "halted": accumulated["halted"],
            "halt_reason": accumulated["halt_reason"],
            "chain_valid": chain_valid,
        }
    )


@app.post("/v1/domains/support/ask/stream")
@app.post("/v1/chatbot/ask/stream")
async def ask_support_stream(req: SupportAskRequest) -> StreamingResponse:
    """NDJSON, one event per AST node transition — same branching plan,
    guardrails (including the ai_spend_limit check before the one paid
    call), audit trail, and ticket write as the synchronous endpoint."""
    return StreamingResponse(_stream_support_ask(req), media_type="application/x-ndjson")


# ---- tickets dashboard: "how the money is flowing" ------------------------------


@app.get("/v1/tickets")
async def list_tickets(limit: int = 50) -> dict[str, Any]:
    tickets = await _ticket_store.list_tickets(limit=limit)
    return {"tickets": tickets, "count": len(tickets)}


@app.get("/v1/tickets/{session_id}")
async def get_ticket(session_id: str) -> dict[str, Any]:
    ticket = await _ticket_store.get_ticket(session_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail=f"no ticket for session '{session_id}'")
    return ticket
