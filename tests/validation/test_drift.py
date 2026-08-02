"""fli.validation.drift — PSI/KS corpus drift. Pure math + fixed windows."""
import math
import unittest

from fli.validation import drift
from tests.helpers import DBTestCase


class PsiTests(unittest.TestCase):
    def test_identical_distributions_are_zero(self):
        counts = {"blog": 50, "arxiv": 30, "github": 20}
        self.assertAlmostEqual(drift.psi(counts, counts), 0.0)

    def test_psi_matches_hand_computation(self):
        ref = {"a": 80, "b": 20}
        cur = {"a": 50, "b": 50}
        expected = ((0.5 - 0.8) * math.log(0.5 / 0.8)
                    + (0.5 - 0.2) * math.log(0.5 / 0.2))
        self.assertAlmostEqual(drift.psi(ref, cur), expected)

    def test_new_category_registers_as_drift_not_crash(self):
        value = drift.psi({"a": 100}, {"a": 50, "b": 50})
        self.assertGreater(value, drift.PSI_MAJOR)

    def test_bigger_shift_scores_higher(self):
        ref = {"a": 70, "b": 30}
        small = drift.psi(ref, {"a": 65, "b": 35})
        large = drift.psi(ref, {"a": 30, "b": 70})
        self.assertLess(small, drift.PSI_MODERATE)
        self.assertGreater(large, small)


class KsTests(unittest.TestCase):
    def test_identical_samples_are_zero(self):
        xs = [float(i) for i in range(100)]
        stat, crit = drift.ks(xs, list(xs))
        self.assertAlmostEqual(stat, 0.0)
        self.assertLess(stat, crit)

    def test_disjoint_samples_are_maximal(self):
        stat, crit = drift.ks([1.0] * 50, [100.0] * 50)
        self.assertAlmostEqual(stat, 1.0)
        self.assertGreater(stat, crit)

    def test_shifted_sample_exceeds_critical_value(self):
        ref = [float(i) for i in range(200)]
        cur = [float(i) + 150 for i in range(200)]
        stat, crit = drift.ks(ref, cur)
        self.assertGreater(stat, crit)

    def test_empty_side_is_inert(self):
        stat, crit = drift.ks([], [1.0, 2.0])
        self.assertEqual(stat, 0.0)
        self.assertEqual(crit, float("inf"))


class BuildTests(DBTestCase):
    """Windowing against the real schema: anchored to newest doc, not clock."""

    def _seed(self, docs):
        """docs: list of (source_type, published_at, content). Returns ids."""
        self.conn.execute(
            "INSERT INTO sources (id, source_type, name, url)"
            " VALUES (1, 'blog', 't', 'https://t.example')")
        ids = []
        for i, (stype, published, content) in enumerate(docs):
            cur = self.conn.execute(
                "INSERT INTO raw_documents (source_type, source_id, url,"
                " content_hash, raw_content, published_at, retrieved_at)"
                " VALUES (?, 1, ?, ?, ?, ?, '2026-08-01T00:00:00+00:00')",
                (stype, f"https://t.example/{i}", f"h{i}", content, published))
            ids.append(cur.lastrowid)
        return ids

    def test_empty_db_reports_nothing(self):
        self.assertEqual(drift.build(self.conn), [])

    def test_window_is_anchored_to_newest_doc(self):
        # Old corpus: nothing within 14 days of the wall clock, but the
        # window anchors to 2026-01-20 so both periods are populated.
        self._seed([("blog", "2026-01-01", "x" * 100)] * 5
                   + [("arxiv", "2026-01-20", "y" * 100)] * 5)
        rows = drift.build(self.conn, days=14)
        mix = next(r for r in rows if r["metric"] == "doc source_type mix")
        self.assertEqual(mix["n_ref"], 5)
        self.assertEqual(mix["n_cur"], 5)

    def test_stable_corpus_has_no_major_drift(self):
        docs = []
        for day in range(1, 29):
            docs.append(("blog", f"2026-01-{day:02d}", "x" * 500))
            docs.append(("arxiv", f"2026-01-{day:02d}", "y" * 500))
        self._seed(docs)
        rows = drift.build(self.conn, days=14)
        self.assertTrue(rows)
        self.assertTrue(all(r["verdict"] == "stable" for r in rows))

    def test_source_mix_flip_is_major_psi(self):
        docs = [("blog", "2026-01-01", "x" * 500)] * 20
        docs += [("arxiv", "2026-01-20", "y" * 500)] * 20
        self._seed(docs)
        rows = drift.build(self.conn, days=14)
        mix = next(r for r in rows if r["metric"] == "doc source_type mix")
        self.assertEqual(mix["verdict"], "MAJOR")

    def test_doc_length_shift_is_major_ks(self):
        docs = [("blog", "2026-01-01", "x" * 100)] * 30
        docs += [("blog", "2026-01-20", "x" * 5000)] * 30
        self._seed(docs)
        rows = drift.build(self.conn, days=14)
        length = next(r for r in rows if r["metric"] == "doc length")
        self.assertEqual(length["verdict"], "MAJOR")

    def test_report_returns_major_count(self):
        docs = [("blog", "2026-01-01", "x" * 100)] * 30
        docs += [("arxiv", "2026-01-20", "x" * 5000)] * 30
        self._seed(docs)
        self.assertGreaterEqual(drift.report(self.conn, days=14), 2)


if __name__ == "__main__":
    unittest.main()
