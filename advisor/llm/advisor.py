"""Advisor tool-calling loop with strict engine re-validation of every proposal.

Core principle (AGENTS.md §1 & §12):
The engine does all the maths. Any fit proposed by the LLM that fails parsing or
validation is discarded and logged. Stats attached to every recommendation come
exclusively from ``engine.calculate``.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from pydantic import BaseModel, Field

from advisor import eft, engine, pricing, profiles, validate, variants
from advisor.llm.base import LLMMessage, LLMProvider, MockLLMProvider, ToolDefinition
from advisor.llm.gemini import GeminiProvider
from advisor.llm.openai_compat import OpenAICompatProvider
from advisor.models import FitSpec, FitStats, SkillSet
from advisor.pricing import PriceProvider
from advisor.profiles import ProfileConfig

logger = logging.getLogger(__name__)

DEFAULT_MAX_TOOL_CALLS = 8

_SYSTEM_PROMPT = """You are an EVE Online ship fitting advisor.
You MUST NEVER calculate, estimate, or invent ship statistics or legality yourself.
All statistics and legality checks come exclusively from the fitting engine tools and the
pre-computed candidates provided to you.

You may call the provided tools (`evaluate_fit`, `list_variants`, `get_profile`) to explore
additional swaps if helpful.

When you are ready to answer, respond ONLY with valid JSON matching this exact schema:
{
  "summary": "Brief explanation of the original fit and the key trade-offs for this profile.",
  "recommendations": [
    {
      "eft": "[Ship, Name]\\n...",
      "reasoning": "Why this fit is recommended and what trade-offs it makes."
    }
  ]
}
Every EFT in `recommendations` must be a complete, legal EFT string.
"""


class RecommendedFit(BaseModel):
    """A single engine-verified recommendation returned to the caller."""

    eft_text: str
    reasoning: str
    stats: FitStats
    total_cost_isk: float | None = None
    unknown_price_items: list[str] = Field(default_factory=list)


class DiscardedProposal(BaseModel):
    """Record of an LLM-proposed fit that was rejected by parsing or validation."""

    eft_text: str
    reasons: list[str]


class AdvisorResponse(BaseModel):
    """Structured output of the advisor pipeline."""

    profile: str
    summary: str
    baseline_eft: str
    baseline_stats: FitStats
    baseline_cost_isk: float | None = None
    recommendations: list[RecommendedFit] = Field(default_factory=list)
    discarded_proposals: list[DiscardedProposal] = Field(default_factory=list)
    tool_calls_made: int = 0


def get_provider_from_env() -> LLMProvider:
    """Instantiate the configured LLM provider from environment variables."""
    provider_name = (os.environ.get("LLM_PROVIDER") or "mock").strip().lower()
    if provider_name == "mock":
        return MockLLMProvider(include_invalid_proposals=False)
    if provider_name == "gemini":
        return GeminiProvider()
    if provider_name in ("openai_compat", "openai", "lmstudio"):
        return OpenAICompatProvider()
    raise ValueError(
        f"Unknown LLM_PROVIDER {provider_name!r}. Supported: mock, gemini, openai_compat."
    )


def _tool_definitions() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="evaluate_fit",
            description="Parse, validate, and calculate stats for an EFT fit string using the eos engine.",
            parameters={
                "type": "object",
                "properties": {
                    "eft": {"type": "string", "description": "Complete EFT fit string."},
                },
                "required": ["eft"],
            },
        ),
        ToolDefinition(
            name="list_variants",
            description="List legal, published variant modules for a module name under a profile, with Jita prices and meta groups.",
            parameters={
                "type": "object",
                "properties": {
                    "module_name": {"type": "string", "description": "Exact EVE module name."},
                    "profile": {"type": "string", "description": "Profile key (e.g. pve, bling, cheap_fw, budget_pvp)."},
                },
                "required": ["module_name", "profile"],
            },
        ),
        ToolDefinition(
            name="get_profile",
            description="Return constraints and scoring weights for an optimization profile.",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Profile key."},
                },
                "required": ["name"],
            },
        ),
    ]


def _extract_json_object(text: str) -> dict[str, Any]:
    """Extract a JSON object from LLM output, tolerating markdown code fences."""
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return obj
    except ValueError:
        pass
    # Fallback: find outermost braces
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(cleaned[start : end + 1])
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
    return {"summary": cleaned or "No summary returned.", "recommendations": []}


def advise_fit(
    fit_spec: FitSpec,
    profile: ProfileConfig,
    price_provider: PriceProvider,
    provider: LLMProvider,
    skills: SkillSet | None = None,
    max_evaluations: int = 40,
    top_n_candidates: int = 5,
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
) -> AdvisorResponse:
    """Run candidate generation, LLM tool-calling loop, and strict engine re-validation."""
    # Step 1: Compute baseline & top candidates BEFORE calling the LLM
    baseline, cands = variants.generate_candidates(
        fit_spec=fit_spec,
        profile=profile,
        price_provider=price_provider,
        skills=skills,
        max_evaluations=max_evaluations,
        top_n=top_n_candidates,
    )

    # Compact structured context for the LLM
    context_payload = {
        "profile": profile.model_dump(),
        "baseline": {
            "eft_text": baseline.eft_text,
            "stats": baseline.stats.model_dump(exclude={"modules"}),
            "total_cost_isk": baseline.total_cost_isk,
            "unknown_price_items": baseline.unknown_price_items,
        },
        "candidates": [
            {
                "eft_text": c.eft_text,
                "changes": c.changes,
                "score": round(c.score, 4),
                "total_cost_isk": c.total_cost_isk,
                "stats": c.stats.model_dump(exclude={"modules"}),
            }
            for c in cands
        ],
    }

    messages: list[LLMMessage] = [
        LLMMessage(role="system", content=_SYSTEM_PROMPT),
        LLMMessage(role="user", content=json.dumps(context_payload)),
    ]
    tools = _tool_definitions()

    def _run_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "evaluate_fit":
            raw_eft = str(args.get("eft") or "")
            try:
                parsed = eft.parse_eft(raw_eft)
            except eft.EFTParseError as exc:
                return {"valid": False, "errors": [str(exc)]}
            val = validate.validate_fit(parsed, skills)
            if not val.valid:
                return {"valid": False, "errors": [i.message for i in val.issues]}
            stats = engine.calculate(parsed, skills)
            cost, _, unknown = pricing.estimate_fit_cost(parsed, price_provider)
            return {
                "valid": True,
                "stats": stats.model_dump(exclude={"modules"}),
                "total_cost_isk": cost,
                "unknown_price_items": unknown,
            }

        if name == "list_variants":
            mod_name = str(args.get("module_name") or "")
            prof_name = str(args.get("profile") or profile.key)
            try:
                prof = profiles.get_profile(prof_name)
            except KeyError as exc:
                return {"error": str(exc)}
            opts = variants.list_legal_variants(mod_name, prof, price_provider)
            return {"variants": [o.model_dump() for o in opts[:25]]}

        if name == "get_profile":
            prof_name = str(args.get("name") or profile.key)
            try:
                prof = profiles.get_profile(prof_name)
            except KeyError as exc:
                return {"error": str(exc)}
            return prof.model_dump()

        return {"error": f"Unknown tool: {name}"}

    # Step 2: Bounded tool-calling loop
    tool_calls_made = 0
    final_text = ""

    while True:
        remaining = max_tool_calls - tool_calls_made
        active_tools = tools if remaining > 0 else []
        resp = provider.chat(messages, tools=active_tools, json_mode=(not active_tools))

        if resp.tool_calls and remaining > 0:
            messages.append(
                LLMMessage(role="assistant", content=resp.content, tool_calls=resp.tool_calls)
            )
            for tc in resp.tool_calls:
                if tool_calls_made >= max_tool_calls:
                    break
                tool_calls_made += 1
                result = _run_tool(tc.name, tc.arguments)
                messages.append(
                    LLMMessage(
                        role="tool",
                        tool_call_id=tc.id,
                        tool_name=tc.name,
                        content=json.dumps(result),
                    )
                )
            continue

        final_text = resp.content
        break

    # Step 3 & 4: Parse final JSON and strictly re-validate every proposed fit
    parsed_json = _extract_json_object(final_text)
    summary = str(parsed_json.get("summary") or "")
    raw_recs = parsed_json.get("recommendations") or []

    verified_recs: list[RecommendedFit] = []
    discarded: list[DiscardedProposal] = []
    seen_efts: set[str] = set()

    for rec in raw_recs:
        if not isinstance(rec, dict):
            continue
        raw_eft = str(rec.get("eft") or "").strip()
        reasoning = str(rec.get("reasoning") or "").strip()
        if not raw_eft:
            continue

        try:
            spec = eft.parse_eft(raw_eft)
        except eft.EFTParseError as exc:
            logger.warning("Discarding unparseable LLM fit proposal: %s", exc)
            discarded.append(DiscardedProposal(eft_text=raw_eft, reasons=[str(exc)]))
            continue

        val = validate.validate_fit(spec, skills)
        if not val.valid:
            reasons = [i.message for i in val.issues]
            logger.warning("Discarding illegal LLM fit proposal (%s): %s", spec.name, reasons)
            discarded.append(DiscardedProposal(eft_text=raw_eft, reasons=reasons))
            continue

        canonical_eft = eft.export_eft(spec)
        if canonical_eft in seen_efts:
            continue
        seen_efts.add(canonical_eft)

        # Compute stats & cost EXCLUSIVELY via our engine and price provider
        computed_stats = engine.calculate(spec, skills)
        cost, _, unknown = pricing.estimate_fit_cost(spec, price_provider)

        # Enforce budget cap on LLM proposals too
        if profile.budget_cap_isk is not None:
            if cost is None:
                reason = f"Unknown item price(s) ({', '.join(unknown)}) not allowed under budget-capped profile '{profile.key}'"
                logger.warning("Discarding LLM proposal: %s", reason)
                discarded.append(DiscardedProposal(eft_text=raw_eft, reasons=[reason]))
                continue
            if cost > profile.budget_cap_isk:
                reason = f"Fit cost {cost:,.0f} ISK exceeds profile budget cap {profile.budget_cap_isk:,.0f} ISK"
                logger.warning("Discarding LLM proposal: %s", reason)
                discarded.append(DiscardedProposal(eft_text=raw_eft, reasons=[reason]))
                continue

        verified_recs.append(
            RecommendedFit(
                eft_text=canonical_eft,
                reasoning=reasoning,
                stats=computed_stats,
                total_cost_isk=cost,
                unknown_price_items=unknown,
            )
        )

    return AdvisorResponse(
        profile=profile.key,
        summary=summary,
        baseline_eft=baseline.eft_text,
        baseline_stats=baseline.stats,
        baseline_cost_isk=baseline.total_cost_isk,
        recommendations=verified_recs,
        discarded_proposals=discarded,
        tool_calls_made=tool_calls_made,
    )
