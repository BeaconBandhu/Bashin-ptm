"""Guardrails for the customer-support template. `ai_spend_limit` is the
one guardrail in this codebase that performs I/O inside its check — a
documented exception, see app/guardrails/base.py's module docstring — since
protecting the real spend cap requires reading the real current total right
before the call it might block. It guards BOTH Council stages (triage and,
for high-stakes tickets, the verifier) since each is an independent paid
call.
"""

from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.domains.support.schemas import (
    CouncilTriageInput,
    CouncilTriageOutput,
    CouncilVerifierInput,
    CouncilVerifierOutput,
    TakeProtectiveActionInput,
)
from app.guardrails.base import GuardrailCheckResult, GuardrailSpec
from app.nodes.types import NodeContext
from app.spend import dependencies as spend_dependencies


async def query_is_well_formed(ctx: NodeContext, data: Any) -> GuardrailCheckResult:
    query = getattr(data, "query", "")
    if not query or not query.strip():
        return GuardrailCheckResult("fail", "query must not be empty")
    if len(query) > 4000:
        return GuardrailCheckResult("fail", "query too long (max 4000 characters)")
    return GuardrailCheckResult("pass")


retrieve_context_guardrails = (
    GuardrailSpec(
        name="query_is_well_formed",
        category="input_validation",
        phase="pre",
        check=query_is_well_formed,
        on_fail="block",
    ),
)


async def _ai_spend_limit_check(
    ctx: NodeContext, data: CouncilTriageInput | CouncilVerifierInput
) -> GuardrailCheckResult:
    # Resolved fresh on every call — NOT captured via closure at node-type
    # registration time (which happens once, at module import). Registration
    # is process-global, but spend tracking must never be: this indirection
    # is what lets tests monkeypatch `app.spend.dependencies.get_spend_tracker`
    # per-test without leaking state into (or reading from) the real shared
    # tracker/database. Production behavior is unchanged — it's the same
    # lru_cached singleton either way, just resolved per-call instead of once.
    if data.provider == "null":
        return GuardrailCheckResult("pass")  # no real call, nothing to guard

    settings = get_settings()
    caps = {
        "openai": settings.max_openai_spend_usd,
        "anthropic": settings.max_anthropic_spend_usd,
        "groq": settings.max_groq_spend_usd,
    }
    cap = caps.get(data.provider)
    if cap is None:
        return GuardrailCheckResult("fail", f"no spend cap configured for provider '{data.provider}'")

    tracker = spend_dependencies.get_spend_tracker()
    current = await tracker.total(data.provider)
    projected = current + float(data.estimated_cost_usd)
    if projected > cap:
        return GuardrailCheckResult(
            "fail",
            f"{data.provider} spend cap would be exceeded: ${current:.4f} already spent + "
            f"~${float(data.estimated_cost_usd):.4f} estimated for this call > ${cap:.2f} cap",
        )
    return GuardrailCheckResult("pass")


async def triage_output_is_internally_consistent(
    ctx: NodeContext, data: CouncilTriageOutput
) -> GuardrailCheckResult:
    if not (0.0 <= data.confidence <= 1.0):
        return GuardrailCheckResult("fail", f"confidence {data.confidence} out of [0,1] range")
    if data.resolvable and not data.resolution_steps:
        return GuardrailCheckResult("fail", "resolvable=true but no resolution_steps provided")
    if not data.resolvable and not data.escalate_reason:
        return GuardrailCheckResult("warn", "resolvable=false but no escalate_reason given")
    return GuardrailCheckResult("pass")


async def verifier_output_is_internally_consistent(
    ctx: NodeContext, data: CouncilVerifierOutput
) -> GuardrailCheckResult:
    if not (0.0 <= data.confidence <= 1.0):
        return GuardrailCheckResult("fail", f"confidence {data.confidence} out of [0,1] range")
    if data.approved and not data.final_resolution_steps:
        return GuardrailCheckResult("fail", "approved=true but no final_resolution_steps provided")
    if not data.approved and not data.veto_reason:
        return GuardrailCheckResult("warn", "approved=false but no veto_reason given")
    return GuardrailCheckResult("pass")


def build_council_triage_guardrails() -> tuple[GuardrailSpec, ...]:
    return (
        GuardrailSpec(
            name="ai_spend_limit",
            category="ai_spend_limit",
            phase="pre",
            check=_ai_spend_limit_check,
            on_fail="ask_human",
        ),
        GuardrailSpec(
            name="triage_output_is_internally_consistent",
            category="output_hallucination_check",
            phase="post",
            check=triage_output_is_internally_consistent,
            on_fail="block",
        ),
    )


def build_council_verifier_guardrails() -> tuple[GuardrailSpec, ...]:
    return (
        GuardrailSpec(
            name="ai_spend_limit",
            category="ai_spend_limit",
            phase="pre",
            check=_ai_spend_limit_check,
            on_fail="ask_human",
        ),
        GuardrailSpec(
            name="verifier_output_is_internally_consistent",
            category="output_hallucination_check",
            phase="post",
            check=verifier_output_is_internally_consistent,
            on_fail="block",
        ),
    )


async def protective_action_has_evidence(
    ctx: NodeContext, data: TakeProtectiveActionInput
) -> GuardrailCheckResult:
    """Never freeze or file anything on an empty premise — a real, if
    minimal, business-rule check re-validating the Council's own action
    request before it takes effect, same "never trust a claim without a
    deterministic re-check" pattern as the payment domain's guardrails."""
    if data.action == "none":
        return GuardrailCheckResult("pass")
    if not data.reason or not data.reason.strip():
        return GuardrailCheckResult("fail", f"'{data.action}' requested with no reason given")
    if data.action == "file_dispute" and data.amount is None and not data.payee_id:
        return GuardrailCheckResult(
            "fail", "file_dispute requires at least an amount or a payee to file against"
        )
    return GuardrailCheckResult("pass")


def build_take_protective_action_guardrails() -> tuple[GuardrailSpec, ...]:
    return (
        GuardrailSpec(
            name="protective_action_has_evidence",
            category="business_rule",
            phase="pre",
            check=protective_action_has_evidence,
            on_fail="block",
        ),
    )
