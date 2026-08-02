"""fli.orchestration.graph — topology and gating of the LangGraph packaging.

Every layer function is faked, so this asserts ONLY what the graph owns:
ordering, the spend gates, and verdict propagation. No network, no spend.
"""
import importlib.util
import unittest
from unittest import mock

HAVE_LANGGRAPH = importlib.util.find_spec("langgraph") is not None
if HAVE_LANGGRAPH:
    from fli.orchestration import graph as G

FREE = ["ingest", "stage1", "expand", "register", "extract", "authors",
        "observe", "mobility", "cluster", "features", "drift", "score",
        "evaluate", "positions", "digest", "alerts", "digest_parity", "checks"]
PAID = ["verify", "personas", "faithfulness"]


@unittest.skipUnless(HAVE_LANGGRAPH, "langgraph not installed")
class GraphTests(unittest.TestCase):
    def _run(self, spend, have_key):
        calls = []

        def rec(name, ret="ok"):
            def f(*a, **kw):
                calls.append(name)
                return ret
            return f

        patches = [
            mock.patch.object(G.feeds, "ingest_all", rec("ingest")),
            mock.patch.object(G.filter1, "stage1_all",
                              rec("stage1", {"checked": 0, "passed": 0,
                                             "rejected": {}})),
            mock.patch.object(G.filter1, "suppress_near_dups", lambda c: 0),
            mock.patch.object(G.expand, "expand_coauthors", rec("expand")),
            mock.patch.object(G.register, "auto_approve", rec("register")),
            mock.patch.object(G.extraction, "extract_all", rec("extract")),
            mock.patch.object(G.extraction, "report_measurements", lambda c: None),
            mock.patch.object(G.extraction, "backfill_arxiv_authors", rec("authors")),
            mock.patch.object(G.register, "observe", rec("observe")),
            mock.patch.object(G.register, "observe_gh_profiles", lambda c: None),
            mock.patch("fli.ingestion.x_api.bearer_token", lambda: None),
            mock.patch.object(G.register, "detect_mobility_events", rec("mobility")),
            mock.patch.object(G.clustering, "cluster_all", rec("cluster")),
            mock.patch.object(G.featmod, "compute_features", rec("features")),
            mock.patch.object(G.drift, "report", rec("drift", 0)),
            mock.patch.object(G, "available_rubrics", rec("score", [])),
            mock.patch.object(G.entailment, "check_all", rec("verify")),
            mock.patch.object(G.entailment, "repair_all", lambda *a, **k: None),
            mock.patch.object(G.personas, "build", rec("personas")),
            mock.patch.object(G.evaluation, "build", rec("evaluate")),
            mock.patch.object(G.positions, "build", rec("positions")),
            mock.patch.object(G.digest, "write", rec("digest")),
            mock.patch.object(G.alerts, "run", rec("alerts")),
            mock.patch.object(G.faithfulness, "check_digests", rec("digest_parity")),
            mock.patch.object(G.faithfulness, "score_hypotheses", rec("faithfulness")),
            mock.patch.object(G.checks, "run", rec("checks", 3)),
            mock.patch.object(G, "have_api_key", lambda *a: have_key),
            mock.patch.object(G, "LLM", mock.MagicMock()),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        final = G.build(conn=mock.MagicMock()).invoke(
            {"spend": spend, "max_extract": 5, "report": {}, "verdict": 0})
        # digest node calls write once per persona; collapse repeats
        dedup = [c for i, c in enumerate(calls) if i == 0 or calls[i - 1] != c]
        return dedup, final

    def test_default_run_skips_every_paid_stage(self):
        calls, final = self._run(spend=False, have_key=False)
        self.assertEqual(calls, [s for s in FREE if s != "extract"])
        for stage in PAID:
            self.assertNotIn(stage, calls)
        self.assertEqual(final["verdict"], 3)      # checks verdict propagates

    def test_spend_without_a_key_still_skips_paid_stages(self):
        calls, _ = self._run(spend=True, have_key=False)
        for stage in PAID:
            self.assertNotIn(stage, calls)

    def test_spend_run_orders_repair_and_notes_before_delivery(self):
        calls, final = self._run(spend=True, have_key=True)
        for stage in PAID + ["extract"]:
            self.assertIn(stage, calls)
        # the ordering fix this graph exists for: nothing published is stale
        self.assertLess(calls.index("verify"), calls.index("digest"))
        self.assertLess(calls.index("personas"), calls.index("digest"))
        self.assertLess(calls.index("digest_parity"), calls.index("faithfulness"))
        self.assertEqual(calls[-1], "checks")
        # every stage reported, so the run summary is complete
        self.assertEqual(set(final["report"]), set(FREE) | set(PAID))


if __name__ == "__main__":
    unittest.main()
