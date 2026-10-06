"""Compiles a validated CompiledPlan into an executable LangGraph
StateGraph. LangGraph nodes ARE the AST nodes — this is the one execution
engine the architecture plan commits to (no separate AST interpreter).

Every step is wrapped identically:
  1. if the run is already halted (an earlier step failed/blocked) AND this
     step is configured to skip on halt (the default), record a SKIPPED
     audit event and pass state through unchanged. A branch-deciding step
     that must itself route to an escalation path when something upstream
     halted sets `skip_on_halt=False` so it still runs — see
     app/graph/plan.py.
  2. otherwise resolve this step's input from state, run it through the
     guardrail middleware + the node's execute(), and persist exactly one
     hash-chained audit event regardless of outcome;
  3. update `outputs`, `node_records`, `guardrail_verdicts`, `last_status`,
     and `halted` in state for the next step / the API response.

Edges: a linear step (`next_slots=None`) gets a plain edge to whichever step
is declared immediately after it (or END if it's last). A branching step
gets `add_conditional_edges` using its own `router`, which by convention
just reads the decision the step already put in its own output — see
`router_from_node_output` in app/graph/plan.py.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.audit.hash_chain import hash_payload
from app.audit.store import AuditStore
from app.concurrency.idempotency import derive_idempotency_key
from app.graph.plan import END_SLOT, CompiledPlan, PlanStep
from app.graph.state import TeammateState
from app.guardrails.base import guarded_execute
from app.nodes.registry import NodeTypeRegistry
from app.nodes.registry import registry as default_registry
from app.nodes.types import NodeContext, NodeExecutable, NodeStatus


def _split_guardrails(node_def) -> tuple[tuple, tuple]:
    pre = tuple(g for g in node_def.guardrails if g.phase == "pre")
    post = tuple(g for g in node_def.guardrails if g.phase == "post")
    return pre, post


def _make_step_runner(
    step: PlanStep,
    node_def,
    instance: NodeExecutable,
    audit_store: AuditStore,
):
    pre_specs, post_specs = _split_guardrails(node_def)

    async def _run(state: TeammateState) -> dict[str, Any]:
        node_id = uuid.uuid4().hex

        if state["halted"] and step.skip_on_halt:
            event = await audit_store.append_node_event(
                session_id=state["session_id"],
                node_id=node_id,
                template_slot_id=step.template_slot_id,
                status=NodeStatus.SKIPPED.value,
                inputs={},
                outputs={},
                guardrail_verdicts=[],
            )
            return {
                "node_records": [*state["node_records"], event],
            }

        input_model = step.input_mapper(state)
        input_dict = input_model.model_dump(mode="json")
        idempotency_key = derive_idempotency_key(
            state["session_id"], step.template_slot_id, hash_payload(input_dict)
        )

        ctx = NodeContext(
            session_id=state["session_id"],
            tenant_id=state["tenant_id"],
            customer_id=state["customer_id"],
            tree_id=state["tree_id"],
            node_id=node_id,
            template_slot_id=step.template_slot_id,
            idempotency_key=idempotency_key,
        )

        outcome = await guarded_execute(
            pre_specs=pre_specs,
            post_specs=post_specs,
            ctx=ctx,
            input_data=input_model,
            execute=instance.execute,
        )

        if outcome.result.output is not None:
            output_dict = outcome.result.output.model_dump(mode="json")
        elif outcome.result.error:
            # Failed/blocked nodes have no output payload — persist the
            # error in its place so the audit trail explains *why*, not
            # just that a status changed.
            output_dict = {"error": outcome.result.error}
        else:
            output_dict = {}
        verdict_dicts = [asdict(v) for v in outcome.verdicts]

        event = await audit_store.append_node_event(
            session_id=state["session_id"],
            node_id=node_id,
            template_slot_id=step.template_slot_id,
            status=outcome.result.status.value,
            inputs=input_dict,
            outputs=output_dict,
            guardrail_verdicts=verdict_dicts,
        )

        newly_halted = outcome.result.status != NodeStatus.SUCCEEDED
        if step.clears_halt and outcome.result.status == NodeStatus.SUCCEEDED:
            new_halted, new_halt_reason = False, None
        else:
            new_halted = state["halted"] or newly_halted
            new_halt_reason = state["halt_reason"] or outcome.result.error
        return {
            "outputs": {**state["outputs"], step.template_slot_id: output_dict},
            "node_records": [*state["node_records"], event],
            "guardrail_verdicts": [*state["guardrail_verdicts"], *verdict_dicts],
            "last_status": outcome.result.status.value,
            "halted": new_halted,
            "halt_reason": new_halt_reason,
        }

    return _run


def build_graph(
    plan: CompiledPlan,
    node_instances: dict[str, NodeExecutable],
    audit_store: AuditStore,
    *,
    node_registry: NodeTypeRegistry = default_registry,
    checkpointer=None,
):
    """`node_instances` maps node_type name -> an already-constructed
    executable (dependencies injected by the caller, e.g. the mock bank/CRM/
    payment-rail clients for Phase 0). Returns a compiled, runnable graph."""

    graph: StateGraph = StateGraph(TeammateState)

    for step in plan.steps:
        node_def = node_registry.get(step.node_type)
        instance = node_instances[step.node_type]
        graph.add_node(step.template_slot_id, _make_step_runner(step, node_def, instance, audit_store))

    for i, step in enumerate(plan.steps):
        if step.next_slots is None:
            # Linear: edge to whichever step is declared immediately after
            # this one, or END if this is the last declared step.
            target = plan.steps[i + 1].template_slot_id if i + 1 < len(plan.steps) else END
            graph.add_edge(step.template_slot_id, target)
        else:
            route_map = {slot: (END if slot == END_SLOT else slot) for slot in step.next_slots}
            graph.add_conditional_edges(step.template_slot_id, step.router, route_map)

    if plan.steps:
        graph.add_edge(START, plan.steps[0].template_slot_id)

    return graph.compile(checkpointer=checkpointer or MemorySaver())
