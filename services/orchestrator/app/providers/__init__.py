"""Provider router — pick a concrete LLM provider from the environment.

Precedence: ``FOUNDRY_PROVIDER=ollama`` (local, free) → Ollama; else Anthropic when
``ANTHROPIC_API_KEY`` is set; else ``NoModelProvider`` — which errors honestly rather than
fabricating output. The product never falls back to a fake/"mock" model.
"""

from __future__ import annotations

import contextlib
import os

from .anthropic import AnthropicProvider
from .base import AgentTurn, LLMProvider, LLMResult, ProviderError
from .catalog import OPENAI_COMPATIBLE
from .claude_cli import ClaudeCliProvider
from .ollama import OllamaProvider
from .openai_compat import OpenAICompatibleProvider
from .unavailable import NoModelProvider

__all__ = [
    "LLMProvider", "LLMResult", "ProviderError",
    "NoModelProvider", "AnthropicProvider", "ClaudeCliProvider", "OllamaProvider",
    "OpenAICompatibleProvider", "FailoverProvider", "get_provider", "build_provider",
]


class FailoverProvider:
    """Wraps an ordered list of providers and tries each on a ``ProviderError`` until one succeeds —
    so a single call site (a reasoning phase OR a build agent) fails over across EVERY connected model
    and provider (e.g. OpenRouter out of credits → Groq → local Ollama). Raises the last error only
    when ALL are exhausted. ``on_failover(from_model, error)`` is an optional callback for surfacing
    each hop; kept side-effect-free otherwise so it's safe inside the agent tool loop."""

    name = "failover"

    def __init__(self, providers: list, *, on_failover=None) -> None:
        self._providers = [p for p in providers if p is not None]
        self.model = getattr(self._providers[0], "model", "") if self._providers else ""
        self._on_failover = on_failover

    async def _run(self, method: str, **kwargs):
        last: Exception | None = None
        for i, p in enumerate(self._providers):
            try:
                result = await getattr(p, method)(**kwargs)
                self.model = getattr(p, "model", self.model)
                return result
            except ProviderError as exc:
                last = exc
                if self._on_failover and i + 1 < len(self._providers):
                    with contextlib.suppress(Exception):
                        await self._on_failover(getattr(p, "model", "model"), exc)
        raise last or ProviderError("no model available")

    async def complete(self, **kwargs) -> LLMResult:
        return await self._run("complete", **kwargs)

    async def complete_tools(self, **kwargs) -> AgentTurn:
        return await self._run("complete_tools", **kwargs)


def build_provider(
    provider: str, model: str, endpoint: str | None = None, api_key: str | None = None,
    config: dict | None = None,
) -> LLMProvider:
    """Construct a concrete provider from a stored model connection (the UI's active model).

    Ollama runs locally (free). Cloud providers need their API key: the process environment wins
    (the production/Vault path), else the key the user saved on the connection (``api_key``, the
    local convenience so connecting a model in the UI actually enables runs). Without a key (or for
    an unknown provider) we return ``NoModelProvider`` so the run fails with a clear "connect a
    model" error instead of inventing output. ``config`` is the connection's config dict — how the
    Claude CLI provider reads its effort / autonomy settings.
    """
    name = (provider or "").strip().lower()
    # Claude Code CLI: drive the local binary (subscription seat). Not an HTTP endpoint, so it never
    # goes through LiteLLM — intercept it first. No API key: the binary holds the OAuth credential.
    if name == "claude_cli":
        cfg = config or {}
        return ClaudeCliProvider(
            model=model or "auto", effort=cfg.get("effort") or "auto",
            autonomy=cfg.get("autonomy") or "safe", binary=cfg.get("binary") or None,
        )
    # v2: LiteLLM is the default provider layer (plan 03). One adapter over every provider — it
    # keeps our tool-name sanitizer + Ollama text-tool-call salvage. FOUNDRY_LLM_ADAPTER=legacy
    # falls back to the hand-rolled adapters (rollback switch).
    if os.getenv("FOUNDRY_LLM_ADAPTER", "litellm").strip().lower() != "legacy":
        return _build_litellm(name, model, endpoint, api_key)
    if name == "ollama":
        return OllamaProvider(model=model or "qwen2.5:7b", endpoint=endpoint or "http://localhost:11434")
    if name == "anthropic":
        key = os.getenv("ANTHROPIC_API_KEY", "").strip() or (api_key or "").strip()
        if key:
            return AnthropicProvider(api_key=key, model=model or "claude-sonnet-5")
        return NoModelProvider()
    spec = OPENAI_COMPATIBLE.get(name)
    if spec is not None:
        key = os.getenv(spec.env_key, "").strip() or (api_key or "").strip()
        # A generic OpenAI-compatible provider has no fixed base URL — use the connection's endpoint.
        base_url = spec.base_url or (endpoint or "").strip()
        if key and base_url:
            return OpenAICompatibleProvider(
                provider=name, base_url=base_url, api_key=key,
                model=model or (spec.default_models[0] if spec.default_models else ""),
            )
    return NoModelProvider()


def _build_litellm(
    name: str, model: str, endpoint: str | None, api_key: str | None
) -> LLMProvider:
    """LiteLLM path: resolve the key the same way the legacy path does (env wins, else the
    connection's saved key), then hand off to the one LiteLLM adapter. Missing key/model for a
    cloud provider → NoModelProvider (honest failure, never a fake)."""
    from .litellm_provider import LiteLLMProvider

    if name == "ollama":  # local, free — no key required
        return LiteLLMProvider("ollama", model or "qwen2.5:7b", endpoint=endpoint or None)
    if name == "anthropic":
        key = os.getenv("ANTHROPIC_API_KEY", "").strip() or (api_key or "").strip()
        return LiteLLMProvider("anthropic", model or "claude-sonnet-5", api_key=key) if key else NoModelProvider()
    spec = OPENAI_COMPATIBLE.get(name)
    if spec is not None:
        key = os.getenv(spec.env_key, "").strip() or (api_key or "").strip()
        base_url = spec.base_url or (endpoint or "").strip()  # generic passthrough needs an endpoint
        if key and (spec.base_url or base_url):
            return LiteLLMProvider(
                name, model or (spec.default_models[0] if spec.default_models else ""),
                endpoint=base_url or None, api_key=key,
            )
    return NoModelProvider()


def get_provider() -> LLMProvider:
    provider = os.getenv("FOUNDRY_PROVIDER", "").strip().lower()
    if provider == "ollama" or os.getenv("FOUNDRY_OLLAMA_MODEL", "").strip():
        model = os.getenv("FOUNDRY_OLLAMA_MODEL", "qwen2.5:7b").strip() or "qwen2.5:7b"
        endpoint = os.getenv("FOUNDRY_OLLAMA_ENDPOINT", "http://localhost:11434").strip()
        return OllamaProvider(model=model, endpoint=endpoint)

    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if key:
        model = os.getenv("FOUNDRY_DEFAULT_MODEL", "claude-sonnet-5").strip() or "claude-sonnet-5"
        return AnthropicProvider(api_key=key, model=model)
    return NoModelProvider()
