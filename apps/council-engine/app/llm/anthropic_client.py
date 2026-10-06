from __future__ import annotations

from anthropic import AsyncAnthropic

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

_TRIAGE_TOOL_NAME = "record_triage_decision"
_VERIFIER_TOOL_NAME = "record_verifier_decision"
_VALID_ACTIONS = ("freeze_card", "file_dispute", "none")


def _safe_action(raw: object) -> ProtectiveAction:
    return raw if raw in _VALID_ACTIONS else "none"  # type: ignore[return-value]


# Forcing a tool call (rather than asking Claude to emit JSON in plain text)
# is the reliable way to get structured output from the Messages API — no
# free-text parsing, no risk of the model wrapping JSON in prose.
_TRIAGE_TOOL_SCHEMA = {
    "name": _TRIAGE_TOOL_NAME,
    "description": "Record the Triage member's decision on this support ticket.",
    "input_schema": {
        "type": "object",
        "properties": {
            "resolvable": {"type": "boolean"},
            "diagnosis": {"type": "string"},
            "resolution_steps": {"type": ["string", "null"]},
            "escalate_reason": {"type": ["string", "null"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "risk_class": {"type": "string", "enum": ["routine", "high_stakes"]},
            "requested_action": {"type": "string", "enum": ["freeze_card", "file_dispute", "none"]},
        },
        "required": ["resolvable", "diagnosis", "confidence", "risk_class", "requested_action"],
    },
}

_VERIFIER_TOOL_SCHEMA = {
    "name": _VERIFIER_TOOL_NAME,
    "description": "Record the Verifier member's decision on the Triage member's proposal.",
    "input_schema": {
        "type": "object",
        "properties": {
            "approved": {"type": "boolean"},
            "final_resolution_steps": {"type": ["string", "null"]},
            "veto_reason": {"type": ["string", "null"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "requested_action": {"type": "string", "enum": ["freeze_card", "file_dispute", "none"]},
        },
        "required": ["approved", "confidence", "requested_action"],
    },
}


class AnthropicClient:
    provider = "anthropic"

    def __init__(self, api_key: str, model: str) -> None:
        self.model = model
        self._client = AsyncAnthropic(api_key=api_key)

    async def _call(
        self, system_prompt: str, user_prompt: str, tool_schema: dict, tool_name: str
    ) -> tuple[dict, int, int, float]:
        response = await self._client.messages.create(
            model=self.model,
            max_tokens=700,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            tools=[tool_schema],
            tool_choice={"type": "tool", "name": tool_name},
        )
        tool_use = next(block for block in response.content if block.type == "tool_use")
        parsed = tool_use.input
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
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
            _TRIAGE_TOOL_SCHEMA,
            _TRIAGE_TOOL_NAME,
        )
        decision = TriageDecision(
            resolvable=bool(parsed.get("resolvable", False)),
            diagnosis=str(parsed.get("diagnosis", "")),
            resolution_steps=parsed.get("resolution_steps"),
            escalate_reason=parsed.get("escalate_reason"),
            confidence=float(parsed.get("confidence", 0.0)),
            risk_class=parsed.get("risk_class", "high_stakes"),
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
            _VERIFIER_TOOL_SCHEMA,
            _VERIFIER_TOOL_NAME,
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
