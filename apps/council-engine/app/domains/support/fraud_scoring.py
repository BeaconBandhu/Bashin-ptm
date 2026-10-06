"""Deterministic fraud scoring — the real judgment behind "is this
suspicious," which the Council reasons ON TOP of rather than guessing from
free text (see the architecture plan's Council-Ecosystem-v2 increment for
the full rationale). No training data exists to fit a real classifier yet,
so this computes the exact feature set a real fraud model would use
(velocity, novelty, deviation-from-baseline, report-recency) and combines
them with a fixed weighted rule — same "simulated now, same interface
later" pattern as every mock client in this codebase. A trained model
drops in behind `score_fraud` without any caller changing.

Every signal here comes from data we actually have (this customer's own
ticket history) — nothing is invented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

VELOCITY_WINDOW = timedelta(hours=24)
VELOCITY_THRESHOLD = 3  # >=N tickets/payments in the window starts looking like velocity fraud
GOLDEN_HOUR = timedelta(hours=1)
STANDARD_REPORT_WINDOW = timedelta(days=2)

FREEZE_THRESHOLD = 0.35  # deliberately low — see the plan's asymmetric-risk rationale


@dataclass(frozen=True, slots=True)
class FraudSignals:
    velocity_count: int
    is_new_payee: bool
    amount_deviation_ratio: float | None  # amount / this customer's typical amount, None if no baseline
    report_recency: timedelta | None  # time since the referenced transaction, None if unknown
    is_within_golden_hour: bool


@dataclass(frozen=True, slots=True)
class FraudScore:
    score: float
    reasons: list[str] = field(default_factory=list)
    signals: FraudSignals | None = None

    @property
    def should_freeze(self) -> bool:
        return self.score >= FREEZE_THRESHOLD


def score_fraud(
    *,
    customer_id: str,
    amount: Decimal | None,
    payee_id: str | None,
    transaction_time: datetime | None,
    report_time: datetime,
    recent_payment_tickets: list[dict[str, Any]],
    known_payee_ids: set[str],
) -> FraudScore:
    """`recent_payment_tickets` / `known_payee_ids` are pre-fetched by the
    calling node (see LookupTransactionContextNode) — this function itself
    does no I/O, staying a pure, independently-testable scorer."""
    reasons: list[str] = []
    weighted_score = 0.0

    # Velocity: count this customer's own tickets/payments within the window.
    recent_count = 0
    if report_time is not None:
        window_start = report_time - VELOCITY_WINDOW
        for t in recent_payment_tickets:
            created_at = t.get("created_at")
            if isinstance(created_at, str):
                try:
                    created_at = datetime.fromisoformat(created_at)
                except ValueError:
                    continue
            if isinstance(created_at, datetime) and created_at >= window_start:
                recent_count += 1
    if recent_count >= VELOCITY_THRESHOLD:
        weighted_score += 0.35
        reasons.append(f"{recent_count} transactions/tickets from this customer in the last 24h (velocity)")

    # Novelty: unrecognized payee.
    is_new_payee = bool(payee_id) and payee_id not in known_payee_ids
    if is_new_payee and amount is not None and amount > Decimal(5000):
        # A brand-new payee plus a high amount is, by itself, one of the
        # strongest real-world UPI fraud signals — deliberately weighted to
        # clear FREEZE_THRESHOLD on its own, not just contribute to it.
        weighted_score += 0.40
        reasons.append(f"new/unrecognized payee '{payee_id}' combined with a high amount")
    elif is_new_payee:
        weighted_score += 0.10
        reasons.append(f"new/unrecognized payee '{payee_id}'")

    # Deviation from this customer's own typical amount.
    amount_deviation_ratio: float | None = None
    if amount is not None and recent_payment_tickets:
        amounts = [
            Decimal(str(t["query"]["amount"]))
            for t in recent_payment_tickets
            if isinstance(t.get("query"), dict) and "amount" in t["query"]
        ]
        if amounts:
            typical = sum(amounts) / len(amounts)
            if typical > 0:
                amount_deviation_ratio = float(amount / typical)
                if amount_deviation_ratio >= 10:
                    weighted_score += 0.25
                    reasons.append(
                        f"amount is {amount_deviation_ratio:.1f}x this customer's typical transaction"
                    )

    # Report recency — informs urgency/golden-hour, and a same-minute report
    # of a large loss is itself a mild signal worth surfacing.
    report_recency: timedelta | None = None
    is_within_golden_hour = False
    if transaction_time is not None:
        report_recency = report_time - transaction_time
        is_within_golden_hour = timedelta(0) <= report_recency <= GOLDEN_HOUR

    score = min(weighted_score, 1.0)
    signals = FraudSignals(
        velocity_count=recent_count,
        is_new_payee=is_new_payee,
        amount_deviation_ratio=amount_deviation_ratio,
        report_recency=report_recency,
        is_within_golden_hour=is_within_golden_hour,
    )
    return FraudScore(score=score, reasons=reasons, signals=signals)


def utcnow() -> datetime:
    return datetime.now(UTC)
