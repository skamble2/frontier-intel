"""Optional OpenInference tracing for the single LLM choke point (llm.py)."""
from __future__ import annotations

import os
from contextlib import contextmanager

SPAN_KIND = "openinference.span.kind"
LLM_MODEL = "llm.model_name"
LLM_PROVIDER = "llm.provider"
LLM_SYSTEM = "llm.system"
INPUT_VALUE = "input.value"
OUTPUT_VALUE = "output.value"
TOKENS_PROMPT = "llm.token_count.prompt"
TOKENS_COMPLETION = "llm.token_count.completion"
TOKENS_TOTAL = "llm.token_count.total"

_tracer = None


def enabled() -> bool:
    return os.environ.get("FLI_TRACING", "").lower() in ("1", "true", "yes")


def _endpoint() -> str:
    base = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006").rstrip("/")
    return f"{base}/v1/traces"


def setup(project_name: str = "frontier-intel", exporter=None) -> bool:
    """Configure a tracer that exports to Phoenix. """
    global _tracer
    if _tracer is not None:
        return True
    if exporter is None and not enabled():
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        if enabled():
            print("FLI_TRACING set but opentelemetry is not installed in this env"
                  " — tracing OFF. Fix: pip install -r requirements-tracing.txt")
        return False
    if exporter is None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        exporter = OTLPSpanExporter(endpoint=_endpoint())
    provider = TracerProvider(resource=Resource.create({"service.name": project_name}))
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("fli")
    return True


@contextmanager
def llm_span(task: str):
    """Span for one LLM.call. """
    if _tracer is None:
        yield None
        return
    with _tracer.start_as_current_span(f"llm.{task}") as span:
        span.set_attribute(SPAN_KIND, "LLM")
        span.set_attribute("fli.task", task)
        yield span


def annotate(span, attrs: dict) -> None:
    """Set attributes on a span; no-op when span is None (tracing off)."""
    if span is None:
        return
    for k, v in attrs.items():
        if v is not None:
            span.set_attribute(k, v)


def input_attrs(model: str, system: str, user: str) -> dict:
    return {LLM_MODEL: model, LLM_PROVIDER: "anthropic", LLM_SYSTEM: "anthropic",
            INPUT_VALUE: user,
            "llm.input_messages.0.message.role": "system",
            "llm.input_messages.0.message.content": system,
            "llm.input_messages.1.message.role": "user",
            "llm.input_messages.1.message.content": user}


def output_attrs(text: str, input_tokens: int, output_tokens: int) -> dict:
    return {OUTPUT_VALUE: text, TOKENS_PROMPT: input_tokens,
            TOKENS_COMPLETION: output_tokens,
            TOKENS_TOTAL: input_tokens + output_tokens,
            "llm.output_messages.0.message.role": "assistant",
            "llm.output_messages.0.message.content": text}
