"""The honest fallback when no real model is connected.

The product never fabricates AI output: if a run can't resolve a real, connected model, it must
fail loudly with a clear, actionable error rather than silently returning canned/"mock" text.
``NoModelProvider`` is that fallback — every call raises so the run engine surfaces a red error
telling the user to connect a model.
"""

from __future__ import annotations

from typing import Any

from .base import AgentTurn, LLMResult, ProviderError, ToolSpec

_MESSAGE = (
    "No model is connected. Connect a model in Models → Connect model (e.g. a local Ollama model, "
    "or a cloud provider with its API key) and set it as the active model, then run again."
)


class NoModelProvider:
    """A provider that always errors — used when nothing real is configured."""

    name = "none"
    model = "none"

    async def complete(
        self, *, system: str, prompt: str, purpose: str = "", max_tokens: int = 1024
    ) -> LLMResult:
        raise ProviderError(_MESSAGE)

    async def complete_tools(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[ToolSpec],
        max_tokens: int = 2048, tool_choice: str = "auto",
    ) -> AgentTurn:
        raise ProviderError(_MESSAGE)
