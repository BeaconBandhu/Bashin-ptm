"""Dependency injection for the payment-authorization template: binds
concrete (currently mock) integration clients to the node types that need
them. Swapping mocks for a real bank/CRM/payment-rail sandbox later means
changing only this file's construction — node and guardrail code is
untouched, matching the architecture plan's "config swap, not a rewrite"
requirement.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.concurrency.idempotency import IdempotencyStore
from app.domains.payment.nodes import (
    BalanceThresholdDecisionNode,
    ExecutePaymentNode,
    NotifyUserNode,
    PaymentAuthorizationGuardrailNode,
    VerifyBalanceNode,
)
from app.integrations.protocols import BankClient, CRMClient, PaymentRailClient
from app.nodes.types import NodeExecutable


@dataclass(frozen=True, slots=True)
class PaymentDependencies:
    bank: BankClient
    payment_rail: PaymentRailClient
    crm: CRMClient
    idempotency_store: IdempotencyStore


def build_node_instances(deps: PaymentDependencies) -> dict[str, NodeExecutable]:
    return {
        "verify_balance": VerifyBalanceNode(deps.bank),
        "balance_threshold_decision": BalanceThresholdDecisionNode(),
        "payment_authorization_gate": PaymentAuthorizationGuardrailNode(),
        "execute_payment": ExecutePaymentNode(deps.payment_rail, deps.idempotency_store),
        "notify_user": NotifyUserNode(),
    }
