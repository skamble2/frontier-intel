"""The MCP surface must expose exactly what the reader surfaces already show.

The tool bodies are plain functions (slate/search/drift_status/latest_digest)
tested directly against the real schema — no SDK required. The SDK wiring
gets one smoke test (skipped when mcp isn't installed): the four read-only
tools are registered under their public names.
"""
import asyncio
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fli.delivery import mcp_server
from tests.helpers import memory_db

try:
    import mcp  # noqa: F401
    HAVE_MCP = True
except ImportError:
    HAVE_MCP = False


def _seed(conn):
    """Three scored insights at 2/3/200 days old, one lab each."""
    conn.execute("INSERT INTO sources (source_type,name,url) VALUES ('blog','s','u')")
    for i, ago in enumerate((2, 3, 200), 1):
        pub = (dt.date.today() - dt.timedelta(days=ago)).isoformat()
        conn.execute("INSERT INTO labs (id,name) VALUES (?,?)", (i, f"Lab {i}"))
        conn.execute("INSERT INTO raw_documents (id,source_id,source_type,url,"
                     "content_hash,raw_content,retrieved_at,published_at)"
                     " VALUES (?,1,'blog',?,?,'x','t',?)",
                     (i, f"https://lab.example/{i}", f"h{i}", pub))
        conn.execute("INSERT INTO evidence (id,document_id,locator,"
                     "verbatim_content,verification) VALUES (?,?,'{}',?,'exact')",
                     (i, i, f"verbatim quote {i} copied word for word from the "
                            f"stored source document"))
        conn.execute("INSERT INTO insights (id,evidence_id,attributed_lab_id,"
                     "event_type,claim,score,created_at)"
                     " VALUES (?,?,?,'release',?,?,'t')",
                     (i, i, i, f"claim about topic {i}", 5.0 - i))
        conn.execute("INSERT INTO event_scores (event_id,model,score,rank,"
                     "components,created_at,policy_version) VALUES"
                     " (?,?,?,?,'{\"winner\": true}','t',3)",
                     (i, "investment:logistic", 5.0 - i, i))
    conn.commit()


class MCPTestCase(unittest.TestCase):
    def setUp(self):
        self.conn = memory_db()
        _seed(self.conn)

    def tearDown(self):
        self.conn.close()


class TestSearch(MCPTestCase):
    def test_matches_claims_and_quotes_best_score_first(self):
        by_claim = mcp_server.search(self.conn, "topic 2")
        self.assertEqual([r["id"] for r in by_claim], [2])
        by_quote = mcp_server.search(self.conn, "verbatim quote")
        self.assertEqual([r["id"] for r in by_quote], [1, 2, 3])  # score desc

    def test_k_caps_and_rows_carry_only_the_wire_fields(self):
        rows = mcp_server.search(self.conn, "claim", k=2)
        self.assertEqual(len(rows), 2)
        self.assertEqual(set(rows[0]), set(mcp_server._FIELDS))
        self.assertEqual(rows[0]["lab"], "Lab 1")
        self.assertEqual(rows[0]["quote"], "verbatim quote 1 copied word for "
                                           "word from the stored source document")

    def test_no_match_is_an_empty_list_not_an_error(self):
        self.assertEqual(mcp_server.search(self.conn, "zzz-nothing"), [])


class TestSlate(MCPTestCase):
    def test_slate_is_the_rubric_ranking_windowed_and_slim(self):
        rows = mcp_server.slate(self.conn, k=10)
        # the 200-day-old item falls outside the policy window
        self.assertEqual([r["claim"] for r in rows],
                         ["claim about topic 1", "claim about topic 2"])
        self.assertEqual(set(rows[0]), set(mcp_server._FIELDS))

    def test_empty_scores_means_empty_slate(self):
        self.conn.execute("DELETE FROM event_scores")
        self.assertEqual(mcp_server.slate(self.conn), [])


class TestDriftStatus(MCPTestCase):
    def test_delegates_to_drift_build_rows(self):
        rows = mcp_server.drift_status(self.conn, days=14)
        # 2/3-day docs are current, the 200-day doc is the reference
        self.assertTrue(rows)
        self.assertIn("doc source_type mix", [r["metric"] for r in rows])
        self.assertTrue(all(r["verdict"] for r in rows))


class TestLatestDigest(unittest.TestCase):
    def test_newest_file_wins_and_persona_filters(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "2026-07-30-ai_team.md").write_text("old team", encoding="utf-8")
            (d / "2026-08-01-ai_team.md").write_text("new team", encoding="utf-8")
            (d / "2026-08-01-investment.md").write_text("invest", encoding="utf-8")
            with mock.patch.object(mcp_server, "DIGEST_DIR", d):
                any_newest = mcp_server.latest_digest()
                self.assertEqual(any_newest["file"], "2026-08-01-investment.md")
                team = mcp_server.latest_digest("ai_team")
                self.assertEqual(team["content"], "new team")
                self.assertIn("error", mcp_server.latest_digest("nope"))


@unittest.skipUnless(HAVE_MCP, "mcp SDK not installed")
class TestServerWiring(unittest.TestCase):
    def test_the_four_readonly_tools_are_registered(self):
        app = mcp_server.build_server()
        names = {t.name for t in asyncio.run(app.list_tools())}
        self.assertEqual(names, {"top_insights", "search_insights",
                                 "corpus_drift", "get_latest_digest"})
