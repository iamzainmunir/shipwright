"""LiteLLM-backed provider (v2 Phase 1 — plan 03 §4).

One instance per (provider, model), satisfying the existing ``LLMProvider`` Protocol so it
drops in behind ``build_provider`` / ``FailoverProvider`` with zero engine changes. Chain-level
failover stays in ``FailoverProvider`` (agent-scoped, invariant 6) — we do NOT use LiteLLM's
Router, whose global fallbacks would violate "failover only across THIS agent's models".

Two hard-won guards are kept, NOT delegated to LiteLLM (which does not do them reliably):
  * tool-name sanitizer enforcing Anthropic's ``^[a-zA-Z0-9_-]{1,64}$`` (litellm #17904) with a
    reverse map so returned tool_calls carry the ORIGINAL names;
  * the Ollama/local text-emitted tool-call salvage (``_tool_calls_from_text``) — without it,
    local models that print the tool call as JSON text write zero files (the 2026-08-24 incident).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import litellm

from .base import AgentTurn, LLMResult, ProviderError, ToolCall, ToolSpec
from .catalog import default_price
from .ollama import _tool_calls_from_text

# Drop provider-unsupported params (e.g. temperature on reasoning models) instead of erroring.
litellm.drop_params = True

# Catalog slug → LiteLLM provider prefix (native cost tables + quirks). Anything not here
# (``openai_compatible`` and any unknown slug) routes through the OpenAI-compatible passthrough
# with an explicit ``api_base`` + ``api_key`` — identical reach to the old openai_compat adapter.
_PREFIX: dict[str, str] = {
    "anthropic": "anthropic",
    "openai": "openai",
    "google": "gemini",
    "xai": "xai",
    "groq": "groq",
    "mistral": "mistral",
    "deepseek": "deepseek",
    "together": "together_ai",
    "cohere": "cohere",
    "openrouter": "openrouter",
    "perplexity": "perplexity",
    "fireworks": "fireworks_ai",
    "deepinfra": "deepinfra",
    "cerebras": "cerebras",
    "nebius": "nebius",
    # ollama handled specially (needs api_base); openai_compatible → passthrough (see below).
}
_DEFAULT_OLLAMA = "http://localhost:11434"
_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class LiteLLMProvider:
    def __init__(
        self, provider: str, model: str, endpoint: str | None = None, api_key: str | None = None
    ) -> None:
        self.name = provider
        self.model = model
        self._endpoint = (endpoint or "").strip() or None
        self._key = (api_key or "").strip() or None

    # ---- litellm call kwargs ----------------------------------------------------
    def _base_kwargs(self) -> dict[str, Any]:
        """Model string + api_base/api_key per provider, for litellm.acompletion."""
        slug = (self.name or "").strip().lower()
        kw: dict[str, Any] = {}
        if slug == "ollama":
            kw["model"] = f"ollama_chat/{self.model}"
            kw["api_base"] = self._endpoint or _DEFAULT_OLLAMA
        elif slug in _PREFIX:
            kw["model"] = f"{_PREFIX[slug]}/{self.model}"
            if self._key:
                kw["api_key"] = self._key
            if self._endpoint:  # some native providers accept a custom base (proxies, Azure-like)
                kw["api_base"] = self._endpoint
        else:  # openai_compatible / unknown → OpenAI-format passthrough at the given endpoint
            kw["model"] = f"openai/{self.model}"
            if self._endpoint:
                kw["api_base"] = self._endpoint.rstrip("/")
            if self._key:
                kw["api_key"] = self._key
        return kw

    async def complete(
        self, *, system: str, prompt: str, purpose: str = "", max_tokens: int = 1024
    ) -> LLMResult:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        resp = await self._acompletion(messages=messages, max_tokens=max_tokens)
        msg = _message(resp)
        ti, to = _usage(resp)
        return LLMResult(
            text=_get(msg, "content") or "",
            model=self.model,
            tokens_in=ti, tokens_out=to,
            cost_cents=_cost_cents(resp, ti, to, provider=self.name, model=self.model),
        )

    async def complete_tools(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[ToolSpec],
        max_tokens: int = 2048, tool_choice: str = "auto",
    ) -> AgentTurn:
        original_names = {t.name for t in tools}
        sanitized, reverse = _sanitize_tools(tools)
        oai_messages = [{"role": "system", "content": system}, *_to_litellm_messages(messages)]
        schemas = [_tool_schema(t) for t in sanitized]
        choice = tool_choice if tool_choice in ("auto", "required", "none") else "auto"
        try:
            resp = await self._acompletion(
                messages=oai_messages, max_tokens=max_tokens, tools=schemas, tool_choice=choice,
            )
        except ProviderError:
            # Some models/providers reject tool_choice="required" (or "none"); retry once on
            # "auto" so a forced-tool build turn degrades gracefully instead of failing over.
            if choice == "auto":
                raise
            resp = await self._acompletion(
                messages=oai_messages, max_tokens=max_tokens, tools=schemas, tool_choice="auto",
            )
        msg = _message(resp)
        content = _get(msg, "content") or None
        calls: list[ToolCall] = []
        for raw in _get(msg, "tool_calls") or []:
            fn = _get(raw, "function") or {}
            args = _get(fn, "arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            sane = _get(fn, "name") or ""
            calls.append(ToolCall(id=_get(raw, "id") or "", name=reverse.get(sane, sane), input=args or {}))
        # Local-model salvage: tool call emitted as JSON TEXT, not the native array.
        if not calls and content:
            salvaged = _tool_calls_from_text(content, original_names)
            if salvaged:
                calls, content = salvaged, None
        ti, to = _usage(resp)
        return AgentTurn(content, calls, ti, to,
                         _cost_cents(resp, ti, to, provider=self.name, model=self.model))

    async def _acompletion(self, **call_kwargs: Any):
        kwargs = {**self._base_kwargs(), **call_kwargs, "num_retries": 1, "timeout": 120}
        try:
            return await litellm.acompletion(**kwargs)
        except Exception as exc:  # noqa: BLE001 — normalize every provider failure to ProviderError
            raise ProviderError(_error_text(self.name, exc)) from exc


# ---- helpers (module-level, so they're unit-testable in isolation) ------------------

def _sanitize_tools(tools: list[ToolSpec]) -> tuple[list[ToolSpec], dict[str, str]]:
    """Return (tools with valid names, sanitized→original map). Valid names pass through."""
    out: list[ToolSpec] = []
    reverse: dict[str, str] = {}
    for t in tools:
        safe = _sanitize_name(t.name)
        reverse[safe] = t.name
        out.append(t if safe == t.name else ToolSpec(name=safe, description=t.description,
                                                     input_schema=t.input_schema))
    return out, reverse


def _sanitize_name(name: str) -> str:
    if _NAME_RE.match(name):
        return name
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", name) or "tool"
    if len(cleaned) <= 64:
        return cleaned
    # deterministic truncate + hash so long/renamed tools stay stable + reversible
    digest = hashlib.sha256(name.encode()).hexdigest()[:8]
    return f"{cleaned[:55]}_{digest}"


def _tool_schema(t: ToolSpec) -> dict[str, Any]:
    return {"type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.input_schema}}


def _to_litellm_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic-format → OpenAI chat format. A user message may hold MULTIPLE tool_result blocks
    (one per tool call in the prior turn); each becomes its own ``tool`` message (fixing the
    single-result limitation of the legacy adapter — OpenAI requires one tool msg per tool_call_id)."""
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in content or []:
            kind = block.get("type")
            if kind == "text":
                text_parts.append(block.get("text", ""))
            elif kind == "tool_use":
                tool_calls.append({
                    "id": block.get("id", ""), "type": "function",
                    "function": {"name": block.get("name", ""),
                                 "arguments": json.dumps(block.get("input", {}))},
                })
            elif kind == "tool_result":
                out.append({"role": "tool", "tool_call_id": block.get("tool_use_id", ""),
                            "content": _stringify(block.get("content", ""))})
        if text_parts or tool_calls:
            msg: dict[str, Any] = {"role": role, "content": "\n".join(text_parts)}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            out.append(msg)
    return out


def _stringify(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Attribute-or-item access, so real litellm ModelResponse objects and test fakes both work."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _message(resp: Any) -> Any:
    choices = _get(resp, "choices") or []
    return _get(choices[0], "message") if choices else None


def _usage(resp: Any) -> tuple[int, int]:
    u = _get(resp, "usage") or {}
    return int(_get(u, "prompt_tokens", 0) or 0), int(_get(u, "completion_tokens", 0) or 0)


def _is_free_model(provider: str, model: str) -> bool:
    """A run that costs nothing: a local runtime (Ollama runs on the user's own hardware) or an
    explicitly free hosted model (the ``:free`` suffix convention, e.g. OpenRouter's
    ``z-ai/glm-5.2:free``). These must report $0 — never the fabricated default price."""
    return (provider or "").strip().lower() == "ollama" or ":free" in (model or "").lower()


def _cost_cents(resp: Any, tokens_in: int, tokens_out: int, *,
                provider: str = "", model: str = "") -> int:
    # Free/local models cost nothing — don't let the illustrative default table invent spend
    # (this is what showed $0.46 on a `:free` model). Check BEFORE the pricing fallback.
    if _is_free_model(provider, model):
        return 0
    try:
        usd = litellm.completion_cost(completion_response=resp)
        if usd:
            return round(float(usd) * 100)
    except Exception:  # noqa: BLE001 — unknown model / offline: fall back to the default table
        pass
    pin, pout = default_price()
    return round(((tokens_in / 1_000_000) * pin + (tokens_out / 1_000_000) * pout) * 100)


def _error_text(provider: str, exc: Exception) -> str:
    """Normalize a litellm exception to a clear ProviderError message (defensive across versions)."""
    name = type(exc).__name__.lower()
    msg = str(exc)
    low = (name + " " + msg).lower()
    if "auth" in name or "401" in low or "invalid api key" in low or "403" in low:
        return f"{provider} auth failed (invalid or expired key)"
    if "402" in low or "insufficient" in low or "credit" in low or "budget" in low:
        return f"{provider} insufficient credits — add credits or use another model"
    if "ratelimit" in name or "429" in low or "rate limit" in low or "quota" in low:
        return f"{provider} rate/usage limit reached"
    return f"{provider} error: {msg[:200]}"
