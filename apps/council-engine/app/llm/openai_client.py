from __future__ import annotations

import json

from openai import AsyncOpenAI

from app.llm.client import (
    TRIAGE_SYSTEM_PROMPT,
    VERIFIER_SYSTEM_PROMPT,
    ProtectiveAction,
    TriageCallResult,
    TriageDecision,
    VerifierCallResult,
    VerifierDecision,
    build_triage_user_prompt,
    build_verifier_user_prompt,
)
from app.llm.pricing import compute_cost_usd

_VALID_ACTIONS = ("freeze_card", "file_dispute", "none")


def _safe_action(raw: object) -> ProtectiveAction:
    return raw if raw in _VALID_ACTIONS else "none"  # type: ignore[return-value]


class OpenAIClient:
    provider = "openai"

    def __init__(self, api_key: str, model: str) -> None:
        self.model = model
        self._client = AsyncOpenAI(api_key=api_key)

    async def _call(self, system_prompt: str, user_prompt: str) -> tuple[dict, int, int, float]:
        response = await self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=700,
            # gpt-5-nano is a reasoning-tier model: without this, it can
            # spend its ENTIRE max_completion_tokens budget on hidden
            # reasoning tokens (confirmed via live testing — 192 reasoning
            # tokens for a trivial prompt, and a real council prompt
            # returned a blank/degenerate JSON payload at the default
            # effort), leaving nothing for the actual JSON answer. This
            # task is a bounded classification, not multi-step reasoning,
            # so minimal effort is both correct and cheaper.
            reasoning_effort="minimal",
        )
        raw = response.choices[0].message.content or "{}"
        parsed = json.loads(raw)
        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0
        cost_usd = compute_cost_usd(self.model, input_tokens, output_tokens)
        return parsed, input_tokens, output_tokens, cost_usd

    async def triage_ticket(
        self,
        *,
        ticket_query: str,
        retrieved_context: list[str],
        fraud_score: float,
        fraud_reasons: list[str],
        is_repeat_complaint: bool,
        is_within_golden_hour: bool,
    ) -> TriageCallResult:
        parsed, input_tokens, output_tokens, cost_usd = await self._call(
            TRIAGE_SYSTEM_PROMPT,
            build_triage_user_prompt(
                ticket_query,
                retrieved_context,
                fraud_score=fraud_score,
                fraud_reasons=fraud_reasons,
                is_repeat_complaint=is_repeat_complaint,
                is_within_golden_hour=is_within_golden_hour,
            ),
        )
        raw_risk_class = parsed.get("risk_class")
        # Default to the safer branch (gets a second opinion) if the model
        # returns something outside the two allowed values.
        risk_class = raw_risk_class if raw_risk_class in ("routine", "high_stakes") else "high_stakes"
        decision = TriageDecision(
            resolvable=bool(parsed.get("resolvable", False)),
            diagnosis=str(parsed.get("diagnosis", "")),
            resolution_steps=parsed.get("resolution_steps"),
            escalate_reason=parsed.get("escalate_reason"),
            confidence=float(parsed.get("confidence", 0.0)),
            risk_class=risk_class,
            requested_action=_safe_action(parsed.get("requested_action")),
        )
        return TriageCallResult(
            decision=decision,
            provider=self.provider,
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
        )

    async def verify_resolution(
        self,
        *,
        ticket_query: str,
        retrieved_context: list[str],
        fraud_score: float,
        fraud_reasons: list[str],
        triage_diagnosis: str,
        triage_resolvable: bool,
        triage_resolution_steps: str | None,
        triage_requested_action: ProtectiveAction,
    ) -> VerifierCallResult:
        parsed, input_tokens, output_tokens, cost_usd = await self._call(
            VERIFIER_SYSTEM_PROMPT,
            build_verifier_user_prompt(
                ticket_query,
                retrieved_context,
                triage_diagnosis,
                triage_resolvable,
                triage_resolution_steps,
                fraud_score=fraud_score,
                fraud_reasons=fraud_reasons,
                triage_requested_action=triage_requested_action,
            ),
        )
        decision = VerifierDecision(
            approved=bool(parsed.get("approved", False)),
            final_resolution_steps=parsed.get("final_resolution_steps"),
            veto_reason=parsed.get("veto_reason"),
            confidence=float(parsed.get("confidence", 0.0)),
            requested_action=_safe_action(parsed.get("requested_action")),
        )
        return VerifierCallResult(
            decision=decision,
            provider=self.provider,
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
        )
