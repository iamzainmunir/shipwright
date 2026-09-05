"""Cross-provider failover.

A build/reasoning call must try the next model when one is down (rate-limited, out of credits, etc.)
and only error when EVERY connected model is exhausted — so one provider's outage (e.g. OpenRouter 402)
doesn't sink the run while other providers are connected.
"""

from __future__ import annotations

from app.providers import FailoverProvider, ProviderError
from app.providers.base import AgentTurn, LLMResult


class _Boom:
    name = "boom"

    def __init__(self, model: str) -> None:
        self.model = model

    async def complete(self, **_kw) -> LLMResult:
        raise ProviderError(f"{self.model} down")

    async def complete_tools(self, **_kw) -> AgentTurn:
        raise ProviderError(f"{self.model} down")


class _OK:
    name = "ok"

    def __init__(self, model: str) -> None:
        self.model = model

    async def complete(self, **_kw) -> LLMResult:
        return LLMResult(text="hi", model=self.model, tokens_in=1, tokens_out=1, cost_cents=0)

    async def complete_tools(self, **_kw) -> AgentTurn:
        return AgentTurn("done", [], 1, 1, 0)


async def test_failover_skips_failing_providers_and_reports_model() -> None:
    fp = FailoverProvider([_Boom("a"), _Boom("b"), _OK("c")])
    r = await fp.complete(system="s", prompt="p")
    assert r.text == "hi"
    assert fp.model == "c"  # reflects the model that actually served


async def test_failover_tools_path() -> None:
    fp = FailoverProvider([_Boom("a"), _OK("c")])
    t = await fp.complete_tools(system="s", messages=[], tools=[])
    assert t.text == "done"


async def test_failover_raises_only_when_all_exhausted() -> None:
    fp = FailoverProvider([_Boom("a"), _Boom("b")])
    raised = False
    try:
        await fp.complete(system="s", prompt="p")
    except ProviderError:
        raised = True
    assert raised


async def test_failover_calls_hook_on_each_hop() -> None:
    hops: list[str] = []

    async def on_failover(model: str, _exc: Exception) -> None:
        hops.append(model)

    fp = FailoverProvider([_Boom("a"), _Boom("b"), _OK("c")], on_failover=on_failover)
    await fp.complete(system="s", prompt="p")
    assert hops == ["a", "b"]  # a hop is reported for each failed provider, not the one that succeeded


def test_provider_chain_is_limited_to_the_agents_selected_models() -> None:
    # POLICY: failover only spans the models the user SELECTED on THAT agent — never other connected
    # models. One selected model → a chain of one → no failover (it errors if that model is down).
    from types import SimpleNamespace as NS

    from app.engine import RunEngine
    from foundry_core.enums import ConnectionStatus

    conns = [
        NS(status=ConnectionStatus.CONNECTED, provider="openrouter", models=["m1", "m2"],
           endpoint=None, config={"apiKey": "k"}),
        NS(status=ConnectionStatus.CONNECTED, provider="groq", models=["g1"],
           endpoint=None, config={"apiKey": "k"}),
    ]
    # One selected model → chain of exactly that one (no borrowing m2 or g1).
    one = NS(model_binding="m1", models=["m1"])
    assert [p.model for p in RunEngine._provider_chain(one, conns)] == ["m1"]
    # Two selected → both, selected/binding first, in the agent's order — still nothing else.
    two = NS(model_binding="m2", models=["m2", "m1"])
    assert [p.model for p in RunEngine._provider_chain(two, conns)] == ["m2", "m1"]
