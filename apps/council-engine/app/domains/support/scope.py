"""The harness/scope guardrail: a deterministic, $0 check that runs BEFORE
retrieval or the Council, catching requests that aren't a Paytm support
question at all — general-purpose tasks ("write me a python script"),
trivia, or "how do I earn/make money" schemes. These get declined
immediately rather than burning a RAG lookup, a paid Council call, or a
human agent's time on something support cannot help with anyway (see
policy-out-of-scope-requests / policy-earning-schemes in app/rag/corpus.py,
which the Council itself is told to apply if a borderline case ever reaches
it — this node is the fast, free first line, not the only line).

Deliberately simple pattern + keyword matching, not an LLM call — the whole
point is to filter BEFORE spending anything. It will misclassify some edge
cases (see its own limitations noted below); when in doubt it lets the
request through to RAG/Council rather than wrongly refusing a real
complaint, since a wasted retrieval is cheap and a wrongly-refused customer
is not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_OFF_TOPIC_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bwrite\s+(me\s+)?(a\s+)?(python|javascript|typescript|java|c\+\+|sql|html|css|code|script|program|function|algorithm)\b",
        r"\bhow\s+(do|can)\s+i\s+(code|program)\b",
        r"\b(write|compose)\s+(me\s+)?(an?\s+)?(essay|poem|story|song|joke)\b",
        r"\btranslate\s+this\b",
        r"\bwhat\s+is\s+the\s+capital\s+of\b",
        r"\bsolve\s+this\s+(math|equation)\b",
        r"\brecipe\s+for\b",
        r"\bwho\s+(won|is\s+the\s+president|is\s+the\s+prime\s+minister)\b",
    )
)

_EARNING_SCHEME_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bhow\s+(can|do)\s+i\s+earn\b",
        r"\bmake\s+money\s+(fast|quick|online|overnight)\b",
        r"\bearn\s+(rs\.?|inr|₹)?\s*\d[\d,]*\b",
        r"\bget\s+rich\b",
        r"\bguaranteed\s+(income|profit|returns)\b",
    )
)

_DOMAIN_KEYWORDS = frozenset(
    {
        "upi", "payment", "pay", "paytm", "wallet", "kyc", "transaction", "refund",
        "account", "card", "bank", "beneficiary", "neft", "imps", "recharge", "bill",
        "cashback", "reward", "voucher", "fastag", "postpaid", "mandate", "autopay",
        "dispute", "chargeback", "money", "debit", "credit", "balance", "statement",
        "ticket", "booking", "otp", "pin", "fraud", "unauthorized", "transfer",
        "merchant", "qr", "loan", "investment", "mutual", "stock", "insurance", "app", "login", "password", "number", "email", "profile", "limit",
    }
)


@dataclass(frozen=True, slots=True)
class ScopeCheckResult:
    in_scope: bool
    reason: str | None


def check_scope(query: str) -> ScopeCheckResult:
    text = query.lower()

    for pattern in _OFF_TOPIC_PATTERNS:
        if pattern.search(text):
            return ScopeCheckResult(
                False,
                "This looks like a general-purpose task (writing code, trivia, etc.), not a "
                "Paytm account or payment question.",
            )

    for pattern in _EARNING_SCHEME_PATTERNS:
        if pattern.search(text):
            return ScopeCheckResult(
                False,
                "This looks like a request about earning or making money through a scheme, "
                "not a specific account or transaction issue support can act on.",
            )

    has_domain_word = any(
        re.search(rf"\b{re.escape(word)}\b", text) for word in _DOMAIN_KEYWORDS
    )
    if not has_domain_word:
        return ScopeCheckResult(
            False, "This doesn't mention any Paytm account, payment, or transaction topic."
        )

    return ScopeCheckResult(True, None)
