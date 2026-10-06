"""The payment-authorization domain template: a fixed, linear backbone DAG.
Per the architecture plan, domain templates are the primary structural
defense against "the AST becomes too complex to debug" — request-specific
parameters are bound into input_mapper closures at build time, not left for
a planner LLM to assemble freely.

verify_balance -> balance_threshold_decision -> payment_authorization_gate
-> execute_payment -> notify_user
"""

from __future__ import annotations

from decimal import Decimal

from app.domains.payment.schemas import (
    ExecutePaymentInput,
    NotifyUserInput,
    PaymentAuthorizationInput,
    ThresholdDecisionInput,
    VerifyBalanceInput,
)
from app.graph.plan import CompiledPlan, PlanStep
from app.graph.state import TeammateState


def build_payment_authorization_plan(
    *,
    account_id: str,
    payee_id: str,
    amount: Decimal,
    currency: str,
    payee_verified: bool,
    upi_pin_verified: bool,
    daily_limit: Decimal,
) -> CompiledPlan:
    # Phase-0 scope cut, not a security design: `payee_verified` and
    # `upi_pin_verified` are accepted here as already-resolved trust
    # signals. In Phase 1+ these must come from a CRM-lookup node and a real
    # PIN-verification step respectively — never taken as a raw, unverified
    # claim on the inbound API request — since that would let a caller
    # simply assert its way past the authorization_entitlement guardrails.

    def verify_balance_input(state: TeammateState) -> VerifyBalanceInput:
        return VerifyBalanceInput(account_id=account_id, requested_amount=amount)

    def threshold_input(state: TeammateState) -> ThresholdDecisionInput:
        vb = state["outputs"]["verify_balance"]
        return ThresholdDecisionInput(balance=vb["balance"], requested_amount=amount)

    def gate_input(state: TeammateState) -> PaymentAuthorizationInput:
        vb = state["outputs"]["verify_balance"]
        return PaymentAuthorizationInput(
            account_id=account_id,
            payee_id=payee_id,
            payee_verified=payee_verified,
            amount=amount,
            currency=currency,
            balance=vb["balance"],
            daily_limit=daily_limit,
            daily_spent_so_far=vb["daily_spent_so_far"],
            upi_pin_verified=upi_pin_verified,
        )

    def execute_input(state: TeammateState) -> ExecutePaymentInput:
        gate = state["outputs"]["payment_authorization_gate"]
        return ExecutePaymentInput(
            account_id=account_id,
            payee_id=payee_id,
            amount=amount,
            currency=currency,
            authorized_by_upstream_gate=gate.get("authorized", False),
        )

    def notify_input(state: TeammateState) -> NotifyUserInput:
        payment = state["outputs"]["execute_payment"]
        return NotifyUserInput(
            payment_id=payment["payment_id"], amount=amount, currency=currency, payee_id=payee_id
        )

    steps = (
        PlanStep("verify_balance", "verify_balance", verify_balance_input),
        PlanStep(
            "balance_threshold_decision", "balance_threshold_decision", threshold_input
        ),
        PlanStep("payment_authorization_gate", "payment_authorization_gate", gate_input),
        PlanStep("execute_payment", "execute_payment", execute_input),
        PlanStep("notify_user", "notify_user", notify_input),
    )
    return CompiledPlan(domain="payment_authorization", steps=steps)
