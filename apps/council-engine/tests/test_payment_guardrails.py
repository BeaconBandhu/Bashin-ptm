"""Guardrail logic is pure functions — the safety-critical layer the
architecture plan calls out for near-100% coverage. No DB, no mocks, no
event loop infra beyond pytest-asyncio needed."""

from datetime import UTC, datetime
from decimal import Decimal

from app.domains.payment import guardrails as g
from app.domains.payment.schemas import (
    ExecutePaymentInput,
    ExecutePaymentOutput,
    PaymentAuthorizationInput,
    VerifyBalanceInput,
)
from app.nodes.types import NodeContext


def make_ctx(**overrides) -> NodeContext:
    base = dict(
        session_id="s1",
        tenant_id="t1",
        customer_id="cust-1",
        tree_id="s1",
        node_id="n1",
        template_slot_id="slot",
        idempotency_key="idem:s1:slot:abc123",
    )
    base.update(overrides)
    return NodeContext(**base)


def auth_input(**overrides) -> PaymentAuthorizationInput:
    base = dict(
        account_id="cust-1",
        payee_id="payee-1",
        payee_verified=True,
        amount=Decimal(1000),
        currency="INR",
        balance=Decimal(5000),
        daily_limit=Decimal(20000),
        daily_spent_so_far=Decimal(0),
        upi_pin_verified=True,
    )
    base.update(overrides)
    return PaymentAuthorizationInput(**base)


async def test_entitlement_passes_when_account_matches_customer():
    result = await g.entitlement_owns_account(
        make_ctx(customer_id="cust-1"), VerifyBalanceInput(account_id="cust-1", requested_amount=Decimal(100))
    )
    assert result.verdict == "pass"


async def test_entitlement_fails_when_account_mismatched():
    result = await g.entitlement_owns_account(
        make_ctx(customer_id="cust-1"), VerifyBalanceInput(account_id="cust-2", requested_amount=Decimal(100))
    )
    assert result.verdict == "fail"


async def test_spend_ceiling_rejects_nonpositive_amount():
    result = await g.balance_check_spend_ceiling(
        make_ctx(), VerifyBalanceInput(account_id="cust-1", requested_amount=Decimal(0))
    )
    assert result.verdict == "fail"


async def test_spend_ceiling_rejects_over_max():
    result = await g.balance_check_spend_ceiling(
        make_ctx(),
        VerifyBalanceInput(account_id="cust-1", requested_amount=g.MAX_SINGLE_REQUEST_AMOUNT + 1),
    )
    assert result.verdict == "fail"


async def test_spend_ceiling_passes_within_bounds():
    result = await g.balance_check_spend_ceiling(
        make_ctx(), VerifyBalanceInput(account_id="cust-1", requested_amount=Decimal(1000))
    )
    assert result.verdict == "pass"


async def test_well_formed_passes():
    result = await g.payee_input_well_formed(make_ctx(), auth_input())
    assert result.verdict == "pass"


async def test_well_formed_rejects_nonpositive_amount():
    result = await g.payee_input_well_formed(make_ctx(), auth_input(amount=Decimal(0)))
    assert result.verdict == "fail"


async def test_well_formed_rejects_missing_payee():
    result = await g.payee_input_well_formed(make_ctx(), auth_input(payee_id=""))
    assert result.verdict == "fail"


async def test_payee_verification_required():
    result = await g.payee_is_verified(make_ctx(), auth_input(payee_verified=False))
    assert result.verdict == "fail"


async def test_payee_verification_passes_when_verified():
    result = await g.payee_is_verified(make_ctx(), auth_input(payee_verified=True))
    assert result.verdict == "pass"


async def test_upi_pin_required():
    result = await g.upi_pin_is_verified(make_ctx(), auth_input(upi_pin_verified=False))
    assert result.verdict == "fail"


async def test_insufficient_balance_blocked():
    result = await g.sufficient_balance_and_within_daily_limit(
        make_ctx(), auth_input(balance=Decimal(500), amount=Decimal(1000))
    )
    assert result.verdict == "fail"


async def test_over_daily_limit_blocked():
    result = await g.sufficient_balance_and_within_daily_limit(
        make_ctx(),
        auth_input(daily_limit=Decimal(1000), daily_spent_so_far=Decimal(900), amount=Decimal(200)),
    )
    assert result.verdict == "fail"


async def test_within_limits_passes():
    result = await g.sufficient_balance_and_within_daily_limit(make_ctx(), auth_input())
    assert result.verdict == "pass"


async def test_high_value_requires_human():
    result = await g.requires_human_if_high_value_or_unverified(
        make_ctx(), auth_input(amount=g.HUMAN_APPROVAL_AMOUNT_THRESHOLD + Decimal(1))
    )
    assert result.verdict == "fail"


async def test_low_value_does_not_require_human():
    result = await g.requires_human_if_high_value_or_unverified(make_ctx(), auth_input())
    assert result.verdict == "pass"


async def test_upstream_gate_required():
    data = ExecutePaymentInput(
        account_id="cust-1", payee_id="payee-1", amount=Decimal(100), currency="INR",
        authorized_by_upstream_gate=False,
    )
    result = await g.upstream_gate_was_authorized(make_ctx(), data)
    assert result.verdict == "fail"


async def test_upstream_gate_passes_when_authorized():
    data = ExecutePaymentInput(
        account_id="cust-1", payee_id="payee-1", amount=Decimal(100), currency="INR",
        authorized_by_upstream_gate=True,
    )
    result = await g.upstream_gate_was_authorized(make_ctx(), data)
    assert result.verdict == "pass"


async def test_idempotency_key_format_rejected():
    data = ExecutePaymentInput(
        account_id="cust-1", payee_id="payee-1", amount=Decimal(100), currency="INR",
        authorized_by_upstream_gate=True,
    )
    result = await g.idempotency_key_well_formed(make_ctx(idempotency_key="malformed-no-colon"), data)
    assert result.verdict == "fail"


async def test_receipt_shape_rejects_non_settled():
    output = ExecutePaymentOutput(payment_id="pay_1", status="PENDING", executed_at=datetime.now(UTC))
    result = await g.payment_receipt_shape_is_valid(make_ctx(), output)
    assert result.verdict == "fail"


async def test_receipt_shape_accepts_settled():
    output = ExecutePaymentOutput(payment_id="pay_1", status="SETTLED", executed_at=datetime.now(UTC))
    result = await g.payment_receipt_shape_is_valid(make_ctx(), output)
    assert result.verdict == "pass"
