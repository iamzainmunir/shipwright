"""The tracing facade builds the right span tree and attributes (plan 11 §3.2).

Uses OTel's InMemorySpanExporter so the assertions run offline with no collector. The
tracer provider can be installed only once per process, so it is set at import time and the
exporter is cleared before each test.
"""

from __future__ import annotations

import pytest
from foundry_core import tracing
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

_EXPORTER = InMemorySpanExporter()
_PROVIDER = TracerProvider()
_PROVIDER.add_span_processor(SimpleSpanProcessor(_EXPORTER))
trace.set_tracer_provider(_PROVIDER)


@pytest.fixture(autouse=True)
def _clear_spans() -> None:
    _EXPORTER.clear()


def test_span_tree_parenting_and_attributes() -> None:
    with tracing.span("mission.run", {tracing.ATTR_MISSION_KEY: "FND-1"}, root=True) as run_span:
        with tracing.span("step.intake", {tracing.ATTR_PHASE: "intake"}):
            with tracing.span("llm.call", {tracing.GEN_AI_MODEL: "mock-1"}):
                pass
        tracing.set_attributes(run_span, {tracing.ATTR_RESULT: "shipped"})

    spans = {s.name: s for s in _EXPORTER.get_finished_spans()}
    assert set(spans) == {"mission.run", "step.intake", "llm.call"}

    # mission.run is a root; the rest nest under it in order.
    assert spans["mission.run"].parent is None
    assert spans["step.intake"].parent.span_id == spans["mission.run"].context.span_id
    assert spans["llm.call"].parent.span_id == spans["step.intake"].context.span_id

    # attributes (including ones set after the span opened) are recorded.
    assert spans["mission.run"].attributes[tracing.ATTR_MISSION_KEY] == "FND-1"
    assert spans["mission.run"].attributes[tracing.ATTR_RESULT] == "shipped"
    assert spans["step.intake"].attributes[tracing.ATTR_PHASE] == "intake"


def test_none_attributes_are_skipped() -> None:
    with tracing.span("step.spec", {tracing.ATTR_PHASE: "spec", tracing.ATTR_AGENT_ROLE: None}):
        pass
    span = _EXPORTER.get_finished_spans()[0]
    assert span.attributes[tracing.ATTR_PHASE] == "spec"
    assert tracing.ATTR_AGENT_ROLE not in span.attributes


def test_record_error_marks_span_failed() -> None:
    with tracing.span("mission.run", root=True) as span:
        tracing.record_error(span, ValueError("boom"))
    finished = _EXPORTER.get_finished_spans()[0]
    assert finished.status.status_code.name == "ERROR"
    assert any(e.name == "exception" for e in finished.events)
