"""fli.ingestion.x_api — X as a LAYER 1 source.

X is the only paid source, so the tests that matter are the ones about money and
about attribution. Runs entirely against a fake client: no token, no network,
nothing spent.
"""
import sqlite3
import unittest
from pathlib import Path
from unittest import mock

from fli import storage
from fli.core.config import (MIN_CHARS, X_MAX_POSTS_PER_RUN, X_POST_COST_USD,
                             X_RUN_BUDGET_USD, X_USER_COST_USD)
from fli.ingestion import x_api
from fli.knowledge.filtering import stage1


class TestCostControl(unittest.TestCase):
    """The caps must stop a run BEFORE it spends, not after."""

    def test_budget_guard_refuses_an_over_budget_run(self):
        with self.assertRaises(SystemExit) as cm:
            x_api._budget_guard(n_accounts=10_000, per_account=100)
        self.assertIn("exceeds", str(cm.exception))

    def test_budget_guard_returns_worst_case_for_a_normal_run(self):
        worst = x_api._budget_guard(n_accounts=44, per_account=20)
        # 44 user reads + min(880, cap) post reads
        expected = (44 * X_USER_COST_USD
                    + min(44 * 20, X_MAX_POSTS_PER_RUN) * X_POST_COST_USD)
        self.assertAlmostEqual(expected, worst)
        self.assertLessEqual(worst, X_RUN_BUDGET_USD)

    def test_spend_is_counted_per_resource_returned(self):
        """X bills per resource returned, so the ledger must too."""
        c = x_api.XClient("fake-token")
        c.posts_read, c.users_read = 200, 44
        self.assertAlmostEqual(200 * X_POST_COST_USD + 44 * X_USER_COST_USD,
                               c.spend_usd)


class TestTweetsSurviveStage1(unittest.TestCase):
    """A post is <=280 chars. The default 400-char fallback floor would reject
    every single one as too_short, which is why 'social' needs its own entry."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(Path(storage.SCHEMA_PATH).read_text())
        self.conn.execute("INSERT INTO labs (id, name) VALUES (1, 'OpenAI')")

    def test_social_has_its_own_floor_below_the_post_limit(self):
        self.assertIn("social", MIN_CHARS)
        self.assertLess(MIN_CHARS["social"], 280)

    def test_a_realistic_post_passes_stage1(self):
        sid = storage.upsert_source(self.conn, "social", "@OpenAI",
                                   "https://x.com/OpenAI", 1, "official")
        _url, body = x_api.as_document("OpenAI", {
            "id": "1", "text": "We are rolling out GPT-5.6 to all API customers "
                               "today, with pricing down to $2 per million tokens.",
            "created_at": "2026-07-25T10:00:00Z"})
        doc, _ = storage.store_document(self.conn, sid, "social",
                                        "https://x.com/OpenAI/status/1", body,
                                        "2026-07-25T10:00:00Z")
        self.assertEqual((True, None), stage1(self.conn, doc))

    def test_a_bare_link_post_is_rejected(self):
        sid = storage.upsert_source(self.conn, "social", "@OpenAI",
                                   "https://x.com/OpenAI", 1, "official")
        _url, body = x_api.as_document("OpenAI", {"id": "2", "text": "OpenAI"})
        doc, _ = storage.store_document(self.conn, sid, "social",
                                        "https://x.com/OpenAI/status/2", body, None)
        passed, reason = stage1(self.conn, doc)
        self.assertFalse(passed)
        self.assertEqual("too_short", reason)


class TestAttribution(unittest.TestCase):
    """The rule that protects C12: a person tweeting is not their employer
    announcing, so researcher accounts must carry no lab."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(Path(storage.SCHEMA_PATH).read_text())
        self.conn.execute("INSERT INTO people (id, canonical_name) VALUES (1,'Jane Doe')")
        sid = storage.upsert_source(self.conn, "blog", "p", "http://p")
        doc, _ = storage.store_document(self.conn, sid, "blog", "http://p", "Jane Doe", None)
        ev = storage.insert_evidence(self.conn, doc, "{}", "Jane Doe", "exact", 1.0)
        self.conn.execute(
            "INSERT INTO identities (person_id, platform, handle, confidence_tier,"
            " resolution_method, evidence_id) VALUES (1,'x','janedoe','verbatim',"
            "'self_link',?)", (ev,))

    def test_lab_accounts_are_official_and_researcher_accounts_are_not(self):
        by_handle = {h: (lab, ch) for h, lab, ch in x_api.accounts_to_track(self.conn)}
        self.assertEqual(("OpenAI", "official"), by_handle["OpenAI"])
        # the researcher pulled from identities: no lab, not an official channel
        self.assertEqual((None, "third_party"), by_handle["janedoe"])

    def test_every_lab_account_maps_to_a_tracked_lab_name(self):
        """A typo here would silently create a source with lab_id NULL."""
        from fli.knowledge.register import LABS
        tracked = {name for name, _, _ in LABS}
        for handle, lab in x_api.LAB_ACCOUNTS.items():
            with self.subTest(handle):
                self.assertIn(lab, tracked)


class TestDryRun(unittest.TestCase):
    def test_dry_run_makes_no_request_and_needs_no_token(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(Path(storage.SCHEMA_PATH).read_text())
        with mock.patch("fli.core.http.http_get",
                        side_effect=AssertionError("dry run must not fetch")):
            out = x_api.ingest(conn, dry_run=True)
        self.assertTrue(out["dry_run"])
        self.assertGreater(out["worst_case_usd"], 0)


if __name__ == "__main__":
    unittest.main()
