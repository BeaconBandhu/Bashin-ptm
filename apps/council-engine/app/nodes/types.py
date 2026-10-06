"""Core types shared by every node type and the graph/guardrail layers.

A "node" here is the AST unit: one typed, traceable step in a plan. LangGraph
nodes *are* these AST nodes (see app/graph/builder.py) — there is one
execution engine, not two.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel


class NodeStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED_BY_GUARDRAIL = "BLOCKED_BY_GUARDRAIL"
    AWAITING_HUMAN = "AWAITING_HUMAN"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


RiskTier = Literal["deterministic", "single_model", "council"]

# Who produced this node instance in the compiled plan — feeds the audit
# trail and the static validator (freeform planner output is validated
# harder than a fixed template).
NodeOrigin = Literal["template", "planner_llm", "human_override"]


class NodeContext(BaseModel):
    """Everything a node's execute() may read. Deliberately does NOT include
    a raw DB/session handle — nodes stay testable as near-pure functions;
    persistence happens in the guardrail middleware and the graph runner."""

    session_id: str
    tenant_id: str
    customer_id: str
    tree_id: str
    node_id: str
    template_slot_id: str
    idempotency_key: str
    retry_count: int = 0
    memory: dict[str, Any] = {}  # pre-fetched by the runner when uses_memory=True

    model_config = {"frozen": True}


@dataclass(slots=True)
class NodeResult:
    output: BaseModel | None
    status: NodeStatus
    error: str | None = None
    confidence: float | None = None  # set by single_model/council nodes
    metadata: dict[str, Any] = field(default_factory=dict)


class NodeExecutable(Protocol):
    async def execute(self, ctx: NodeContext, input: BaseModel) -> NodeResult: ...
