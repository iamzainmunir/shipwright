"""LLM provider abstraction (Canon §5 / doc 05).

One interface over Anthropic / Ollama / OpenAI-compatible providers. The router picks Ollama
(local, free) or a cloud provider when its key is set, else ``NoModelProvider`` — which errors
honestly rather than fabricating output (the product never falls back to a fake model). Usage/cost
is returned per call so the run engine can meter it (doc 05 §7).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class ProviderError(RuntimeError):
    """A provider call failed (network, auth, rate limit, bad response)."""


@dataclass(slots=True)
class LLMResult:
    text: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_cents: int


# ---- tool-use (agent loop) ------------------------------------------------------

@dataclass(slots=True)
class ToolSpec:
    """A tool the agent may call. ``input_schema`` is a JSON Schema object."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(slots=True)
class AgentTurn:
    """One model turn: either it wants tool calls, or it's done (final ``text``)."""

    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_cents: int = 0


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str

    async def complete(
        self, *, system: str, prompt: str, purpose: str = "", max_tokens: int = 1024
    ) -> LLMResult:
        """Return a completion for ``prompt`` under ``system``. ``purpose`` is a free-text
        hint (e.g. ``"spec"``, ``"code"``) some providers use to shape output; real
        providers ignore it."""
        ...

    async def complete_tools(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[ToolSpec],
        max_tokens: int = 2048, tool_choice: str = "auto",
    ) -> AgentTurn:
        """One agent turn over Anthropic-format ``messages``: return tool calls to run, or a
        final answer. The agent loop appends the ``tool_use`` turn and ``tool_result`` reply
        and calls again until there are no tool calls.

        ``tool_choice``: ``"auto"`` lets the model choose; ``"required"`` forces it to call a
        tool this turn (used early in a build so a flaky open model can't reply with prose and
        produce zero files). Providers that can't honour ``"required"`` fall back to ``"auto"``."""
        ...
