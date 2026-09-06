"""Ollama adapter (real, local). Talks to a local Ollama server's ``/api/chat`` endpoint.

Selected by the router when ``SHIPWRIGHT_PROVIDER=ollama`` (or ``SHIPWRIGHT_OLLAMA_MODEL`` is set).
Local models are free, so ``cost_cents`` is always 0; token counts come from Ollama's
``prompt_eval_count`` / ``eval_count``. Tool-use maps to Ollama's OpenAI-style ``tools`` schema
(models like ``qwen2.5`` support it; ``gemma2`` does not — it simply returns text).
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from .base import AgentTurn, LLMResult, ProviderError, ToolCall, ToolSpec

_DEFAULT_ENDPOINT = "http://localhost:11434"
_DEFAULT_MODEL = "qwen2.5:7b"


class OllamaProvider:
    name = "ollama"

    def __init__(
        self, model: str = _DEFAULT_MODEL, endpoint: str = _DEFAULT_ENDPOINT, *, timeout: float = 600.0
    ) -> None:
        self.model = model
        self._url = endpoint.rstrip("/") + "/api/chat"
        self._timeout = timeout

    async def complete(
        self, *, system: str, prompt: str, purpose: str = "", max_tokens: int = 1024
    ) -> LLMResult:
        data = await self._post({
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "options": {"num_predict": max_tokens},
        })
        message = data.get("message", {})
        return LLMResult(
            text=message.get("content", ""),
            model=self.model,
            tokens_in=int(data.get("prompt_eval_count", 0)),
            tokens_out=int(data.get("eval_count", 0)),
            cost_cents=0,  # local models run free
        )

    async def complete_tools(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[ToolSpec],
        max_tokens: int = 2048, tool_choice: str = "auto",
    ) -> AgentTurn:
        # Ollama's chat API has no first-class tool_choice; the agent loop's prose-nudge +
        # text salvage handle a model that won't tool-call. Accepted for protocol parity.
        _ = tool_choice
        ollama_messages = [{"role": "system", "content": system}]
        ollama_messages.extend(_to_ollama_message(m) for m in messages)
        data = await self._post({
            "model": self.model,
            "stream": False,
            "messages": ollama_messages,
            "tools": [_to_ollama_tool(t) for t in tools],
            "options": {"num_predict": max_tokens},
        })
        message = data.get("message", {})
        calls: list[ToolCall] = []
        for i, raw in enumerate(message.get("tool_calls") or []):
            fn = raw.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            calls.append(ToolCall(id=f"call_{i}", name=fn.get("name", ""), input=args))
        content = message.get("content") or None
        # Fallback: many local models (qwen2.5-coder, etc.) emit the tool call as JSON TEXT in
        # `content` instead of the native `tool_calls` array. Without this, the agent loop sees "no
        # tool calls", treats the JSON as final text, and writes NO files — the build produces nothing.
        if not calls and content:
            text_calls = _tool_calls_from_text(content, {t.name for t in tools})
            if text_calls:
                calls = text_calls
                content = None  # the content WAS the tool call, not a message
        return AgentTurn(
            content,
            calls,
            int(data.get("prompt_eval_count", 0)),
            int(data.get("eval_count", 0)),
            0,
        )

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(self._url, json=body)
        except httpx.HTTPError as exc:  # server down / timeout
            raise ProviderError(f"ollama request failed ({self._url}): {exc}") from exc
        if resp.status_code == 404:
            raise ProviderError(f"ollama model not found: {self.model} — try `ollama pull {self.model}`")
        if resp.status_code >= 400:
            raise ProviderError(f"ollama error {resp.status_code}: {resp.text[:200]}")
        return resp.json()


def _iter_json_objects(text: str):
    """Yield top-level JSON objects found in ``text`` by brace-matching (tolerant of surrounding prose
    and of multiple objects). Handles the nested braces inside a tool call's ``arguments``."""
    depth, start, in_str, esc = 0, -1, False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    chunk = text[start:i + 1]
                    try:
                        yield json.loads(chunk)
                    except json.JSONDecodeError:
                        pass
                    start = -1


def _tool_calls_from_text(content: str, tool_names: set[str]) -> list[ToolCall]:
    """Extract tool calls a local model emitted as JSON TEXT (not the native ``tool_calls`` array).

    Recognises: a bare ``{"name","arguments"}`` object, ```` ```json ... ``` ```` fenced blocks, and
    ``<tool_call>{...}</tool_call>`` tags — the shapes qwen/llama-family local models actually produce.
    Only objects whose ``name`` is a real tool are returned, so ordinary JSON output isn't misread."""
    import re

    text = content.strip()
    # Pull the inside of <tool_call>…</tool_call> tags first, then scan the whole text too.
    tagged = re.findall(r"<tool_call>\s*(.*?)\s*</tool_call>", text, re.DOTALL)
    blobs = [*tagged, text]
    calls: list[ToolCall] = []
    seen: set[str] = set()
    for blob in blobs:
        for obj in _iter_json_objects(blob):
            name = obj.get("name")
            if name not in tool_names:
                continue
            args = obj.get("arguments")
            if args is None:
                args = obj.get("parameters") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            key = f"{name}:{json.dumps(args, sort_keys=True)[:200]}"
            if key in seen:
                continue
            seen.add(key)
            calls.append(ToolCall(id=f"txt_{len(calls)}", name=name, input=args if isinstance(args, dict) else {}))
    return calls


def _to_ollama_tool(tool: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


def _to_ollama_message(message: dict[str, Any]) -> dict[str, Any]:
    """Convert an Anthropic-format message (str or content blocks) to Ollama's chat format."""
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
            tool_calls.append({"function": {"name": block.get("name", ""), "arguments": block.get("input", {})}})
        elif kind == "tool_result":
            # A tool result is its own Ollama "tool" message.
            return {"role": "tool", "content": _stringify(block.get("content", ""))}

    out: dict[str, Any] = {"role": role, "content": "\n".join(text_parts)}
    if tool_calls:
        out["tool_calls"] = tool_calls
    return out


def _stringify(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value)
