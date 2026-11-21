"""OpenAI-compatible LLM provider (LM Studio, Ollama, vLLM, OpenAI, etc.)."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from advisor.llm.base import LLMMessage, LLMProvider, LLMResponse, ToolCall, ToolDefinition


class OpenAICompatProvider(LLMProvider):
    """Calls any OpenAI-compatible ``/chat/completions`` endpoint."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float = 90.0,
        http_client: httpx.Client | None = None,
    ):
        raw_base = base_url if base_url is not None else os.environ.get("LLM_BASE_URL", "http://127.0.0.1:1234/v1")
        self.base_url = raw_base.rstrip("/")
        self.model = model or os.environ.get("LLM_MODEL") or "local-model"
        self.api_key = api_key if api_key is not None else os.environ.get("LLM_API_KEY", "")
        self.timeout = timeout
        self._http_client = http_client

    def chat(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition],
        json_mode: bool = False,
    ) -> LLMResponse:
        oai_messages: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == "tool":
                oai_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": msg.tool_call_id or "call_0",
                        "content": msg.content,
                    }
                )
            elif msg.role == "assistant" and msg.tool_calls:
                oai_messages.append(
                    {
                        "role": "assistant",
                        "content": msg.content or None,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.name,
                                    "arguments": json.dumps(tc.arguments),
                                },
                            }
                            for tc in msg.tool_calls
                        ],
                    }
                )
            else:
                oai_messages.append({"role": msg.role, "content": msg.content})

        body: dict[str, Any] = {
            "model": self.model,
            "messages": oai_messages,
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
        if json_mode and not tools:
            body["response_format"] = {"type": "json_object"}

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        url = f"{self.base_url}/chat/completions"
        owns_client = self._http_client is None
        client = self._http_client or httpx.Client(timeout=self.timeout)
        try:
            resp = client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
        finally:
            if owns_client:
                client.close()

        choices = data.get("choices") or []
        if not choices:
            return LLMResponse(content="")

        message = choices[0].get("message") or {}
        content = str(message.get("content") or "")
        tool_calls: list[ToolCall] = []

        for idx, tc in enumerate(message.get("tool_calls") or []):
            fn = tc.get("function") or {}
            raw_args = fn.get("arguments") or "{}"
            try:
                parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except ValueError:
                parsed_args = {}
            tool_calls.append(
                ToolCall(
                    id=str(tc.get("id") or f"oai-tc-{idx}"),
                    name=str(fn.get("name") or ""),
                    arguments=parsed_args,
                )
            )

        return LLMResponse(content=content, tool_calls=tool_calls)
