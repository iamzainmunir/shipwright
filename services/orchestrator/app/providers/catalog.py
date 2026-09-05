"""Cloud-provider registry for the OpenAI-compatible family.

Every provider here speaks the OpenAI Chat Completions API (``POST {base}/chat/completions``,
``GET {base}/models``), so one adapter (:class:`OpenAICompatibleProvider`) serves them all —
only the base URL and the API-key env var differ. Anthropic and Ollama have their own adapters
and are NOT in this table.

Each entry is the single source of truth for: the base URL, the env var the run engine reads
the key from, whether tool-use is supported, and suggested default models for the connect UI.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderSpec:
    key: str  # canonical provider id (matches ModelProvider value)
    label: str  # human label for the connect UI
    base_url: str  # OpenAI-compatible base (no trailing /chat/completions)
    env_key: str  # env var the run engine reads the API key from
    default_models: list[str] = field(default_factory=list)
    supports_tools: bool = True


# USD per 1M tokens (input, output) — illustrative defaults used only when a model is unknown.
_DEFAULT_PRICE = (1.0, 3.0)

OPENAI_COMPATIBLE: dict[str, ProviderSpec] = {
    "openai": ProviderSpec(
        "openai", "OpenAI (GPT / Codex)", "https://api.openai.com/v1", "OPENAI_API_KEY",
        ["gpt-4o", "gpt-4o-mini", "o1-mini"],
    ),
    "google": ProviderSpec(
        "google", "Google (Gemini)", "https://generativelanguage.googleapis.com/v1beta/openai",
        "GEMINI_API_KEY", ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"],
    ),
    "xai": ProviderSpec(
        "xai", "xAI (Grok)", "https://api.x.ai/v1", "XAI_API_KEY",
        ["grok-2-latest", "grok-2-mini"],
    ),
    "groq": ProviderSpec(
        "groq", "Groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY",
        ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
    ),
    "mistral": ProviderSpec(
        "mistral", "Mistral", "https://api.mistral.ai/v1", "MISTRAL_API_KEY",
        ["mistral-large-latest", "mistral-small-latest"],
    ),
    "deepseek": ProviderSpec(
        "deepseek", "DeepSeek", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY",
        ["deepseek-chat", "deepseek-reasoner"],
    ),
    "together": ProviderSpec(
        "together", "Together AI", "https://api.together.xyz/v1", "TOGETHER_API_KEY",
        ["meta-llama/Llama-3.3-70B-Instruct-Turbo", "Qwen/Qwen2.5-72B-Instruct-Turbo"],
    ),
    "cohere": ProviderSpec(
        "cohere", "Cohere", "https://api.cohere.ai/compatibility/v1", "COHERE_API_KEY",
        ["command-r-plus", "command-r"],
    ),
    # ── aggregators & popular OpenAI-compatible inference providers ──────────────────────────────
    "openrouter": ProviderSpec(
        "openrouter", "OpenRouter (100+ models)", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
        ["anthropic/claude-sonnet-4.5", "openai/gpt-4o", "google/gemini-2.0-flash-001",
         "meta-llama/llama-3.3-70b-instruct", "deepseek/deepseek-chat", "qwen/qwen-2.5-coder-32b-instruct"],
    ),
    "perplexity": ProviderSpec(
        "perplexity", "Perplexity (Sonar)", "https://api.perplexity.ai", "PERPLEXITY_API_KEY",
        ["sonar", "sonar-pro", "sonar-reasoning-pro"],
    ),
    "fireworks": ProviderSpec(
        "fireworks", "Fireworks AI", "https://api.fireworks.ai/inference/v1", "FIREWORKS_API_KEY",
        ["accounts/fireworks/models/llama-v3p3-70b-instruct",
         "accounts/fireworks/models/qwen2p5-coder-32b-instruct",
         "accounts/fireworks/models/deepseek-v3"],
    ),
    "deepinfra": ProviderSpec(
        "deepinfra", "DeepInfra", "https://api.deepinfra.com/v1/openai", "DEEPINFRA_API_KEY",
        ["meta-llama/Llama-3.3-70B-Instruct", "Qwen/Qwen2.5-Coder-32B-Instruct", "deepseek-ai/DeepSeek-V3"],
    ),
    "cerebras": ProviderSpec(
        "cerebras", "Cerebras (fast)", "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY",
        ["llama-3.3-70b", "llama3.1-8b", "qwen-3-32b"],
    ),
    "nebius": ProviderSpec(
        "nebius", "Nebius AI Studio", "https://api.studio.nebius.com/v1", "NEBIUS_API_KEY",
        ["meta-llama/Llama-3.3-70B-Instruct", "Qwen/Qwen2.5-Coder-32B-Instruct", "deepseek-ai/DeepSeek-V3"],
    ),
    # Generic escape hatch: ANY OpenAI-compatible endpoint (Azure OpenAI, a self-hosted vLLM/LM Studio,
    # etc.). base_url is EMPTY — the connection's own ``endpoint`` is used at runtime instead.
    "openai_compatible": ProviderSpec(
        "openai_compatible", "OpenAI-compatible (custom URL)", "", "OPENAI_COMPATIBLE_API_KEY", [],
    ),
}


def default_price() -> tuple[float, float]:
    return _DEFAULT_PRICE
