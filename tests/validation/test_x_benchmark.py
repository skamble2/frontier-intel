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

    def test_benchmark_is_the_frozen_29(self):
        self.assertEqual(len(load_benchmark()), 29)

    def test_labels_cover_every_post(self):
        posts, labels = load_benchmark(), load_labels()
        self.assertEqual({str(p["id"]) for p in posts}, set(labels))

    def test_boolean_strings_are_normalised(self):
        """The writer stored "False" as a string; a truthy check on that is a
        bug that reads as correct."""
        for r in load_labels().values():
            self.assertIsInstance(r["audited"], bool)

    def test_every_label_carries_a_human_audit(self):
        """The set was human-audited on 2026-07-30 (28 none, 1 talent_movement;
        3 corrections). f5's caption reads the flag and reports a human
        reference. If this fails, labels were regenerated or the flag was
        dropped — either silently demotes every number measured against the
        set back to LLM-agreement."""
        self.assertEqual(sum(r["audited"] for r in load_labels().values()), 29)


if __name__ == "__main__":
    unittest.main()
