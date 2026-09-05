"""Anthropic adapter (real). Activated by the router when ``ANTHROPIC_API_KEY`` is set.

Uses the Messages API over httpx. Cost is derived from a small per-model price table
(USD per million tokens); unknown models fall back to a conservative default. Errors are
normalized to :class:`ProviderError` so the engine can raise a ``token``/``error`` blocker.
"""

from __future__ import annotations

from typing import Any

import httpx

from .base import AgentTurn, LLMResult, ProviderError, ToolCall, ToolSpec

_API_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"

# USD per 1M tokens (input, output). Illustrative; refine from doc 05 catalog.
_PRICE: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (15.0, 75.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (0.80, 4.0),
}
_DEFAULT_PRICE = (3.0, 15.0)


def _cost_cents(model: str, tokens_in: int, tokens_out: int) -> int:
    pin, pout = _PRICE.get(model, _DEFAULT_PRICE)
    usd = (tokens_in / 1_000_000) * pin + (tokens_out / 1_000_000) * pout
    return round(usd * 100)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str = "claude-sonnet-5", *, timeout: float = 60.0):
        self._api_key = api_key
        self.model = model
        self._timeout = timeout

    async def complete(
        self, *, system: str, prompt: str, purpose: str = "", max_tokens: int = 1024
    ) -> LLMResult:
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": _API_VERSION,
            "content-type": "application/json",
        }
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(_API_URL, headers=headers, json=body)
        except httpx.HTTPError as exc:  # network / timeout
            raise ProviderError(f"anthropic request failed: {exc}") from exc

        if resp.status_code == 401:
            raise ProviderError("anthropic auth failed (expired or invalid key)")
        if resp.status_code == 429:
            raise ProviderError("anthropic rate/usage limit reached")
        if resp.status_code >= 400:
            raise ProviderError(f"anthropic error {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        parts = data.get("content", [])
        text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
        usage = data.get("usage", {})
        tokens_in = int(usage.get("input_tokens", 0))
        tokens_out = int(usage.get("output_tokens", 0))
        return LLMResult(
            text=text,
            model=self.model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_cents=_cost_cents(self.model, tokens_in, tokens_out),
        )

    async def complete_tools(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[ToolSpec],
        max_tokens: int = 2048, tool_choice: str = "auto",
    ) -> AgentTurn:
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": _API_VERSION,
            "content-type": "application/json",
        }
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
            "tools": [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in tools
            ],
        }
        # "required" → Anthropic's {"type": "any"} (must use a tool this turn).
        if tool_choice == "required":
            body["tool_choice"] = {"type": "any"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(_API_URL, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise ProviderError(f"anthropic request failed: {exc}") from exc
        if resp.status_code == 401:
            raise ProviderError("anthropic auth failed (expired or invalid key)")
        if resp.status_code == 429:
            raise ProviderError("anthropic rate/usage limit reached")
        if resp.status_code >= 400:
            raise ProviderError(f"anthropic error {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        blocks = data.get("content", [])
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        tool_calls = [
            ToolCall(id=b["id"], name=b["name"], input=b.get("input", {}))
            for b in blocks if b.get("type") == "tool_use"
        ]
        usage = data.get("usage", {})
        ti = int(usage.get("input_tokens", 0))
        to = int(usage.get("output_tokens", 0))
        return AgentTurn(text or None, tool_calls, ti, to, _cost_cents(self.model, ti, to))
