"""Faithfulness of the delivery layer: the persona-note audit and the digest
parity check. The LLM is scripted; the digest check is deterministic, so its
tests write real files and assert on real misses.
"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from fli.validation.faithfulness import (_parse, check_digests,
                                         score_hypotheses)
from tests.helpers import DBTestCase

MODEL = "claude-haiku-4-5-20251001"


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def call(self, task, system, user, **kw):
        self.calls.append(user)
        return self.replies.pop(0)


class FaithfulnessDB(DBTestCase):
    def setUp(self):
        super().setUp()
        c = self.conn
        c.execute("INSERT INTO sources (source_type,name,url) VALUES ('blog','s','u')")
        c.execute("INSERT INTO raw_documents (source_id,source_type,url,content_hash,"
                  "raw_content,retrieved_at) VALUES (1,'blog','u','h','x','t')")
        c.execute("INSERT INTO evidence (document_id,locator,verbatim_content,"
                  "verification) VALUES (1,'{}','the lab shipped a 7B model','exact')")
        c.execute("INSERT INTO insights (id,evidence_id,event_type,claim,created_at)"
                  " VALUES (1,1,'release','a 7B model shipped','t')")
        c.execute("INSERT INTO hypotheses (id,insight_id,persona,hypothesis,"
                  "direction,reasoning) VALUES "
                  "(1,1,'ai_team','adopt the 7B model','adopt','it is small'),"
                  "(2,1,'investment','no exposure','unclear','nothing held')")
        c.commit()

    def _score(self, llm, **kw):
        with contextlib.redirect_stdout(io.StringIO()):
            return score_hypotheses(self.conn, llm, model=MODEL, **kw)


class TestScoreHypotheses(FaithfulnessDB):
    def test_score_is_supported_over_total_and_detail_names_the_misses(self):
        llm = FakeLLM([
            '{"statements": [{"text": "model is 7B", "supported": true},'
            ' {"text": "it beats GPT-4", "supported": false}]}',
            '{"statements": [{"text": "nothing held", "supported": true}]}'])
        counts = self._score(llm)
        self.assertEqual(counts["scored"], 2)
        self.assertEqual(counts["flagged"], 1)
        row = self.conn.execute(
            "SELECT * FROM hypothesis_checks WHERE hypothesis_id=1").fetchone()
        self.assertEqual((row["supported"], row["total"]), (1, 2))
        self.assertAlmostEqual(row["score"], 0.5)
        self.assertIn("it beats GPT-4", row["detail"])

    def test_fully_supported_note_has_null_detail(self):
        llm = FakeLLM(['{"statements": [{"text": "x", "supported": true}]}'] * 2)
        self._score(llm)
        row = self.conn.execute(
            "SELECT detail FROM hypothesis_checks WHERE hypothesis_id=1").fetchone()
        self.assertIsNone(row["detail"])

    def test_no_factual_assertions_scores_one_with_zero_total(self):
        llm = FakeLLM(['{"statements": []}'] * 2)
        self._score(llm)
        row = self.conn.execute(
            "SELECT score, total FROM hypothesis_checks WHERE hypothesis_id=1"
        ).fetchone()
        self.assertEqual((row["score"], row["total"]), (1.0, 0))

    def test_resumable_second_run_sends_nothing(self):
        self._score(FakeLLM(['{"statements": []}'] * 2))
        llm = FakeLLM([])
        counts = self._score(llm)
        self.assertEqual(llm.calls, [])
        self.assertEqual(counts["scored"], 0)

    def test_unparsed_reply_records_nothing_for_a_later_retry(self):
        llm = FakeLLM(["garbled", '{"statements": []}'])
        counts = self._score(llm)
        self.assertEqual(counts["unparsed"], 1)
        n = self.conn.execute("SELECT count(1) FROM hypothesis_checks").fetchone()[0]
        self.assertEqual(n, 1)

    def test_investment_note_evidence_includes_position_mechanisms(self):
        self.conn.execute("INSERT INTO event_positions (event_id,ticker,"
                          "direction,channel,rationale,evidence_id,"
                          "policy_version,created_at) VALUES (1,'NVDA',"
                          "'unclear','compute_demand','r',1,1,'t')")
        self.conn.commit()
        llm = FakeLLM(['{"statements": []}'] * 2)
        self._score(llm)
        inv_user = next(u for u in llm.calls if "investment" in u)
        self.assertIn("NVDA via compute_demand", inv_user)


class TestParse(unittest.TestCase):
    def test_malformed_statement_entries_are_dropped_not_fatal(self):
        got = _parse('{"statements": [{"text": "a", "supported": true},'
                     ' {"text": "b"}, "junk"]}')
        self.assertEqual(got, [("a", True)])

    def test_no_json_is_none(self):
        self.assertIsNone(_parse("no json here"))


class TestDigestParity(FaithfulnessDB):
    def _check(self, md: str) -> dict:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "digest.md").write_text(md, encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                return check_digests(self.conn, out_dir=Path(d))

    def test_quotes_and_claims_present_in_the_db_pass(self):
        totals = self._check("## 1. a 7B model shipped\n"
                             "> \u201cthe lab shipped a 7B model\u201d\n")
        self.assertEqual(totals["quote_misses"], 0)
        self.assertEqual(totals["claim_misses"], 0)
        self.assertEqual((totals["quotes"], totals["claims"]), (1, 1))

    def test_a_quote_the_db_does_not_hold_is_a_miss(self):
        totals = self._check("> \u201can invented quotation\u201d\n")
        self.assertEqual(totals["quote_misses"], 1)

    def test_a_claim_the_db_does_not_hold_is_a_miss(self):
        totals = self._check("## 3. a claim nobody extracted\n")
        self.assertEqual(totals["claim_misses"], 1)

    def test_renderer_normalisation_is_matched(self):
        """The digest collapses whitespace and truncates to 600 chars before
        printing; the parity check must apply the same transform or every
        long quote would be a false miss."""
        long_quote = "word " * 200                      # > 600 chars stored
        self.conn.execute("INSERT INTO evidence (document_id,locator,"
                          "verbatim_content,verification)"
                          " VALUES (1,'{}',?,'exact')", (long_quote,))
        self.conn.commit()
        rendered = " ".join(long_quote.split())[:600]
        totals = self._check(f"> \u201c{rendered}\u201d\n")
        self.assertEqual(totals["quote_misses"], 0)


if __name__ == "__main__":
    unittest.main()
