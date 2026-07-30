"""fli.validation.x_benchmark — scoring against the frozen reference set."""
import unittest

from fli.validation.x_benchmark import (channel_scores, load_benchmark,
                                        load_labels)


class TestScoring(unittest.TestCase):
    POSTS = [{"id": "1", "text": "900 megawatts contracted in Abilene"},
             {"id": "2", "text": "our model tops the reasoning benchmark"},
             {"id": "3", "text": "we deprecate the old api tier"},
             {"id": "4", "text": "unlabelled post, must not be scored"}]
    LABELS = {"1": {"channel": "energy_datacenter"},
              "2": {"channel": "none"},
              "3": {"channel": "competitive_displacement"}}

    def test_channel_scores(self):
        perfect = {"900 megawatts contracted in Abilene": "energy_datacenter",
                   "we deprecate the old api tier": "competitive_displacement"}
        cases = [
            # (name, predictor, (tp, fp, fn), f1 or None)
            ("perfect predictor", lambda _p, t: perfect.get(t), (2, 0, 0), 1.0),
            # naming the wrong channel is not a partial success: it is both a
            # fp and a fn. And post 4 carries no label, so tp+fp+fn is 5, never
            # 6 — an unlabelled post must not be scored as a negative.
            ("wrong channel is both fp and fn; unlabelled posts skipped",
             lambda _p, _t: "compute_memory", (0, 3, 2), None),
            # also the division-by-zero guard: zero tp must give f1 0.0
            ("predicting none everywhere scores zero, not a crash",
             lambda _p, _t: None, (0, 0, 2), 0.0),
        ]
        for name, predictor, counts, f1 in cases:
            with self.subTest(name):
                s = channel_scores(self.POSTS, self.LABELS, None, predictor)
                self.assertEqual(counts, (s["tp"], s["fp"], s["fn"]))
                if f1 is not None:
                    self.assertEqual(f1, s["f1"])


class TestFrozenFixtures(unittest.TestCase):
    """The fixtures are the reference set. If they drift, every number measured
    against them silently changes meaning."""

    def test_frozen_file_still_holds_exactly_29(self):
        from fli.validation.x_benchmark import BENCHMARK_PATH
        self.assertEqual(len(load_benchmark(BENCHMARK_PATH)), 29)

    def test_labels_cover_every_post(self):
        """Every counted label has its post. The reverse need not hold: an
        extension post's label is invisible until a human audits it."""
        posts, labels = load_benchmark(), load_labels()
        self.assertLessEqual(set(labels), {str(p["id"]) for p in posts})
        self.assertGreaterEqual(len(labels), 29)

    def test_boolean_strings_are_normalised(self):
        """The writer stored "False" as a string; a truthy check on that is a
        bug that reads as correct."""
        for r in load_labels().values():
            self.assertIsInstance(r["audited"], bool)

    def test_every_counted_label_carries_a_human_audit(self):
        """The frozen 29 were human-audited on 2026-07-30 (28 none,
        1 talent_movement; 3 corrections), and unaudited extension rows are
        dropped by load_labels — so every label that counts is human-audited.
        If this fails, labels were regenerated, the flag was dropped, or an
        unaudited seed leaked into scoring: either silently demotes numbers
        measured against the set back to LLM-agreement (or worse, echo)."""
        labels = load_labels()
        self.assertEqual(sum(r["audited"] for r in labels.values()), len(labels))
        self.assertGreaterEqual(len(labels), 29)

    def test_unaudited_extension_rows_never_count(self):
        """The extension seed is the channel classifier's own verdict — one of
        the two systems f5 scores. Counting it before a human confirms would
        score the classifier against itself."""
        from fli.validation.x_benchmark import EXT_LABELS_PATH
        ext = load_labels(EXT_LABELS_PATH)
        counted = load_labels()
        for pid, r in ext.items():
            if not r["audited"]:
                self.assertNotIn(pid, counted)


if __name__ == "__main__":
    unittest.main()
