"""Fully simulated human support-agent roster: no real agent pool exists
behind this. Assignment is specialty-aware ("assigned tasks accordingly")
— each agent has topic tags matched against the ticket's query/diagnosis
text, falling back to round-robin among available agents when nothing
matches. Implements a minimal availability-queue interface so
EscalateToHumanNode has somewhere concrete to hand a ticket off to.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SupportAgent:
    agent_id: str
    name: str
    available: bool
    specialties: tuple[str, ...] = ()


class MockHumanAgentQueue:
    def __init__(self, agents: list[SupportAgent] | None = None) -> None:
        self._agents = agents or [
            SupportAgent(
                "agent-aranya",
                "Aranya Bandhu",
                available=True,
                specialties=(
                    "fraud", "unauthorized", "dispute", "security", "chargeback",
                    "ombudsman", "hacked", "scam", "suspicious",
                ),
            ),
            SupportAgent(
                "agent-vanshika",
                "Vanshika Yadav",
                available=True,
                specialties=(
                    "kyc", "account", "payment", "refund", "transaction", "wallet",
                    "upi", "beneficiary", "bank", "card",
                ),
            ),
        ]
        self._round_robin_index = 0

    async def assign_next_available(self, category_hint: str = "") -> SupportAgent | None:
        available = [a for a in self._agents if a.available]
        if not available:
            return None

        hint = category_hint.lower()
        for agent in available:
            if any(specialty in hint for specialty in agent.specialties):
                return agent

        agent = available[self._round_robin_index % len(available)]
        self._round_robin_index += 1
        return agent
