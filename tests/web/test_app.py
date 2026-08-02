"""The web surface's one write path, and the verdicts it must display.

The read-only pages are thin renders of calls other tests already cover
(top_events, register queries), so the tests here focus on what is new and
riskiest: the candidate-review POST goes through the same `approval.review`
as the CLI (mocked here — the real thing writes register_overrides.yml, a
repo file no test may touch), invalid decisions 404, and entailment verdicts
actually reach the page a reviewer reads.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fli import storage
from fli.web.app import create_app


class WebTestCase(unittest.TestCase):
    def setUp(self):
        # Routes open and CLOSE a connection per request, so the test DB must
        # survive closes: a temp file, not :memory:.
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "web-test.db"
        conn = storage.connect(self.db_path)
        storage.init_db(conn)
        self._seed(conn)
        conn.commit()
        conn.close()
        # fli.web.app's `storage` IS the fli.storage module, so bind the real
        # connect before patching or the lambda calls itself.
        real_connect = storage.connect
        patcher = mock.patch("fli.web.app.storage.connect",
                             lambda *a, **k: real_connect(self.db_path))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)
        self.client = create_app().test_client()

    def _seed(self, conn):
        conn.execute("INSERT INTO labs (id,name) VALUES (1,'DeepMind')")
        conn.execute("INSERT INTO sources (id,source_type,name,url,lab_id)"
                     " VALUES (1,'blog','s','u',1)")
        conn.execute("INSERT INTO raw_documents (id,source_id,source_type,url,"
                     "content_hash,raw_content,retrieved_at,published_at)"
                     " VALUES (1,1,'blog','u','h','x','t','2026-08-01')")
        conn.execute("INSERT INTO evidence (id,document_id,locator,"
                     "verbatim_content,verification)"
                     " VALUES (1,1,'{}','the lab shipped a new model to every "
                     "customer in general availability','exact')")
        conn.execute("INSERT INTO insights (id,evidence_id,attributed_lab_id,"
                     "event_type,claim,score,created_at)"
                     " VALUES (7,1,1,'release','a claim',0.9,'t')")
        conn.execute("INSERT INTO claim_checks (insight_id,model,verdict,reason,"
                     "created_at) VALUES (7,'claude-haiku-4-5-20251001',"
                     "'partial','missing date','t')")


class TestCandidateReview(WebTestCase):
    def _seed(self, conn):
        super()._seed(conn)
        conn.execute(
            "INSERT INTO person_candidates (id,name,discovered_via,paper_count,"
            "entry_ids,seed_person_ids,seed_lab_ids,evidence_id,status,"
            "created_at) VALUES (42,'Ada Lovelace','coauthor_expansion',3,"
            "'[]','[]','[1]',1,'pending','t')")

    def test_register_page_shows_the_queue_with_action_forms(self):
        html = self.client.get("/register").get_data(as_text=True)
        self.assertIn("Ada Lovelace", html)
        self.assertIn("/register/candidates/42/approve", html)
        self.assertIn("/register/candidates/42/reject", html)

    def test_approve_goes_through_the_cli_review_path_and_redirects(self):
        with mock.patch("fli.knowledge.register.approval.review") as rev:
            resp = self.client.post("/register/candidates/42/approve")
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.location.endswith("/register"))
        (_, ids, verdict), _ = rev.call_args
        self.assertEqual((ids, verdict), ([42], "approved"))

    def test_reject_maps_to_the_rejected_verdict(self):
        with mock.patch("fli.knowledge.register.approval.review") as rev:
            self.client.post("/register/candidates/42/reject")
        self.assertEqual(rev.call_args[0][2], "rejected")

    def test_unknown_decision_is_a_404_not_a_write(self):
        with mock.patch("fli.knowledge.register.approval.review") as rev:
            resp = self.client.post("/register/candidates/42/promote")
        self.assertEqual(resp.status_code, 404)
        rev.assert_not_called()


class TestVerdictDisplay(WebTestCase):
    def test_insight_detail_shows_verdict_and_named_gap(self):
        html = self.client.get("/insights/7").get_data(as_text=True)
        self.assertIn("v-partial", html)
        self.assertIn("missing date", html)

    def test_get_pages_render(self):
        for path in ("/", "/register", "/config", "/contributors"):
            self.assertEqual(self.client.get(path).status_code, 200, path)


if __name__ == "__main__":
    unittest.main()
