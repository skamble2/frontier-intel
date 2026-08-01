"""Claim repair: the verification loop acting on its own `partial` findings.

The properties worth locking down are the destructive ones. Repair rewrites a
column that clustering and the story rule read, so the tests pin exactly what
it may touch (insights.claim, that insight's claim_check) and what it must
never touch (evidence, other verdicts, claims whose reply was unusable). The
audit-trail test exists because an edit that erases its own history is the one
failure mode this repo's design explicitly forbids.
"""
import contextlib
import io
import unittest

from fli.validation.entailment import _parse_repair, repair_all
from tests.helpers import DBTestCase

VERIFY_MODEL = "claude-haiku-4-5-20251001"


class FakeLLM:
    """Scripted replies: one per 'repair' call, one per 'verify' call."""

    def __init__(self, repair_replies, verify_replies=()):
        self.repair = list(repair_replies)
        self.verify = list(verify_replies)

    def call(self, task, system, user, **kw):
        return (self.repair if task == "repair" else self.verify).pop(0)


class TestRepair(DBTestCase):
    def setUp(self):
        super().setUp()
        c = self.conn
        c.execute("INSERT INTO sources (source_type,name,url) VALUES ('blog','s','u')")
        c.execute("INSERT INTO raw_documents (source_id,source_type,url,content_hash,"
                  "raw_content,retrieved_at) VALUES (1,'blog','u','h','x','t')")
        c.execute("INSERT INTO evidence (document_id,locator,verbatim_content,"
                  "verification) VALUES (1,'{}','the lab shipped a model','exact')")
        for i, (claim, verdict) in enumerate([
                ("overclaim with a date the quote lacks", "partial"),
                ("fully supported claim", "entailed"),
                ("second overclaim", "partial")], start=1):
            c.execute("INSERT INTO insights (id,evidence_id,event_type,claim,"
                      "created_at) VALUES (?,1,'release',?,'t')", (i, claim))
            c.execute("INSERT INTO claim_checks (insight_id,model,verdict,reason,"
                      "created_at) VALUES (?,?,?,'gap named','t')",
                      (i, VERIFY_MODEL, verdict))
        c.commit()

    def _repair(self, llm, **kw):
        with contextlib.redirect_stdout(io.StringIO()):
            return repair_all(self.conn, llm, verify_model=VERIFY_MODEL, **kw)

    def _row(self, iid):
        return self.conn.execute(
            "SELECT i.claim, c.verdict FROM insights i JOIN claim_checks c"
            " ON c.insight_id=i.id AND c.model=? WHERE i.id=?",
            (VERIFY_MODEL, iid)).fetchone()

    def test_repaired_claim_is_written_and_reverified_in_the_same_pass(self):
        llm = FakeLLM(
            repair_replies=['{"claim": "the lab shipped a model", "reason": "ok"}'] * 2,
            verify_replies=['{"verdict": "entailed", "reason": "matches"}'] * 2)
        counts = self._repair(llm)
        self.assertEqual(counts["entailed"], 2)
        for iid in (1, 3):
            row = self._row(iid)
            self.assertEqual(row["claim"], "the lab shipped a model")
            self.assertEqual(row["verdict"], "entailed")

    def test_entailed_rows_are_never_in_the_queue(self):
        """The convergence guarantee: repair only ever sees `partial` rows,
        so a claim that reached `entailed` cannot be rewritten again."""
        llm = FakeLLM(repair_replies=['{"claim": null, "reason": "x"}'] * 2)
        self._repair(llm)
        row = self._row(2)
        self.assertEqual(row["claim"], "fully supported claim")
        self.assertEqual(row["verdict"], "entailed")

    def test_unrepairable_claim_is_left_untouched(self):
        llm = FakeLLM(repair_replies=['{"claim": null, "reason": "quote too thin"}'] * 2)
        counts = self._repair(llm)
        self.assertEqual(counts["unrepairable"], 2)
        row = self._row(1)
        self.assertEqual(row["claim"], "overclaim with a date the quote lacks")
        self.assertEqual(row["verdict"], "partial")

    def test_unparsed_reply_skips_the_row_and_keeps_the_old_verdict(self):
        llm = FakeLLM(repair_replies=["no json here"] * 2)
        counts = self._repair(llm)
        self.assertEqual(counts["unparsed"], 2)
        self.assertEqual(self._row(1)["verdict"], "partial")

    def test_old_claim_survives_in_the_rejections_log(self):
        llm = FakeLLM(
            repair_replies=['{"claim": "the lab shipped a model", "reason": "ok"}'],
            verify_replies=['{"verdict": "entailed", "reason": "matches"}'])
        self._repair(llm, limit=1)
        logged = self.conn.execute(
            "SELECT detail FROM rejections WHERE reason='claim_rewritten'").fetchall()
        self.assertEqual(len(logged), 1)
        self.assertIn("overclaim with a date the quote lacks", logged[0]["detail"])

    def test_unparsed_reverify_downgrades_honestly_to_partial(self):
        """A rewritten claim whose fresh verdict could not be read must not
        keep the old verdict (it describes text that no longer exists) and
        must not be assumed entailed."""
        llm = FakeLLM(
            repair_replies=['{"claim": "the lab shipped a model", "reason": "ok"}'],
            verify_replies=["garbled"])
        self._repair(llm, limit=1)
        self.assertEqual(self._row(1)["verdict"], "partial")


class TestParseRepair(unittest.TestCase):
    def test_valid_rewrite(self):
        self.assertEqual(_parse_repair('{"claim": " tight claim ", "reason": "r"}'),
                         ("tight claim", "r"))

    def test_null_claim_is_a_valid_unrepairable_answer(self):
        self.assertEqual(_parse_repair('{"claim": null, "reason": "thin"}'),
                         (None, "thin"))

    def test_empty_string_claim_is_rejected_not_written(self):
        self.assertIsNone(_parse_repair('{"claim": "  ", "reason": "r"}'))

    def test_missing_claim_key_is_unparsed(self):
        self.assertIsNone(_parse_repair('{"reason": "r"}'))


if __name__ == "__main__":
    unittest.main()
