"""OpenTelemetry setup and helpers, shared by the Python services (plan 11 §3).

Tracing follows Shipwright's "works offline by default, real backend is a drop-in" design:

  * :func:`init_telemetry` is a no-op unless tracing is switched on (``FOUNDRY_TRACING``
    truthy, or an ``OTEL_EXPORTER_OTLP_ENDPOINT`` is configured).
  * With no endpoint it prints the span tree to **stdout** (``ConsoleSpanExporter``) so a
    developer sees traces with zero infrastructure; set ``OTEL_EXPORTER_OTLP_ENDPOINT`` to
    ship OTLP/HTTP to a Collector instead — no code change.

The :func:`span` helper is **always safe to call**: when tracing is off, OTel hands back a
non-recording span whose ``set_attribute`` is a cheap no-op, so call sites never branch on
"is tracing enabled?". Span names and ``foundry.*`` / ``gen_ai.*`` attribute keys follow the
Canon §8 conventions catalogued in plan 11 §3.2.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanProcessor,
)
from opentelemetry.trace import Span, Status, StatusCode

# ---- foundry.* / gen_ai.* attribute keys (plan 11 §3.2) -------------------------------
ATTR_RUN_ID = "foundry.run_id"
ATTR_MISSION_KEY = "foundry.mission_key"
ATTR_AUTONOMY = "foundry.autonomy"
ATTR_MISSION_SOURCE = "foundry.mission_source"
ATTR_RESULT = "foundry.result"  # shipped | blocked | failed | cancelled
ATTR_PHASE = "foundry.phase"
ATTR_STEP_STATUS = "foundry.status"
ATTR_AGENT_ROLE = "foundry.agent_role"
ATTR_TURN_INDEX = "foundry.turn_index"
ATTR_STOP_REASON = "foundry.stop_reason"
ATTR_TOOL_NAME = "foundry.tool_name"
ATTR_TOOL_OK = "foundry.tool_ok"
ATTR_GATE = "foundry.gate"
ATTR_BLOCKER_KIND = "foundry.blocker_kind"
ATTR_WAIT_SECONDS = "foundry.wait_seconds"
ATTR_COST_CENTS = "foundry.cost_cents"

GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_MODEL = "gen_ai.request.model"
GEN_AI_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"

_TRACER_NAME = "shipwright"
_initialized = False


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def tracing_enabled() -> bool:
    """True when the operator asked for traces (explicit flag or a configured OTLP endpoint)."""
    return _truthy(os.environ.get("FOUNDRY_TRACING")) or bool(
        os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    )


def _make_processor() -> SpanProcessor:
    """OTLP/HTTP to a Collector when an endpoint is set, else pretty spans to stdout."""
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        return BatchSpanProcessor(OTLPSpanExporter())  # reads OTEL_* env for endpoint/headers
    return SimpleSpanProcessor(ConsoleSpanExporter())


def init_telemetry(service: str) -> None:
    """Install the global tracer provider once. No-op if tracing is off or already installed."""
    global _initialized
    if _initialized or not tracing_enabled():
        return
    resource = Resource.create(
        {
            "service.name": f"shipwright-{service}",
            "service.namespace": "shipwright",
            "service.version": os.environ.get("GIT_SHA", "dev"),
            "deployment.environment": os.environ.get("FOUNDRY_ENV", "local"),
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(_make_processor())
    trace.set_tracer_provider(provider)
    _initialized = True


@contextmanager
def span(
    name: str, attributes: Mapping[str, object] | None = None, *, root: bool = False
) -> Iterator[Span]:
    """Start a current span; yields it so callers can set more attributes as work completes.

    Safe when tracing is disabled (non-recording span, cheap no-ops). ``None`` attribute
    values are skipped so callers can pass optional fields without guarding each one. Pass
    ``root=True`` to start a new trace regardless of ambient context — used for
    ``mission.run``, which outlives the HTTP request that kicks it off.
    """
    tracer = trace.get_tracer(_TRACER_NAME)
    parent = Context() if root else None  # empty context => no parent => new root trace
    with tracer.start_as_current_span(name, context=parent) as current:
        _apply(current, attributes)
        yield current


def _apply(current: Span, attributes: Mapping[str, object] | None) -> None:
    if not attributes:
        return
    for key, value in attributes.items():
        if value is not None:
            current.set_attribute(key, value)


def set_attributes(current: Span, attributes: Mapping[str, object]) -> None:
    """Set several attributes on an in-flight span, skipping ``None`` values."""
    _apply(current, attributes)


def record_error(current: Span, exc: BaseException) -> None:
    """Mark a span as failed and attach the exception (plan 11 §3.2 error traces)."""
    current.set_status(Status(StatusCode.ERROR, str(exc)))
    current.record_exception(exc)
