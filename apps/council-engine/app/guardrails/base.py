"""Guardrail middleware: wraps every node's execute(), not a global filter.

Design invariants (do not relax without updating the plan doc):
  - Pre-guardrails gate whether execute() runs at all. For an irreversible
    action node, all decision-gating must happen in pre-guardrails on that
    node OR on an upstream guardrail-only composite node — never after the
    side effect has already fired.
  - Post-guardrails validate the REAL output of execute() (schema shape,
    business-rule consistency, hallucination checks on drafted content).
    They can still fail the node even though execute() ran, e.g. reject
    output before it reaches the user.
  - Guardrail checks are pure functions of (ctx, data) — no DB/network
    access inside a check — so this whole module is unit-testable without
    any infrastructure. Anything a check needs (balance, entitlement, prior
    idempotency claims) is fetched by the node's execute() and passed in.
    ONE documented exception: `ai_spend_limit` checks (app/domains/support/
    guardrails.py) read a live cumulative-spend total, because the entire
    point of a spend cap is knowing the real total at decision time, right
    before the call it might block — computing it any other way defeats
    the guardrail. It's still a single fast read (in-memory or one Mongo
    lookup), not a chain of calls, and still fails closed on error.
  - `category` values below are the fixed vocabulary from the architecture
    plan; the validator (app/planning/validator.py) checks required
    categories are present per risk tier / reversibility.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from app.nodes.types import NodeContext, NodeResult, NodeStatus

GuardrailCategory = Literal[
    "input_validation",
    "authorization_entitlement",
    "business_rule",
    "pii_data_boundary",
    "action_irreversibility",
    "rate_spend_limit",
    "output_hallucination_check",
    "idempotency_replay",
    "ai_spend_limit",
]

GuardrailPhase = Literal["pre", "post"]
GuardrailDisposition = Literal["block", "ask_human", "reauth", "retry_with_correction"]
GuardrailVerdictKind = Literal["pass", "warn", "fail"]


@dataclass(slots=True)
class GuardrailCheckResult:
    verdict: GuardrailVerdictKind
    reason: str | None = None


GuardrailCheck = Callable[[NodeContext, Any], Awaitable[GuardrailCheckResult]]


@dataclass(frozen=True, slots=True)
class GuardrailSpec:
    name: str
    category: GuardrailCategory
    phase: GuardrailPhase
    check: GuardrailCheck
    on_fail: GuardrailDisposition = "block"


@dataclass(slots=True)
class GuardrailVerdict:
    name: str
    category: GuardrailCategory
    phase: GuardrailPhase
    verdict: GuardrailVerdictKind
    reason: str | None
    latency_ms: float
    on_fail: GuardrailDisposition


class GuardrailBlocked(Exception):
    """Raised internally to short-circuit execution; callers should prefer
    inspecting the returned NodeResult.status rather than catching this."""


async def _run_one(spec: GuardrailSpec, ctx: NodeContext, data: Any) -> GuardrailVerdict:
    start = time.perf_counter()
    try:
        outcome = await spec.check(ctx, data)
    except Exception as exc:  # a guardrail that throws is a fail-closed bug, never fail-open
        outcome = GuardrailCheckResult(verdict="fail", reason=f"guardrail raised: {exc!r}")
    latency_ms = (time.perf_counter() - start) * 1000
    return GuardrailVerdict(
        name=spec.name,
        category=spec.category,
        phase=spec.phase,
        verdict=outcome.verdict,
        reason=outcome.reason,
        latency_ms=latency_ms,
        on_fail=spec.on_fail,
    )


async def run_guardrails(
    specs: tuple[GuardrailSpec, ...], ctx: NodeContext, data: Any
) -> list[GuardrailVerdict]:
    """Runs all guardrails of one phase in parallel (they must be independent
    pure checks — this parallelism is the primary latency mitigation for
    guardrail-heavy nodes, per the architecture plan's latency budget)."""
    if not specs:
        return []
    return list(await asyncio.gather(*(_run_one(spec, ctx, data) for spec in specs)))


def first_blocking(verdicts: list[GuardrailVerdict]) -> GuardrailVerdict | None:
    for v in verdicts:
        if v.verdict == "fail":
            return v
    return None


def has_warn(verdicts: list[GuardrailVerdict]) -> bool:
    """A 'warn' verdict (couldn't decide cleanly) is the signal that promotes
    a single_model node to full council treatment at runtime — see the
    council integration policy in the architecture plan."""
    return any(v.verdict == "warn" for v in verdicts)


def _status_for_disposition(disposition: GuardrailDisposition) -> NodeStatus:
    return {
        "block": NodeStatus.BLOCKED_BY_GUARDRAIL,
        "ask_human": NodeStatus.AWAITING_HUMAN,
        "reauth": NodeStatus.AWAITING_HUMAN,
        "retry_with_correction": NodeStatus.FAILED,  # runner decides whether to retry
    }[disposition]


@dataclass(slots=True)
class GuardedExecutionOutcome:
    result: NodeResult
    verdicts: list[GuardrailVerdict]


async def guarded_execute(
    *,
    pre_specs: tuple[GuardrailSpec, ...],
    post_specs: tuple[GuardrailSpec, ...],
    ctx: NodeContext,
    input_data: Any,
    execute: Callable[[NodeContext, Any], Awaitable[NodeResult]],
) -> GuardedExecutionOutcome:
    verdicts: list[GuardrailVerdict] = []

    pre_verdicts = await run_guardrails(pre_specs, ctx, input_data)
    verdicts.extend(pre_verdicts)
    blocking = first_blocking(pre_verdicts)
    if blocking is not None:
        blocked = NodeResult(
            output=None,
            status=_status_for_disposition(blocking.on_fail),
            error=f"blocked by pre-guardrail '{blocking.name}': {blocking.reason}",
        )
        return GuardedExecutionOutcome(result=blocked, verdicts=verdicts)

    result = await execute(ctx, input_data)

    if result.status == NodeStatus.SUCCEEDED and post_specs:
        post_verdicts = await run_guardrails(post_specs, ctx, result.output)
        verdicts.extend(post_verdicts)
        blocking = first_blocking(post_verdicts)
        if blocking is not None:
            result = NodeResult(
                output=result.output,
                status=_status_for_disposition(blocking.on_fail),
                error=f"blocked by post-guardrail '{blocking.name}': {blocking.reason}",
                metadata=result.metadata,
            )

    return GuardedExecutionOutcome(result=result, verdicts=verdicts)
