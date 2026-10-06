"""Static policy-as-code validator: every compiled plan — templated or
planner-generated — must pass this before a single node executes. This is
the direct mitigation for "the AST becomes too complex to debug": it keeps
malformed or under-guarded plans from ever reaching the graph runner.
"""

from __future__ import annotations

from collections import defaultdict

from app.graph.plan import END_SLOT, CompiledPlan
from app.nodes.registry import NodeTypeRegistry
from app.nodes.registry import registry as default_registry


class PlanValidationError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def validate_compiled_plan(
    plan: CompiledPlan, *, node_registry: NodeTypeRegistry = default_registry
) -> None:
    errors: list[str] = []
    seen_slots: set[str] = set()

    for step in plan.steps:
        if step.template_slot_id in seen_slots:
            errors.append(f"duplicate template_slot_id '{step.template_slot_id}'")
        seen_slots.add(step.template_slot_id)

        if step.node_type not in node_registry:
            errors.append(
                f"slot '{step.template_slot_id}' references unknown node type '{step.node_type}'"
            )
            continue

        node_def = node_registry.get(step.node_type)
        categories = {g.category for g in node_def.guardrails}

        if not node_def.reversible and "action_irreversibility" not in categories:
            errors.append(
                f"slot '{step.template_slot_id}' ({step.node_type}) is irreversible but "
                "missing an action_irreversibility guardrail"
            )
        if (
            node_def.side_effecting
            and not node_def.reversible
            and "idempotency_replay" not in categories
        ):
            errors.append(
                f"slot '{step.template_slot_id}' ({step.node_type}) is a side-effecting "
                "irreversible node but missing an idempotency_replay guardrail"
            )

    edges = plan.edges()
    if _has_cycle(seen_slots, edges):
        errors.append("compiled plan graph contains a cycle")

    for a, b in edges:
        if a not in seen_slots or (b not in seen_slots and b != END_SLOT):
            errors.append(f"edge ({a} -> {b}) references a slot not present in the plan")

    if errors:
        raise PlanValidationError(errors)


def _has_cycle(nodes: set[str], edges: list[tuple[str, str]]) -> bool:
    adjacency: dict[str, list[str]] = defaultdict(list)
    for a, b in edges:
        adjacency[a].append(b)

    visiting: set[str] = set()
    visited: set[str] = set()

    def dfs(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for neighbor in adjacency[node]:
            if dfs(neighbor):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(dfs(n) for n in nodes)
