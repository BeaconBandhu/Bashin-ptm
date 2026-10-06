"""Per-model $/token pricing, used for both the spend guardrail's pre-call
estimate and the post-call actual-cost calculation from real token usage.
Confirmed via web search against current (Sept 2026) provider pricing pages
at the time this was written, not assumed from training data — real money
is on the line. Re-verify if a model here is renamed or repriced.
"""

from __future__ import annotations

# model -> (input $ / 1M tokens, output $ / 1M tokens)
MODEL_PRICING_USD_PER_1M_TOKENS: dict[str, tuple[float, float]] = {
    "gpt-5-nano": (0.05, 0.40),
    "gpt-4o-mini": (0.15, 0.60),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    # GroqCloud self-serve catalog, confirmed Sept 2026 (Llama 3.x now
    # requires an enterprise conversation, not available self-serve).
    "openai/gpt-oss-20b": (0.075, 0.30),
    "openai/gpt-oss-120b": (0.15, 0.60),
}

# A conservative overestimate used by the spend guardrail BEFORE a call is
# made (we don't know exact token counts yet) — deliberately generous so
# the guardrail never lets a call through that could blow the cap.
ESTIMATED_INPUT_TOKENS = 1200
ESTIMATED_OUTPUT_TOKENS = 500


class UnknownModelPricingError(ValueError):
    pass


def compute_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Raises rather than silently returning 0 for an unrecognized model —
    a spend tracker that can go silently wrong is worse than one that
    fails loudly, given the whole point is protecting a small real budget."""
    if model not in MODEL_PRICING_USD_PER_1M_TOKENS:
        raise UnknownModelPricingError(
            f"no pricing entry for model '{model}' — add it to "
            "MODEL_PRICING_USD_PER_1M_TOKENS before using it"
        )
    input_price, output_price = MODEL_PRICING_USD_PER_1M_TOKENS[model]
    return (input_tokens / 1_000_000) * input_price + (output_tokens / 1_000_000) * output_price


def estimate_cost_usd(model: str) -> float:
    return compute_cost_usd(model, ESTIMATED_INPUT_TOKENS, ESTIMATED_OUTPUT_TOKENS)
