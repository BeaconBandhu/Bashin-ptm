"""Pydantic input/output schemas for the payment-authorization template's
node types. Decimal (not float) throughout — this handles real money."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class VerifyBalanceInput(BaseModel):
    account_id: str
    requested_amount: Decimal


class VerifyBalanceOutput(BaseModel):
    account_id: str
    balance: Decimal
    currency: str
    daily_spent_so_far: Decimal
    checked_at: datetime
    # Populated by a real bank.is_frozen() check inside execute() — see
    # account_not_frozen in guardrails.py. A freeze raised from the
    # customer-support domain (take_protective_action) is enforced here,
    # not just recorded — the cross-domain wire the architecture plan calls
    # out as necessary for freezing to mean anything.
    frozen: bool = False
    freeze_reason: str | None = None


class ThresholdDecisionInput(BaseModel):
    balance: Decimal
    requested_amount: Decimal


class ThresholdDecisionOutput(BaseModel):
    sufficient: bool
    balance: Decimal
    requested_amount: Decimal


class PaymentAuthorizationInput(BaseModel):
    account_id: str
    payee_id: str
    payee_verified: bool
    amount: Decimal
    currency: str
    balance: Decimal
    daily_limit: Decimal
    daily_spent_so_far: Decimal
    upi_pin_verified: bool


class PaymentAuthorizationOutput(BaseModel):
    authorized: bool


class ExecutePaymentInput(BaseModel):
    account_id: str
    payee_id: str
    amount: Decimal
    currency: str
    authorized_by_upstream_gate: bool


class ExecutePaymentOutput(BaseModel):
    payment_id: str
    status: str
    executed_at: datetime


class NotifyUserInput(BaseModel):
    payment_id: str
    amount: Decimal
    currency: str
    payee_id: str


class NotifyUserOutput(BaseModel):
    message: str
