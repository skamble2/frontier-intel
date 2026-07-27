"""fli.ops.tracing - LAYER 4 observability. Must be a no-op when disabled."""
import unittest

from fli.ops import tracing

try:
    import opentelemetry  # noqa: F401
    _OTEL = True
except ImportError:  # tracing extras are optional (requirements-tracing.txt)
    _OTEL = False


class TestTracingDisabled(unittest.TestCase):
    """Tracing off: the annotate/attr helpers are pure and never touch OTel."""

    def test_annotate_none_is_noop(self):
        tracing.annotate(None, {"anything": 1})  # must not raise

    def test_attr_builders(self):
        ia = tracing.input_attrs("haiku", "SYS", "doc text")
        self.assertEqual(ia[tracing.LLM_MODEL], "haiku")
        self.assertEqual(ia[tracing.INPUT_VALUE], "doc text")
        self.assertEqual(ia[tracing.LLM_PROVIDER], "anthropic")
        oa = tracing.output_attrs("out", 12, 3)
        self.assertEqual(oa[tracing.OUTPUT_VALUE], "out")
        self.assertEqual(oa[tracing.TOKENS_TOTAL], 15)


@unittest.skipUnless(_OTEL, "opentelemetry not installed (optional tracing extras)")
class TestTracingEnabled(unittest.TestCase):
    """Tracing on: llm_span emits one OpenInference LLM span, task-tagged."""

    def tearDown(self):
        tracing._tracer = None

    def test_llm_span_emits_openinference_span(self):
        from opentelemetry import trace
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter)
        exporter = InMemorySpanExporter()
        self.assertTrue(tracing.setup(exporter=exporter))
        with tracing.llm_span("classify") as span:
            tracing.annotate(span, tracing.input_attrs("haiku", "SYS", "the doc text"))
            tracing.annotate(span, tracing.output_attrs('{"ok":1}', 12, 3))
        trace.get_tracer_provider().force_flush()
        (s,) = exporter.get_finished_spans()
        self.assertEqual(s.name, "llm.classify")
        self.assertEqual(s.attributes[tracing.SPAN_KIND], "LLM")
        self.assertEqual(s.attributes["fli.task"], "classify")
        self.assertEqual(s.attributes[tracing.INPUT_VALUE], "the doc text")
        self.assertEqual(s.attributes[tracing.OUTPUT_VALUE], '{"ok":1}')
        self.assertEqual(s.attributes[tracing.TOKENS_TOTAL], 15)
