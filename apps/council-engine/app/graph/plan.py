"""A CompiledPlan is the AST instance for one request: an ordered set of
PlanSteps (each bound to a registered node type via `template_slot_id`)
plus the edges between them.

Two step shapes:
  - Linear (`next_slots=None`): implicit edge to whichever step is declared
    immediately after it in `steps` — the payment-authorization template
    uses this exclusively.
  - Branching (`next_slots=(...)` + `router`): after the step completes,
    `router(state)` picks which of `next_slots` to follow next. Use
    `END_SLOT` as a target to terminate the plan. The customer-support
    template is the first to use this — see app/domains/support/template.py
    — for "RAG answered it directly" vs "escalate to the council" and
    "council resolved it" vs "escalate to a human".

Convention: a branch-deciding node puts its own decision in its output
(e.g. `{"route": "respond_to_user"}`) and its router is just
`router_from_node_output(slot_id)` — the decision logic lives once, in the
node's execute(), not duplicated in a router lambda.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel

from app.graph.state import TeammateState

InputMapper = Callable[[TeammateState], BaseModel]
Router = Callable[[TeammateState], str]

END_SLOT = "__end__"


@dataclass(frozen=True, slots=True)
class PlanStep:
    template_slot_id: str
    node_type: str
    input_mapper: InputMapper
    next_slots: tuple[str, ...] | None = None
    router: Router | None = None
    # Whether this step is replaced by a SKIPPED audit passthrough once an
    # upstream failure has set state["halted"] (the default, linear
    # behavior). A branch-deciding step that must itself inspect `halted`
    # to route to an escalation path sets this to False so it still runs.
    skip_on_halt: bool = True
    # Whether a SUCCEEDED result from this step resets state["halted"] to
    # False (and clears halt_reason). `halted` is otherwise sticky by
    # design — once anything fails, everything downstream stays halted —
    # which is correct for a linear chain but wrong for a step whose whole
    # job is to recover from an upstream failure by routing to a designated
    # handler (e.g. an escalate-to-human branch): that handler is the
    # intended way forward, not a dead end, so it must run normally rather
    # than be skipped. Only meaningful alongside skip_on_halt=False.
    clears_halt: bool = False

    def __post_init__(self) -> None:
        if self.next_slots is not None and self.router is None:
            raise ValueError(
                f"step '{self.template_slot_id}' declares next_slots but no router"
            )
        if self.router is not None and self.next_slots is None:
            raise ValueError(
                f"step '{self.template_slot_id}' declares a router but no next_slots"
            )


def router_from_node_output(slot_id: str, field: str = "route", *, default: str | None = None) -> Router:
    """Standard router: read the branch-deciding node's own output field.

    `default` covers the case where a pre-guardrail blocked the node before
    it ran (e.g. `protective_action_has_evidence` rejecting an
    under-evidenced take_protective_action call): there's no output to read
    a decision from, so the router falls back to `default` instead of
    KeyError-ing. Only meaningful for branch-deciding nodes that a
    guardrail can actually block; leave unset (and let it raise) for nodes
    where a missing field would itself be a bug worth surfacing loudly.
    """

    def _route(state: TeammateState) -> str:
        output = state["outputs"][slot_id]
        if field not in output:
            if default is not None:
                return default
            raise KeyError(field)
        return output[field]

    return _route


def terminal_step(
    template_slot_id: str, node_type: str, input_mapper: InputMapper, *, skip_on_halt: bool = True
) -> PlanStep:
    """A step with no successors — routes unconditionally to END_SLOT.

    `skip_on_halt=False` for a terminal step that is itself the designated
    handler for an upstream halt (e.g. escalate_to_human reached because a
    guardrail blocked the step right before it, with no intervening
    clears_halt step) — it must actually execute, not be replaced by a
    SKIPPED passthrough.
    """
    return PlanStep(
        template_slot_id=template_slot_id,
        node_type=node_type,
        input_mapper=input_mapper,
        next_slots=(END_SLOT,),
        router=lambda _state: END_SLOT,
        skip_on_halt=skip_on_halt,
    )


@dataclass(frozen=True, slots=True)
class CompiledPlan:
    domain: str
    steps: tuple[PlanStep, ...]

    def edges(self) -> list[tuple[str, str]]:
        edges: list[tuple[str, str]] = []
        for i, step in enumerate(self.steps):
            if step.next_slots is not None:
                edges.extend((step.template_slot_id, target) for target in step.next_slots)
            elif i + 1 < len(self.steps):
                edges.append((step.template_slot_id, self.steps[i + 1].template_slot_id))
        return edges

    def slot_ids(self) -> set[str]:
        return {s.template_slot_id for s in self.steps}
