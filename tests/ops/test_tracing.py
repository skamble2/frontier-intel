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

    def test_spans_are_noop_when_tracing_off(self):
        with tracing.chain_span("node.ingest") as c, tracing.llm_span("x") as l:
            self.assertIsNone(c)
            self.assertIsNone(l)


@unittest.skipUnless(_OTEL, "opentelemetry not installed (optional tracing extras)")
class TestTracingEnabled(unittest.TestCase):
    """Tracing on: spans carry OpenInference attributes and nest correctly.
    OTel allows set_tracer_provider once per process, so one exporter is shared
    by the class and cleared between tests."""

    @classmethod
    def setUpClass(cls):
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter)
        cls.exporter = InMemorySpanExporter()
        assert tracing.setup(exporter=cls.exporter)

    @classmethod
    def tearDownClass(cls):
        tracing._tracer = None

    def setUp(self):
        self.exporter.clear()

    def test_llm_span_emits_openinference_span(self):
        from opentelemetry import trace
        exporter = self.exporter
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

    def test_llm_span_nests_under_chain_span(self):
        from opentelemetry import trace
        with tracing.chain_span("node.extract") as parent:
            tracing.annotate(parent, {tracing.OUTPUT_VALUE: "5 insights"})
            with tracing.llm_span("extract"):
                pass
        trace.get_tracer_provider().force_flush()
        spans = {s.name: s for s in self.exporter.get_finished_spans()}
        chain, llm = spans["node.extract"], spans["llm.extract"]
        self.assertEqual(chain.attributes[tracing.SPAN_KIND], "CHAIN")
        self.assertEqual(chain.attributes[tracing.OUTPUT_VALUE], "5 insights")
        self.assertEqual(llm.parent.span_id, chain.context.span_id)
