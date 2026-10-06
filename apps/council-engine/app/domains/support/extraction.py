"""Tiny, deterministic fact-extraction helpers — pulling structured signals
(an amount) out of the customer's free-text complaint so the fraud scorer
and dispute-filer have something concrete to work with, instead of the
Council having to infer it from prose. Pure regex, no LLM call, $0.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_AMOUNT_PATTERN = re.compile(
    r"₹\s*([\d,]+(?:\.\d+)?)|([\d,]+(?:\.\d+)?)\s*(?:rupees?|rs\.?|inr)\b",
    re.IGNORECASE,
)


def extract_amount(text: str) -> Decimal | None:
    """Returns the largest currency-tagged number found (₹-prefixed, or
    followed by 'rupees'/'rs'/'inr') — a bare number with no currency
    context (a date, a ticket number) is deliberately NOT treated as an
    amount. Returns None if nothing matches, which is itself meaningful:
    the customer didn't give us a checkable number."""
    candidates: list[Decimal] = []
    for group1, group2 in _AMOUNT_PATTERN.findall(text):
        raw = group1 or group2
        if not raw:
            continue
        try:
            candidates.append(Decimal(raw.replace(",", "")))
        except InvalidOperation:
            continue
    return max(candidates) if candidates else None
