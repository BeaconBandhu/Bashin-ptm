"""Node-type registry: the single source of truth every plan compiler,
validator, and graph builder consults. Registering a node type declares its
contract (schemas, risk tier, reversibility, attached guardrails) up front —
nodes are never constructed ad hoc at execution time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import BaseModel

from app.nodes.types import NodeExecutable, RiskTier

if TYPE_CHECKING:
    from app.guardrails.base import GuardrailSpec


@dataclass(frozen=True, slots=True)
class NodeTypeDef:
    name: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    executable: type[NodeExecutable]
    risk_tier: RiskTier = "deterministic"
    reversible: bool = True
    uses_memory: bool = False
    guardrails: tuple[GuardrailSpec, ...] = field(default_factory=tuple)
    version: int = 1
    # A guardrail-only composite node (e.g. PaymentAuthorizationGuardrailNode)
    # has no real side effect of its own — it exists purely to gate the next
    # node. The validator treats these specially (see app/planning/validator.py).
    side_effecting: bool = True


class NodeTypeRegistry:
    def __init__(self) -> None:
        self._types: dict[str, NodeTypeDef] = {}

    def register(self, definition: NodeTypeDef) -> None:
        if definition.name in self._types:
            raise ValueError(f"node type '{definition.name}' already registered")
        if not definition.reversible and not any(
            g.category == "action_irreversibility" for g in definition.guardrails
        ):
            raise ValueError(
                f"node type '{definition.name}' is irreversible but has no "
                "action_irreversibility guardrail attached"
            )
        self._types[definition.name] = definition

    def get(self, name: str) -> NodeTypeDef:
        try:
            return self._types[name]
        except KeyError as exc:
            raise KeyError(f"unknown node type '{name}'") from exc

    def __contains__(self, name: str) -> bool:
        return name in self._types

    def all(self) -> dict[str, NodeTypeDef]:
        return dict(self._types)


registry = NodeTypeRegistry()


def register_node_type(
    name: str,
    *,
    input_schema: type[BaseModel],
    output_schema: type[BaseModel],
    risk_tier: RiskTier = "deterministic",
    reversible: bool = True,
    uses_memory: bool = False,
    guardrails: tuple[GuardrailSpec, ...] = (),
    version: int = 1,
    side_effecting: bool = True,
):
    """Class decorator: @register_node_type("verify_balance", input_schema=..., ...)"""

    def _wrap(cls: type[NodeExecutable]) -> type[NodeExecutable]:
        registry.register(
            NodeTypeDef(
                name=name,
                input_schema=input_schema,
                output_schema=output_schema,
                executable=cls,
                risk_tier=risk_tier,
                reversible=reversible,
                uses_memory=uses_memory,
                guardrails=guardrails,
                version=version,
                side_effecting=side_effecting,
            )
        )
        return cls

    return _wrap
