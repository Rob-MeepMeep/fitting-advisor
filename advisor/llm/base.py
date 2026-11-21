"""Provider interface and offline mock provider for the LLM advisor layer."""

from __future__ import annotations

import abc
import json
from typing import Any

from pydantic import BaseModel, Field


class ToolDefinition(BaseModel):
    """JSON-schema definition of a tool exposed to the LLM."""

    name: str
    description: str
    parameters: dict[str, Any]


class ToolCall(BaseModel):
    """A tool invocation requested by the LLM."""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class LLMMessage(BaseModel):
    """A chat message in the advisor conversation."""

    role: str  # "system" | "user" | "assistant" | "tool"
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    tool_name: str | None = None


class LLMResponse(BaseModel):
    """Response from a single LLM turn."""

    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)


class LLMProvider(abc.ABC):
    """Abstract interface for LLM chat providers with tool calling."""

    @abc.abstractmethod
    def chat(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition],
        json_mode: bool = False,
    ) -> LLMResponse:
        """Send messages and tool schemas to the provider and return one turn."""


class MockLLMProvider(LLMProvider):
    """Deterministic offline provider for tests and local demos without API keys.

    Can optionally emit deliberately invalid fit proposals (unknown module names,
    over-CPU fits) to verify that the advisor loop discards them.
    """

    def __init__(
        self,
        include_invalid_proposals: bool = True,
        infinite_tool_loop: bool = False,
    ):
        self.include_invalid_proposals = include_invalid_proposals
        self.infinite_tool_loop = infinite_tool_loop
        self._turn = 0

    def chat(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition],
        json_mode: bool = False,
    ) -> LLMResponse:
        self._turn += 1

        if self.infinite_tool_loop:
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        id=f"mock-loop-{self._turn}",
                        name="get_profile",
                        arguments={"name": "pve"},
                    )
                ]
            )

        # Extract context JSON from the first user message
        context: dict[str, Any] = {}
        for msg in messages:
            if msg.role == "user":
                try:
                    context = json.loads(msg.content)
                except ValueError:
                    pass
                break

        profile_key = context.get("profile", {}).get("key", "pve")
        candidates = context.get("candidates") or []

        # Turn 1: call get_profile and evaluate_fit (if we have a candidate)
        if self._turn == 1:
            calls = [
                ToolCall(
                    id="mock-tc-1",
                    name="get_profile",
                    arguments={"name": profile_key},
                )
            ]
            if candidates:
                calls.append(
                    ToolCall(
                        id="mock-tc-2",
                        name="evaluate_fit",
                        arguments={"eft": candidates[0]["eft_text"]},
                    )
                )
            return LLMResponse(tool_calls=calls)

        # Turn 2+: return structured JSON recommendations
        recs: list[dict[str, Any]] = []
        for cand in candidates[:2]:
            changes = ", ".join(cand.get("changes") or []) or "engine-generated adjustment"
            recs.append(
                {
                    "eft": cand["eft_text"],
                    "reasoning": f"Engine-validated candidate ({changes}) aligned with the {profile_key} profile.",
                    # Deliberately fake stats to prove advisor.py NEVER trusts model numbers:
                    "dps_total": 999999.0,
                }
            )

        if self.include_invalid_proposals:
            # 1. Unknown / hallucinated module name
            recs.append(
                {
                    "eft": "[Rifter, Hallucinated Module]\nHyper-Mega Ultra Laser 9000\n",
                    "reasoning": "Invalid proposal with a nonexistent module (must be discarded).",
                }
            )
            # 2. Over-CPU / over-PG / wrong-rig illegal fit
            recs.append(
                {
                    "eft": (
                        "[Rifter, Over-CPU Illegal]\n"
                        "Damage Control II\n"
                        "Damage Control II\n"
                        "1600mm Steel Plates II\n"
                        "500MN Y-T8 Compact Microwarpdrive\n"
                    ),
                    "reasoning": "Invalid over-PG / duplicate Damage Control proposal (must be discarded).",
                }
            )

        payload = {
            "summary": (
                f"Evaluated the fit under the '{profile_key}' profile using engine-calculated stats. "
                "See the verified recommendations below for trade-offs."
            ),
            "recommendations": recs,
        }
        return LLMResponse(content=json.dumps(payload))
