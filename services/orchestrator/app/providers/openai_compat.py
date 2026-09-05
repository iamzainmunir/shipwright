"""One adapter for every OpenAI-compatible provider (OpenAI, xAI/Grok, Groq, Mistral,
DeepSeek, Together, Google/Gemini, Cohere).

They all speak the OpenAI Chat Completions API, so only the base URL + API key differ
(see ``catalog.py``). Anthropic-format tool messages are mapped to OpenAI's ``tools`` /
``tool_calls`` shape. Cost is best-effort from a small default price table.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from .base import AgentTurn, LLMResult, ProviderError, ToolCall, ToolSpec
from .catalog import default_price


class OpenAICompatibleProvider:
    def __init__(self, *, provider: str, base_url: str, api_key: str, model: str, timeout: float = 90.0):
        self.name = provider
        self.model = model
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._timeout = timeout

    # ---- text completion --------------------------------------------------------
    async def complete(
        self, *, system: str, prompt: str, purpose: str = "", max_tokens: int = 1024
    ) -> LLMResult:
        data = await self._post({
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        })
        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        ti, to = _usage(data)
        return LLMResult(text=text, model=self.model, tokens_in=ti, tokens_out=to,
                         cost_cents=_cost_cents(ti, to))

    # ---- one agent turn (tool use) ---------------------------------------------
    async def complete_tools(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[ToolSpec],
        max_tokens: int = 2048,
    ) -> AgentTurn:
        oai_messages = [{"role": "system", "content": system}]
        oai_messages.extend(_to_openai_message(m) for m in messages)
        data = await self._post({
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": oai_messages,
            "tools": [_to_openai_tool(t) for t in tools],
        })
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        calls: list[ToolCall] = []
        for raw in message.get("tool_calls") or []:
            fn = raw.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            calls.append(ToolCall(id=raw.get("id", ""), name=fn.get("name", ""), input=args))
        ti, to = _usage(data)
        return AgentTurn(message.get("content") or None, calls, ti, to, _cost_cents(ti, to))

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(f"{self._base}/chat/completions", headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name} request failed: {exc}") from exc
        if resp.status_code in (401, 403):
            raise ProviderError(f"{self.name} auth failed (invalid or expired key)")
        if resp.status_code == 402:
            raise ProviderError(
                f"{self.name} insufficient credits — add credits to your {self.name} account (or lower "
                "the parallel build size / use a cheaper model); the request exceeded your balance")
        if resp.status_code == 429:
            raise ProviderError(f"{self.name} rate/usage limit reached")
        if resp.status_code >= 400:
            raise ProviderError(f"{self.name} error {resp.status_code}: {resp.text[:200]}")
        return resp.json()


def _usage(data: dict[str, Any]) -> tuple[int, int]:
    usage = data.get("usage") or {}
    return int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))


def _cost_cents(tokens_in: int, tokens_out: int) -> int:
    pin, pout = default_price()
    usd = (tokens_in / 1_000_000) * pin + (tokens_out / 1_000_000) * pout
    return round(usd * 100)


def _to_openai_tool(tool: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {"name": tool.name, "description": tool.description, "parameters": tool.input_schema},
    }


def _to_openai_message(message: dict[str, Any]) -> dict[str, Any]:
    """Map an Anthropic-format message (str or content blocks) to OpenAI chat format."""
    role = message.get("role", "user")
    content = message.get("content")
    if isinstance(content, str):
        return {"role": role, "content": content}

    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for block in content or []:
        kind = block.get("type")
        if kind == "text":
            text_parts.append(block.get("text", ""))
        elif kind == "tool_use":
            tool_calls.append({
                "id": block.get("id", ""),
                "type": "function",
                "function": {"name": block.get("name", ""),
                             "arguments": json.dumps(block.get("input", {}))},
            })
        elif kind == "tool_result":
            # A tool result becomes its own OpenAI "tool" message.
            return {"role": "tool", "tool_call_id": block.get("tool_use_id", ""),
                    "content": _stringify(block.get("content", ""))}

    out: dict[str, Any] = {"role": role, "content": "\n".join(text_parts)}
    if tool_calls:
        out["tool_calls"] = tool_calls
    return out


def _stringify(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value)
