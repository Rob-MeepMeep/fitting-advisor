"""Phase 5 tests: LLM advisor loop, invalid proposal rejection, engine-only stats, tool cap."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from advisor import eft, pricing, profiles, validate
from advisor.llm import advisor
from advisor.llm.base import MockLLMProvider
from advisor.llm.gemini import GeminiProvider
from advisor.llm.openai_compat import OpenAICompatProvider
from advisor.models import SkillSet

GOLDEN_DIR = Path(__file__).parent / "golden"


def test_advisor_discards_invalid_proposals_and_uses_engine_stats():
    """Mock provider returns 2 valid fits (with fake dps=999999) + 2 invalid fits.
    Advisor must discard both invalid fits and attach real engine-computed stats.
    """
    fit_spec = eft.parse_eft((GOLDEN_DIR / "rifter_turrets.eft").read_text(encoding="utf-8"))
    bling = profiles.get_profile("bling")
    price_provider = pricing.StaticPriceProvider({})
    mock = MockLLMProvider(include_invalid_proposals=True)

    result = advisor.advise_fit(
        fit_spec=fit_spec,
        profile=bling,
        price_provider=price_provider,
        provider=mock,
        skills=SkillSet(preset="all5"),
        max_evaluations=15,
        top_n_candidates=3,
        max_tool_calls=8,
    )

    assert result.summary
    assert result.tool_calls_made == 2
    # Both invalid proposals (unknown module + over-PG/duplicate DC) must be discarded
    assert len(result.discarded_proposals) == 2
    assert len(result.recommendations) >= 1

    for rec in result.recommendations:
        # Must NOT contain the model's fake 999999 DPS
        assert rec.stats.dps_total < 5000.0
        # Every surviving recommendation must pass validation
        parsed = eft.parse_eft(rec.eft_text)
        assert validate.validate_fit(parsed, SkillSet(preset="all5")).valid


def test_advisor_caps_tool_calls():
    fit_spec = eft.parse_eft((GOLDEN_DIR / "rifter_turrets.eft").read_text(encoding="utf-8"))
    pve = profiles.get_profile("pve")
    price_provider = pricing.StaticPriceProvider({})
    loop_mock = MockLLMProvider(infinite_tool_loop=True)

    result = advisor.advise_fit(
        fit_spec=fit_spec,
        profile=pve,
        price_provider=price_provider,
        provider=loop_mock,
        skills=SkillSet(preset="all5"),
        max_evaluations=10,
        max_tool_calls=3,
    )

    assert result.tool_calls_made == 3


def test_gemini_provider_tool_call_and_json_response():
    """Verify GeminiProvider serializes tool calls/responses and parses JSON output offline."""
    fit_spec = eft.parse_eft((GOLDEN_DIR / "rifter_turrets.eft").read_text(encoding="utf-8"))
    canonical_eft = eft.export_eft(fit_spec)
    requests_seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("x-goog-api-key") == "test-gemini-key"
        body = json.loads(request.content.decode("utf-8"))
        requests_seen.append(body)
        if len(requests_seen) == 1:
            assert "tools" in body
            assert "systemInstruction" in body
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {
                                "role": "model",
                                "parts": [
                                    {"functionCall": {"name": "get_profile", "args": {"name": "bling"}}}
                                ],
                            }
                        }
                    ]
                },
            )
        # Second turn: verify functionResponse was sent back
        last_parts = body["contents"][-1]["parts"]
        assert last_parts[0]["functionResponse"]["name"] == "get_profile"
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {
                                            "summary": "Gemini verified summary.",
                                            "recommendations": [
                                                {
                                                    "eft": canonical_eft,
                                                    "reasoning": "Verified via Gemini mock transport.",
                                                }
                                            ],
                                        }
                                    )
                                }
                            ],
                        }
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GeminiProvider(api_key="test-gemini-key", model="gemini-2.5-flash", http_client=client)
    result = advisor.advise_fit(
        fit_spec=fit_spec,
        profile=profiles.get_profile("bling"),
        price_provider=pricing.StaticPriceProvider({}),
        provider=provider,
        skills=SkillSet(preset="all5"),
        max_evaluations=5,
        top_n_candidates=2,
    )

    assert len(requests_seen) == 2
    assert result.tool_calls_made == 1
    assert result.summary == "Gemini verified summary."
    assert len(result.recommendations) == 1
    assert result.recommendations[0].stats.ship == "Rifter"


def test_openai_compat_provider_tool_call_and_json_response():
    """Verify OpenAICompatProvider serializes tool calls/responses and parses JSON output offline."""
    fit_spec = eft.parse_eft((GOLDEN_DIR / "rifter_turrets.eft").read_text(encoding="utf-8"))
    canonical_eft = eft.export_eft(fit_spec)
    requests_seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://127.0.0.1:1234/v1/chat/completions"
        body = json.loads(request.content.decode("utf-8"))
        requests_seen.append(body)
        if len(requests_seen) == 1:
            assert "tools" in body
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "evaluate_fit",
                                            "arguments": json.dumps({"eft": canonical_eft}),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        # Second turn: verify tool message was sent back
        tool_msgs = [m for m in body["messages"] if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "call_1"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "summary": "OpenAI-compat verified summary.",
                                    "recommendations": [
                                        {
                                            "eft": canonical_eft,
                                            "reasoning": "Verified via OpenAI-compat mock transport.",
                                        }
                                    ],
                                }
                            ),
                        }
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAICompatProvider(
        base_url="http://127.0.0.1:1234/v1",
        model="local-qwen",
        api_key="lm-studio",
        http_client=client,
    )
    result = advisor.advise_fit(
        fit_spec=fit_spec,
        profile=profiles.get_profile("bling"),
        price_provider=pricing.StaticPriceProvider({}),
        provider=provider,
        skills=SkillSet(preset="all5"),
        max_evaluations=5,
        top_n_candidates=2,
    )

    assert len(requests_seen) == 2
    assert result.tool_calls_made == 1
    assert result.summary == "OpenAI-compat verified summary."
    assert len(result.recommendations) == 1
    assert result.recommendations[0].stats.ship == "Rifter"


def test_gemini_provider_batches_parallel_tool_responses_and_preserves_thought_signature():
    """Parallel Gemini functionCalls must produce a single user turn with all functionResponses
    and preserve thoughtSignature on the model turn."""
    fit_spec = eft.parse_eft((GOLDEN_DIR / "rifter_turrets.eft").read_text(encoding="utf-8"))
    canonical_eft = eft.export_eft(fit_spec)
    requests_seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        requests_seen.append(body)
        if len(requests_seen) == 1:
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {
                                "role": "model",
                                "parts": [
                                    {
                                        "functionCall": {"name": "get_profile", "args": {"name": "bling"}},
                                        "thoughtSignature": "sig-alpha",
                                    },
                                    {
                                        "functionCall": {
                                            "name": "list_variants",
                                            "args": {"module_name": "Gyrostabilizer II", "profile": "bling"},
                                        },
                                        "thoughtSignature": "sig-beta",
                                    },
                                ],
                            }
                        }
                    ]
                },
            )

        # Contents must be: user (context) -> model (2 functionCalls w/ sigs) -> single user (2 functionResponses)
        assert len(body["contents"]) == 3
        model_parts = body["contents"][1]["parts"]
        assert model_parts[0]["thoughtSignature"] == "sig-alpha"
        assert model_parts[1]["thoughtSignature"] == "sig-beta"
        fr_turn = body["contents"][2]
        assert fr_turn["role"] == "user"
        assert len(fr_turn["parts"]) == 2
        assert [p["functionResponse"]["name"] for p in fr_turn["parts"]] == ["get_profile", "list_variants"]

        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {
                                            "summary": "Parallel tool calls batched cleanly.",
                                            "recommendations": [
                                                {
                                                    "eft": canonical_eft,
                                                    "reasoning": "Verified.",
                                                }
                                            ],
                                        }
                                    )
                                }
                            ],
                        }
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GeminiProvider(api_key="test-key", model="gemini-2.5-flash", http_client=client)
    result = advisor.advise_fit(
        fit_spec=fit_spec,
        profile=profiles.get_profile("bling"),
        price_provider=pricing.StaticPriceProvider({}),
        provider=provider,
        skills=SkillSet(preset="all5"),
        max_evaluations=5,
        top_n_candidates=2,
    )

    assert result.tool_calls_made == 2
    assert result.summary == "Parallel tool calls batched cleanly."
