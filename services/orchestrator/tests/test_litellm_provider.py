"""v2 Phase 1 — LiteLLMProvider. Fully offline: litellm.acompletion is monkeypatched.

Covers the two hard guards (tool-name sanitizer + reverse map, local text-tool-call salvage),
per-provider model-string mapping, usage/cost, robust message conversion, and error mapping.
"""

from __future__ import annotations

import types

import pytest
from app.providers import litellm_provider as lp
from app.providers.base import ProviderError, ToolSpec
from app.providers.litellm_provider import (
    LiteLLMProvider,
    _sanitize_name,
    _sanitize_tools,
    _to_litellm_messages,
)

_WRITE = ToolSpec(name="fs_write", description="write", input_schema={"type": "object"})


def _resp(*, content=None, tool_calls=None, pin=10, pout=5):
    """A minimal ModelResponse-shaped fake (attribute access, like litellm's object)."""
    fn_calls = []
    for tc in tool_calls or []:
        fn_calls.append(types.SimpleNamespace(
            id=tc["id"],
            function=types.SimpleNamespace(name=tc["name"], arguments=tc["arguments"]),
        ))
    message = types.SimpleNamespace(content=content, tool_calls=fn_calls or None)
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=message)],
        usage=types.SimpleNamespace(prompt_tokens=pin, completion_tokens=pout),
    )


@pytest.fixture
def captured(monkeypatch):
    """Capture the kwargs litellm.acompletion is called with; return a canned response."""
    box: dict = {}
    canned: dict = {"resp": _resp(content="ok")}

    async def fake_acompletion(**kwargs):
        box.update(kwargs)
        if isinstance(canned["resp"], Exception):
            raise canned["resp"]
        return canned["resp"]

    monkeypatch.setattr(lp.litellm, "acompletion", fake_acompletion)
    monkeypatch.setattr(lp.litellm, "completion_cost", lambda **_: 0.0)  # force default-table cost
    return box, canned


# ---- tool-name sanitizer -------------------------------------------------------------

def test_sanitizer_passes_valid_names_unchanged() -> None:
    assert _sanitize_name("fs_write") == "fs_write"
    assert _sanitize_name("git-diff") == "git-diff"


def test_sanitizer_replaces_invalid_chars() -> None:
    assert _sanitize_name("fs.write") == "fs_write"
    assert _sanitize_name("do stuff!") == "do_stuff_"


def test_sanitizer_truncates_and_hashes_long_names() -> None:
    long = "x" * 100
    out = _sanitize_name(long)
    assert len(out) <= 64 and out.startswith("x" * 55) and "_" in out
    assert _sanitize_name(long) == out  # deterministic


def test_sanitize_tools_builds_reverse_map() -> None:
    tools = [ToolSpec(name="fs.write", description="w", input_schema={}),
             ToolSpec(name="fs_read", description="r", input_schema={})]
    safe, reverse = _sanitize_tools(tools)
    assert {t.name for t in safe} == {"fs_write", "fs_read"}
    assert reverse == {"fs_write": "fs.write", "fs_read": "fs_read"}


# ---- model-string mapping ------------------------------------------------------------

def test_model_string_native_prefixes() -> None:
    assert LiteLLMProvider("anthropic", "claude-sonnet-4.5")._base_kwargs()["model"] == "anthropic/claude-sonnet-4.5"
    assert LiteLLMProvider("groq", "gpt-oss-120b")._base_kwargs()["model"] == "groq/gpt-oss-120b"
    assert LiteLLMProvider("openrouter", "x/y")._base_kwargs()["model"] == "openrouter/x/y"


def test_model_string_ollama_sets_api_base() -> None:
    kw = LiteLLMProvider("ollama", "qwen2.5-coder:7b")._base_kwargs()
    assert kw["model"] == "ollama_chat/qwen2.5-coder:7b"
    assert kw["api_base"] == "http://localhost:11434"
    kw2 = LiteLLMProvider("ollama", "m", endpoint="http://box:1234")._base_kwargs()
    assert kw2["api_base"] == "http://box:1234"


def test_model_string_openai_compatible_passthrough() -> None:
    kw = LiteLLMProvider("openai_compatible", "my-model", endpoint="https://host/v1", api_key="k")._base_kwargs()
    assert kw["model"] == "openai/my-model"
    assert kw["api_base"] == "https://host/v1"
    assert kw["api_key"] == "k"


async def test_complete_passes_key_and_returns_usage(captured) -> None:
    box, canned = captured
    canned["resp"] = _resp(content="hello", pin=12, pout=7)
    prov = LiteLLMProvider("anthropic", "claude-sonnet-4.5", api_key="sk-test")
    r = await prov.complete(system="s", prompt="p", max_tokens=100)
    assert r.text == "hello" and r.tokens_in == 12 and r.tokens_out == 7
    assert box["model"] == "anthropic/claude-sonnet-4.5" and box["api_key"] == "sk-test"
    assert box["max_tokens"] == 100 and box["num_retries"] == 1


# ---- tool calls + reverse mapping + salvage ------------------------------------------

async def test_complete_tools_restores_original_names(captured) -> None:
    _box, canned = captured
    # model was given the sanitized name and calls it; provider must restore the original.
    canned["resp"] = _resp(tool_calls=[
        {"id": "c1", "name": "fs_write", "arguments": '{"path": "a.js", "content": "x"}'}])
    prov = LiteLLMProvider("groq", "m")
    tools = [ToolSpec(name="fs.write", description="w", input_schema={})]  # dotted → sanitized to fs_write
    turn = await prov.complete_tools(system="s", messages=[{"role": "user", "content": "go"}], tools=tools)
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].name == "fs.write"  # ORIGINAL name restored
    assert turn.tool_calls[0].input == {"path": "a.js", "content": "x"}


async def test_complete_tools_salvages_text_emitted_call(captured) -> None:
    _box, canned = captured
    canned["resp"] = _resp(content='{"name": "fs_write", "arguments": {"path": "b.js", "content": "y"}}')
    prov = LiteLLMProvider("ollama", "qwen2.5-coder:7b")
    turn = await prov.complete_tools(system="s", messages=[{"role": "user", "content": "go"}], tools=[_WRITE])
    assert turn.text is None
    assert turn.tool_calls and turn.tool_calls[0].name == "fs_write"
    assert turn.tool_calls[0].input["path"] == "b.js"


async def test_complete_tools_malformed_args_default_to_empty(captured) -> None:
    _box, canned = captured
    canned["resp"] = _resp(tool_calls=[{"id": "c1", "name": "fs_write", "arguments": "not json"}])
    prov = LiteLLMProvider("groq", "m")
    turn = await prov.complete_tools(system="s", messages=[{"role": "user", "content": "go"}], tools=[_WRITE])
    assert turn.tool_calls[0].input == {}


# ---- message conversion (multi tool_result) ------------------------------------------

def test_message_conversion_expands_multiple_tool_results() -> None:
    messages = [
        {"role": "user", "content": "build it"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "fs_write", "input": {"path": "a"}},
            {"type": "tool_use", "id": "t2", "name": "fs_write", "input": {"path": "b"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "wrote a"},
            {"type": "tool_result", "tool_use_id": "t2", "content": "wrote b"},
        ]},
    ]
    out = _to_litellm_messages(messages)
    tool_msgs = [m for m in out if m["role"] == "tool"]
    assert {m["tool_call_id"] for m in tool_msgs} == {"t1", "t2"}  # BOTH results, not just the first
    asst = [m for m in out if m["role"] == "assistant"][0]
    assert len(asst["tool_calls"]) == 2


# ---- error mapping -------------------------------------------------------------------

async def test_rate_limit_maps_to_provider_error(captured) -> None:
    _box, canned = captured
    canned["resp"] = RuntimeError("openai RateLimitError: 429 quota exceeded")
    prov = LiteLLMProvider("groq", "m")
    with pytest.raises(ProviderError, match="rate/usage limit"):
        await prov.complete(system="s", prompt="p")


async def test_auth_and_credit_errors_map(captured) -> None:
    _box, canned = captured
    canned["resp"] = RuntimeError("AuthenticationError: invalid api key")
    with pytest.raises(ProviderError, match="auth failed"):
        await LiteLLMProvider("anthropic", "m").complete(system="s", prompt="p")
    canned["resp"] = RuntimeError("BudgetExceededError: 402 insufficient credits")
    with pytest.raises(ProviderError, match="insufficient credits"):
        await LiteLLMProvider("openrouter", "m").complete(system="s", prompt="p")


def test_free_and_local_models_cost_zero() -> None:
    """Regression: a :free hosted model (e.g. OpenRouter z-ai/glm-5.2:free) or a local Ollama run
    must report $0 — never the illustrative default price (which showed $0.46 on a free model)."""
    big = (230_000, 89_000)  # the tokens that produced the bogus $0.46
    assert lp._cost_cents(None, *big, provider="openrouter", model="z-ai/glm-5.2:free") == 0
    assert lp._cost_cents(None, *big, provider="ollama", model="qwen2.5-coder:7b") == 0
    assert lp._is_free_model("openrouter", "z-ai/glm-5.2:free") is True
    assert lp._is_free_model("ollama", "anything") is True
    # A real paid model still gets a non-zero cost from the default table when litellm can't price it.
    assert lp._cost_cents(None, *big, provider="groq", model="some-paid-model") > 0
    assert lp._is_free_model("groq", "openai/gpt-oss-120b") is False
