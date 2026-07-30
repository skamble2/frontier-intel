"""fli.knowledge.filtering - LAYER 2 stage-1 deterministic filter.

Three behaviours: the pass/reject decision, near-duplicate suppression, and the
regression that keeps re-versions of one URL from suppressing themselves.
"""
from fli import storage
from fli.knowledge.filtering import stage1, suppress_near_dups
from tests.helpers import DBTestCase


class TestStage1Decision(DBTestCase):
    """Per-source-type length floors, the releases.atom exemption, and the
    lab-authorship requirement for papers."""

    def setUp(self):
        super().setUp()
        self.conn.execute("INSERT INTO labs (name) VALUES ('OpenAI')")

    def _doc(self, source_type, source_url, content, published=None, url="http://d"):
        sid = storage.upsert_source(self.conn, source_type, "s", source_url)
        doc_id, _ = storage.store_document(self.conn, sid, source_type, url,
                                           content, published)
        return doc_id

    def test_decisions(self):
        cases = [
            ("short blog naming a tracked lab passes",
             "blog", "http://feed",
             "OpenAI ships a new model. " * 10,
             (True, None)),

            # mirrors the measured case: DeepSeek-V3 v1.0.0 was 164 chars
            ("release tag passes the lower floor",
             "github", "http://gh/releases.atom",
             "v1.0.0\nhttp://gh/r/v1\n2026-07-01\n\n"
             "OpenAI model release with brief notes. " * 3,
             (True, None)),

            ("short commit is rejected",
             "github", "http://gh/commits/main.atom",
             "fix: typo in OpenAI docs",
             (False, "too_short")),

            ("third-party paper about a lab is rejected",
             "arxiv", "http://ax/q1",
             "Benchmarking OpenAI models\nhttp://ax/1\n2026-07-01\n"
             "authors: Jane Scholar; John Reviewer\n\n"
             "We evaluate OpenAI models on our benchmark. " * 5,
             (False, "not_lab_authored")),

            # collective author strings ("DeepSeek-AI") are the lab itself
            ("lab-authored paper passes",
             "arxiv", "http://ax/q2",
             "DeepSeek-V4 Technical Report\nhttp://ax/2\n2026-07-01\n"
             "authors: DeepSeek-AI; Some Person\n\n"
             "We present DeepSeek-V4, exceeding OpenAI models. " * 5,
             (True, None)),
        ]
        for name, stype, surl, content, expected in cases:
            with self.subTest(name):
                doc = self._doc(stype, surl, content, url=f"http://d/{name[:6]}")
                self.assertEqual(expected, stage1(self.conn, doc))

    def test_near_duplicate_suppression_keeps_the_earliest(self):
        a = self._doc("blog", "http://f1",
                      "Gemini for Science\nhttp://a\n\n"
                      + "OpenAI rival Gemini for science announced. " * 5,
                      "2026-07-20", url="http://d/a")
        b = self._doc("blog", "http://f2",
                      "Gemini for Science\nhttp://b\n\n"
                      + "Google announces Gemini for science with OpenAI comparisons. " * 5,
                      "2026-07-21", url="http://d/b")
        stage1(self.conn, a)
        stage1(self.conn, b)
        self.assertEqual(1, suppress_near_dups(self.conn))
        rej = self.conn.execute("SELECT document_id FROM rejections"
                                " WHERE reason='near_duplicate'").fetchall()
        self.assertEqual([b], [r["document_id"] for r in rej])
        self.assertEqual(0, suppress_near_dups(self.conn))      # idempotent

    def test_reversions_of_one_url_do_not_suppress_themselves(self):
        """REGRESSION: dedup ran over raw_documents, so a teaser upgraded to a
        full body looked like two near-identical posts and the newer one was
        suppressed. Fixed by running over the latest_documents view."""
        sid = storage.upsert_source(self.conn, "blog", "s", "http://feed")
        for body in ("teaser about OpenAI", "full body OpenAI " * 8):
            storage.store_document(self.conn, sid, "blog", "http://post",
                                   "Gemini Omni\nhttp://post\n\n" + body, "2026-07-20")
        self.assertEqual(0, suppress_near_dups(self.conn))


if __name__ == "__main__":
    import unittest
    unittest.main()
