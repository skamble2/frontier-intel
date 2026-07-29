"""Inter-labeler agreement, and the provider routing that makes it possible.

Why kappa rather than raw agreement: r4 forces a binary verdict, so two
labelers that both answer 'a' most of the time agree at a high rate while
sharing no judgement at all. Chance correction is the whole point, and the
degenerate case below is the one that motivated it.
"""
import unittest

from fli.intelligence.judge import agreement
from tests.helpers import memory_db


def _db(votes_a, votes_b):
    conn = memory_db()
    conn.execute("INSERT INTO sources (source_type,name,url) VALUES ('blog','s','u')")
    conn.execute("INSERT INTO raw_documents (source_id,source_type,url,content_hash,"
                 "raw_content,retrieved_at) VALUES (1,'blog','u','h','x','t')")
    conn.execute("INSERT INTO evidence (document_id,locator,verbatim_content,"
                 "verification) VALUES (1,'{}','x','exact')")
    for i in range(len(votes_a) * 2 + 2):
        conn.execute("INSERT INTO insights (evidence_id,event_type,claim,created_at)"
                     " VALUES (1,'release',?,'t')", (f"c{i}",))
    for lab, votes in (("llm:A/r4", votes_a), ("llm:B/r4", votes_b)):
        for i, w in enumerate(votes):
            conn.execute("INSERT INTO pairwise_labels (event_a,event_b,winner,"
                         "labeler,labeled_at) VALUES (?,?,?,?,'t')", (i + 1, i + 2, w, lab))
    conn.commit()
    return conn


class TestCohensKappa(unittest.TestCase):
    def test_perfect_agreement_with_variance_is_one(self):
        r = agreement(_db("aabb", "aabb"), "llm:A/r4", "llm:B/r4")
        self.assertAlmostEqual(r["kappa"], 1.0)

    def test_total_disagreement_is_minus_one(self):
        r = agreement(_db("aabb", "bbaa"), "llm:A/r4", "llm:B/r4")
        self.assertAlmostEqual(r["kappa"], -1.0)

    def test_unanimous_labelers_give_undefined_kappa_not_a_perfect_score(self):
        """THE CASE THIS EXISTS FOR. Two labelers that always answer 'a' agree
        100% of the time and have told us nothing. Reporting 1.0 here would be
        the same mistake that made the Dawid-Skene estimate an artifact when
        three prompt variants of one model agreed 92-100%."""
        r = agreement(_db("aaaa", "aaaa"), "llm:A/r4", "llm:B/r4")
        self.assertEqual(r["observed"], 1.0)
        self.assertNotEqual(r["kappa"], r["kappa"])      # NaN

    def test_high_raw_agreement_is_discounted_when_skewed(self):
        r = agreement(_db("aaaaaaab", "aaaaaabb"), "llm:A/r4", "llm:B/r4")
        self.assertAlmostEqual(r["observed"], 0.875)
        self.assertLess(r["kappa"], 0.7)                 # chance-corrected

    def test_no_overlap_returns_empty_rather_than_a_number(self):
        conn = _db("aabb", "aabb")
        conn.execute("DELETE FROM pairwise_labels WHERE labeler='llm:B/r4'")
        conn.commit()
        self.assertEqual(agreement(conn, "llm:A/r4", "llm:B/r4"), {})


class TestProviderRouting(unittest.TestCase):
    """Only the refusal behaviour is tested; the routing table's contents are
    config, and a test that restates them just breaks on every addition."""

    def test_unknown_model_refuses_rather_than_guessing(self):
        """Guessing would send an API key to the wrong endpoint."""
        from fli.ops.llm import provider_for
        with self.assertRaises(ValueError):
            provider_for("llama-3-70b")

    def test_unpriced_model_refuses_to_invent_a_rate(self):
        """Token cost is a reported figure. A guessed price is worse than a
        missing one: it is a confident number nobody checked."""
        from fli.ops.llm import cost_usd
        with self.assertRaises(KeyError):
            cost_usd("gpt-not-in-the-table", 1000, 1000)


if __name__ == "__main__":
    unittest.main()
