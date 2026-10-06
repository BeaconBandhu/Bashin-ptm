"""Concrete guardrails for the payment-authorization flow — the user's own
worked example: verify balance, then only pay if balance >= amount, with a
*separate* set of checks (payee, PIN, amount, daily limit) gating the
payment itself. Every check is a pure function of (ctx, data): no DB/network
calls in here, which is what keeps this module unit-testable without any
infrastructure (see tests/test_payment_guardrails.py).
"""

from __future__ import annotations

from decimal import Decimal

from app.domains.payment.schemas import (
    ExecutePaymentInput,
    ExecutePaymentOutput,
    PaymentAuthorizationInput,
    VerifyBalanceInput,
    VerifyBalanceOutput,
)
from app.guardrails.base import GuardrailCheckResult, GuardrailSpec
from app.nodes.types import NodeContext

MAX_SINGLE_REQUEST_AMOUNT = Decimal(500000)  # sanity ceiling on any one request
HUMAN_APPROVAL_AMOUNT_THRESHOLD = Decimal(50000)  # above this, always ask a human


# ---- verify_balance guardrails -------------------------------------------------


async def entitlement_owns_account(ctx: NodeContext, data: VerifyBalanceInput) -> GuardrailCheckResult:
    # Phase 0: one account per customer, account_id == customer_id.
    if data.account_id != ctx.customer_id:
        return GuardrailCheckResult(
            "fail", f"account '{data.account_id}' does not belong to customer '{ctx.customer_id}'"
        )
    return GuardrailCheckResult("pass")


async def balance_check_spend_ceiling(
    ctx: NodeContext, data: VerifyBalanceInput
) -> GuardrailCheckResult:
    if data.requested_amount <= 0:
        return GuardrailCheckResult("fail", "requested_amount must be positive")
    if data.requested_amount > MAX_SINGLE_REQUEST_AMOUNT:
        return GuardrailCheckResult(
            "fail", f"requested_amount {data.requested_amount} exceeds per-request ceiling"
        )
    return GuardrailCheckResult("pass")


async def account_not_frozen(ctx: NodeContext, data: VerifyBalanceOutput) -> GuardrailCheckResult:
    # Post-guardrail, not pre: the freeze flag only exists on the REAL
    # output of execute() (VerifyBalanceNode already called
    # bank.is_frozen() — see app/domains/payment/nodes.py), so this stays a
    # pure function reading a field rather than performing its own I/O,
    # consistent with every other guardrail in this codebase. This is the
    # payment-domain half of the cross-domain freeze wire: a freeze raised
    # by the customer-support domain's take_protective_action node actually
    # blocks a payment here, not just cosmetically exists somewhere.
    if data.frozen:
        return GuardrailCheckResult(
            "fail", f"account is frozen: {data.freeze_reason or 'protective freeze in effect'}"
        )
    return GuardrailCheckResult("pass")


verify_balance_guardrails = (
    GuardrailSpec(
        name="entitlement_owns_account",
        category="authorization_entitlement",
        phase="pre",
        check=entitlement_owns_account,
        on_fail="block",
    ),
    GuardrailSpec(
        name="balance_check_spend_ceiling",
        category="rate_spend_limit",
        phase="pre",
        check=balance_check_spend_ceiling,
        on_fail="block",
    ),
    GuardrailSpec(
        name="account_not_frozen",
        category="authorization_entitlement",
        phase="post",
        check=account_not_frozen,
        on_fail="block",
    ),
)


# ---- payment_authorization_gate guardrails (guardrail-only composite node) -----


async def payee_input_well_formed(
    ctx: NodeContext, data: PaymentAuthorizationInput
) -> GuardrailCheckResult:
    if data.amount <= 0:
        return GuardrailCheckResult("fail", "amount must be positive")
    if not data.payee_id:
        return GuardrailCheckResult("fail", "payee_id is required")
    return GuardrailCheckResult("pass")


async def payee_is_verified(
    ctx: NodeContext, data: PaymentAuthorizationInput
) -> GuardrailCheckResult:
    if not data.payee_verified:
        return GuardrailCheckResult("fail", f"payee '{data.payee_id}' is not verified for this account")
    return GuardrailCheckResult("pass")


async def upi_pin_is_verified(
    ctx: NodeContext, data: PaymentAuthorizationInput
) -> GuardrailCheckResult:
    if not data.upi_pin_verified:
        return GuardrailCheckResult("fail", "UPI PIN not verified for this session")
    return GuardrailCheckResult("pass")


async def sufficient_balance_and_within_daily_limit(
    ctx: NodeContext, data: PaymentAuthorizationInput
) -> GuardrailCheckResult:
    # Re-checked here even though VerifyBalanceNode already fetched the
    # balance upstream — never trust a staler read for the actual debit
    # decision (closes the TOCTOU gap the architecture plan calls out).
    if data.balance < data.amount:
        return GuardrailCheckResult(
            "fail", f"insufficient balance: balance={data.balance} amount={data.amount}"
        )
    remaining_daily = data.daily_limit - data.daily_spent_so_far
    if data.amount > remaining_daily:
        return GuardrailCheckResult(
            "fail",
            f"amount {data.amount} exceeds remaining daily limit {remaining_daily}",
        )
    return GuardrailCheckResult("pass")


async def requires_human_if_high_value_or_unverified(
    ctx: NodeContext, data: PaymentAuthorizationInput
) -> GuardrailCheckResult:
    if data.amount > HUMAN_APPROVAL_AMOUNT_THRESHOLD:
        return GuardrailCheckResult(
            "fail", f"amount {data.amount} exceeds human-approval threshold {HUMAN_APPROVAL_AMOUNT_THRESHOLD}"
        )
    return GuardrailCheckResult("pass")


payment_authorization_gate_guardrails = (
    GuardrailSpec(
        name="payee_input_well_formed",
        category="input_validation",
        phase="pre",
        check=payee_input_well_formed,
        on_fail="block",
    ),
    GuardrailSpec(
        name="payee_is_verified",
        category="authorization_entitlement",
        phase="pre",
        check=payee_is_verified,
        on_fail="ask_human",
    ),
    GuardrailSpec(
        name="upi_pin_is_verified",
        category="authorization_entitlement",
        phase="pre",
        check=upi_pin_is_verified,
        on_fail="reauth",
    ),
    GuardrailSpec(
        name="sufficient_balance_and_within_daily_limit",
        category="business_rule",
        phase="pre",
        check=sufficient_balance_and_within_daily_limit,
        on_fail="block",
    ),
    GuardrailSpec(
        name="requires_human_if_high_value_or_unverified",
        category="action_irreversibility",
        phase="pre",
        check=requires_human_if_high_value_or_unverified,
        on_fail="ask_human",
    ),
)


# ---- execute_payment guardrails -------------------------------------------------


async def upstream_gate_was_authorized(
    ctx: NodeContext, data: ExecutePaymentInput
) -> GuardrailCheckResult:
    # Defense in depth: refuse to move money unless the upstream
    # PaymentAuthorizationGuardrailNode actually authorized this exact
    # payment — protects against a future template wiring the edges wrong.
    if not data.authorized_by_upstream_gate:
        return GuardrailCheckResult("fail", "no upstream authorization present for this payment")
    return GuardrailCheckResult("pass")


async def idempotency_key_well_formed(
    ctx: NodeContext, data: ExecutePaymentInput
) -> GuardrailCheckResult:
    # Structural check only — the *stateful* replay check runs inside
    # execute() against the IdempotencyStore + the rail's own contract, kept
    # out of this guardrail so guardrails stay pure functions with no I/O.
    if not ctx.idempotency_key or ":" not in ctx.idempotency_key:
        return GuardrailCheckResult("fail", "malformed idempotency key")
    return GuardrailCheckResult("pass")


async def payment_receipt_shape_is_valid(
    ctx: NodeContext, data: ExecutePaymentOutput
) -> GuardrailCheckResult:
    if not data.payment_id or data.status != "SETTLED":
        return GuardrailCheckResult("fail", f"unexpected rail response: {data}")
    return GuardrailCheckResult("pass")


execute_payment_guardrails = (
    GuardrailSpec(
        name="upstream_gate_was_authorized",
        category="action_irreversibility",
        phase="pre",
        check=upstream_gate_was_authorized,
        on_fail="block",
    ),
    GuardrailSpec(
        name="idempotency_key_well_formed",
        category="idempotency_replay",
        phase="pre",
        check=idempotency_key_well_formed,
        on_fail="block",
    ),
    GuardrailSpec(
        name="payment_receipt_shape_is_valid",
        category="output_hallucination_check",
        phase="post",
        check=payment_receipt_shape_is_valid,
        on_fail="block",
    ),
)
