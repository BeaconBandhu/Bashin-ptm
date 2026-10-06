"""Node types for the payment-authorization template. All `deterministic`
risk tier (Phase 0 is zero-LLM) — see app/domains/payment/template.py for
how they're wired into a CompiledPlan, and guardrails.py for what gates
each one.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.concurrency.idempotency import IdempotencyStore
from app.domains.payment.guardrails import (
    execute_payment_guardrails,
    payment_authorization_gate_guardrails,
    verify_balance_guardrails,
)
from app.domains.payment.schemas import (
    ExecutePaymentInput,
    ExecutePaymentOutput,
    NotifyUserInput,
    NotifyUserOutput,
    PaymentAuthorizationInput,
    PaymentAuthorizationOutput,
    ThresholdDecisionInput,
    ThresholdDecisionOutput,
    VerifyBalanceInput,
    VerifyBalanceOutput,
)
from app.integrations.protocols import BankClient, DuplicatePaymentError, PaymentRailClient
from app.nodes.registry import register_node_type
from app.nodes.types import NodeContext, NodeResult, NodeStatus


@register_node_type(
    "verify_balance",
    input_schema=VerifyBalanceInput,
    output_schema=VerifyBalanceOutput,
    risk_tier="deterministic",
    reversible=True,
    guardrails=verify_balance_guardrails,
)
class VerifyBalanceNode:
    def __init__(self, bank_client: BankClient) -> None:
        self._bank = bank_client

    async def execute(self, ctx: NodeContext, input: VerifyBalanceInput) -> NodeResult:
        try:
            balance, currency = await self._bank.get_balance(input.account_id)
            daily_spent_so_far = await self._bank.get_daily_spent(input.account_id)
            freeze_record = await self._bank.is_frozen(input.account_id)
        except TimeoutError as exc:
            return NodeResult(output=None, status=NodeStatus.FAILED, error=f"bank timeout: {exc}")
        output = VerifyBalanceOutput(
            account_id=input.account_id,
            balance=balance,
            currency=currency,
            daily_spent_so_far=daily_spent_so_far,
            checked_at=datetime.now(UTC),
            frozen=freeze_record is not None,
            freeze_reason=getattr(freeze_record, "reason", None),
        )
        return NodeResult(output=output, status=NodeStatus.SUCCEEDED)


@register_node_type(
    "balance_threshold_decision",
    input_schema=ThresholdDecisionInput,
    output_schema=ThresholdDecisionOutput,
    risk_tier="deterministic",
    reversible=True,
    guardrails=(),  # pure predicate, no LLM, nothing to gate beyond input typing
)
class BalanceThresholdDecisionNode:
    async def execute(self, ctx: NodeContext, input: ThresholdDecisionInput) -> NodeResult:
        output = ThresholdDecisionOutput(
            sufficient=input.balance >= input.requested_amount,
            balance=input.balance,
            requested_amount=input.requested_amount,
        )
        return NodeResult(output=output, status=NodeStatus.SUCCEEDED)


@register_node_type(
    "payment_authorization_gate",
    input_schema=PaymentAuthorizationInput,
    output_schema=PaymentAuthorizationOutput,
    risk_tier="deterministic",
    reversible=True,  # the gate itself has no side effect — see side_effecting below
    side_effecting=False,
    guardrails=payment_authorization_gate_guardrails,
)
class PaymentAuthorizationGuardrailNode:
    """Guardrail-only composite node: all the real decision-gating for the
    payment lives in its attached guardrails (payee verification, UPI PIN,
    balance + daily-limit re-check, human-approval threshold). If every
    guardrail passes, this node simply certifies the payment as authorized —
    it never itself moves money."""

    async def execute(self, ctx: NodeContext, input: PaymentAuthorizationInput) -> NodeResult:
        return NodeResult(output=PaymentAuthorizationOutput(authorized=True), status=NodeStatus.SUCCEEDED)


@register_node_type(
    "execute_payment",
    input_schema=ExecutePaymentInput,
    output_schema=ExecutePaymentOutput,
    risk_tier="deterministic",
    reversible=False,  # the one genuinely irreversible node in this template
    guardrails=execute_payment_guardrails,
)
class ExecutePaymentNode:
    def __init__(self, payment_rail: PaymentRailClient, idempotency_store: IdempotencyStore) -> None:
        self._rail = payment_rail
        self._idempotency = idempotency_store

    async def execute(self, ctx: NodeContext, input: ExecutePaymentInput) -> NodeResult:
        # App-level claim first (fast, in-process/Redis); the rail's own
        # contract is the second, independent line of defense.
        newly_claimed = await self._idempotency.claim(ctx.idempotency_key)
        if not newly_claimed:
            return NodeResult(
                output=None,
                status=NodeStatus.FAILED,
                error=f"idempotency key '{ctx.idempotency_key}' already claimed — refusing to pay twice",
            )
        try:
            receipt = await self._rail.execute_payment(
                idempotency_key=ctx.idempotency_key,
                account_id=input.account_id,
                payee_id=input.payee_id,
                amount=input.amount,
                currency=input.currency,
            )
        except TimeoutError as exc:
            return NodeResult(output=None, status=NodeStatus.FAILED, error=f"payment rail timeout: {exc}")
        except DuplicatePaymentError as exc:
            return NodeResult(output=None, status=NodeStatus.FAILED, error=str(exc))

        output = ExecutePaymentOutput(
            payment_id=receipt.payment_id, status=receipt.status, executed_at=receipt.executed_at
        )
        return NodeResult(output=output, status=NodeStatus.SUCCEEDED)


@register_node_type(
    "notify_user",
    input_schema=NotifyUserInput,
    output_schema=NotifyUserOutput,
    risk_tier="deterministic",  # Phase 1 swaps this for a single_model drafting node
    reversible=True,
    guardrails=(),
)
class NotifyUserNode:
    async def execute(self, ctx: NodeContext, input: NotifyUserInput) -> NodeResult:
        message = (
            f"Paid {input.amount} {input.currency} to {input.payee_id}. "
            f"Reference: {input.payment_id}."
        )
        return NodeResult(output=NotifyUserOutput(message=message), status=NodeStatus.SUCCEEDED)
