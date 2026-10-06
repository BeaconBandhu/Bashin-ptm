from decimal import Decimal

import pytest

from app.domains.payment import nodes as _payment_nodes  # noqa: F401
from app.domains.payment.template import build_payment_authorization_plan
from app.graph.plan import CompiledPlan, PlanStep
from app.planning.validator import PlanValidationError, _has_cycle, validate_compiled_plan


def test_validator_rejects_unknown_node_type():
    bad_step = PlanStep("mystery", "not_a_real_node_type", lambda state: None)
    plan = CompiledPlan(domain="bogus", steps=(bad_step,))
    with pytest.raises(PlanValidationError) as exc_info:
        validate_compiled_plan(plan)
    assert any("unknown node type" in e for e in exc_info.value.errors)


def test_validator_rejects_duplicate_slot_ids():
    step_a = PlanStep("dup", "verify_balance", lambda state: None)
    step_b = PlanStep("dup", "balance_threshold_decision", lambda state: None)
    plan = CompiledPlan(domain="bogus", steps=(step_a, step_b))
    with pytest.raises(PlanValidationError) as exc_info:
        validate_compiled_plan(plan)
    assert any("duplicate template_slot_id" in e for e in exc_info.value.errors)


def test_validator_accepts_the_real_payment_template():
    plan = build_payment_authorization_plan(
        account_id="cust-1",
        payee_id="payee-1",
        amount=Decimal(100),
        currency="INR",
        payee_verified=True,
        upi_pin_verified=True,
        daily_limit=Decimal(10000),
    )
    validate_compiled_plan(plan)  # must not raise


def test_has_cycle_detects_a_cycle():
    assert _has_cycle({"a", "b"}, [("a", "b"), ("b", "a")]) is True


def test_has_cycle_false_for_linear_chain():
    assert _has_cycle({"a", "b", "c"}, [("a", "b"), ("b", "c")]) is False
