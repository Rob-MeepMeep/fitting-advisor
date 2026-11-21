"""Google Gemini LLM provider (REST API via httpx)."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from advisor.llm.base import LLMMessage, LLMProvider, LLMResponse, ToolCall, ToolDefinition

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiProvider(LLMProvider):
    """Calls Gemini ``generateContent`` with function calling and JSON output."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
        http_client: httpx.Client | None = None,
    ):
        self.api_key = api_key if api_key is not None else os.environ.get("LLM_API_KEY", "")
        self.model = model or os.environ.get("LLM_MODEL") or "gemini-2.5-flash"
        self.timeout = timeout
        self._http_client = http_client

    def chat(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition],
        json_mode: bool = False,
    ) -> LLMResponse:
        if not self.api_key:
            raise ValueError("LLM_API_KEY is required for the Gemini provider")

        system_parts: list[dict[str, str]] = []
        contents: list[dict[str, Any]] = []

        for msg in messages:
            if msg.role == "system":
                system_parts.append({"text": msg.content})
            elif msg.role == "user":
                contents.append({"role": "user", "parts": [{"text": msg.content}]})
            elif msg.role == "assistant":
                parts: list[dict[str, Any]] = []
                if msg.content:
                    parts.append({"text": msg.content})
                for tc in msg.tool_calls:
                    parts.append({"functionCall": {"name": tc.name, "args": tc.arguments}})
                contents.append({"role": "model", "parts": parts})
            elif msg.role == "tool":
                try:
                    resp_obj = json.loads(msg.content)
                except ValueError:
                    resp_obj = {"result": msg.content}
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": msg.tool_name or "tool",
                                    "response": resp_obj if isinstance(resp_obj, dict) else {"result": resp_obj},
                                }
                            }
                        ],
                    }
                )

        body: dict[str, Any] = {"contents": contents}
        if system_parts:
            body["systemInstruction"] = {"parts": system_parts}
        if tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.parameters,
                        }
                        for t in tools
                    ]
                }
            ]
        if json_mode and not tools:
            body["generationConfig"] = {"responseMimeType": "application/json"}

        url = f"{GEMINI_API_BASE}/{self.model}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }

        owns_client = self._http_client is None
        client = self._http_client or httpx.Client(timeout=self.timeout)
        try:
            resp = client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
        finally:
            if owns_client:
                client.close()

        candidates = data.get("candidates") or []
        if not candidates:
            return LLMResponse(content="")

        parts = (candidates[0].get("content") or {}).get("parts") or []
        text_chunks: list[str] = []
        tool_calls: list[ToolCall] = []

        for idx, part in enumerate(parts):
            if "text" in part:
                text_chunks.append(str(part["text"]))
            if "functionCall" in part:
                fc = part["functionCall"]
                tool_calls.append(
                    ToolCall(
                        id=f"gemini-tc-{idx}",
                        name=str(fc["name"]),
                        arguments=dict(fc.get("args") or {}),
                    )
                )

        return LLMResponse(content="\n".join(text_chunks).strip(), tool_calls=tool_calls)
