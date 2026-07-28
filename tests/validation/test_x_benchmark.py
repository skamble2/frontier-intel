"""fli.validation.x_benchmark — scoring against the frozen reference set."""
import unittest

from fli.validation.x_benchmark import (_prf, channel_scores, load_benchmark,
                                        load_labels)


class TestScoring(unittest.TestCase):
    POSTS = [{"id": "1", "text": "900 megawatts contracted in Abilene"},
             {"id": "2", "text": "our model tops the reasoning benchmark"},
             {"id": "3", "text": "we deprecate the old api tier"},
             {"id": "4", "text": "unlabelled post, must not be scored"}]
    LABELS = {"1": {"channel": "energy_datacenter"},
              "2": {"channel": "none"},
              "3": {"channel": "competitive_displacement"}}

    def test_prf_arithmetic(self):
        self.assertEqual(_prf(3, 1, 1)["precision"], 0.75)
        self.assertEqual(_prf(3, 1, 1)["recall"], 0.75)
        self.assertEqual(_prf(0, 0, 0)["f1"], 0.0)      # no division by zero

    def test_perfect_predictor(self):
        s = channel_scores(self.POSTS, self.LABELS, None,
                           lambda _p, t: {"900 megawatts contracted in Abilene":
                                          "energy_datacenter",
                                          "we deprecate the old api tier":
                                          "competitive_displacement"}.get(t))
        self.assertEqual((s["tp"], s["fp"], s["fn"]), (2, 0, 0))
        self.assertEqual(s["f1"], 1.0)

    def test_wrong_channel_counts_as_both_fp_and_fn(self):
        """Naming the wrong transmission channel is not a partial success: the
        channel is what says which position an event touches."""
        s = channel_scores(self.POSTS, self.LABELS, None,
                           lambda _p, _t: "compute_memory")
        # 3 labelled posts, all predicted compute_memory -> 0 tp, 3 fp, 2 fn
        self.assertEqual((s["tp"], s["fp"], s["fn"]), (0, 3, 2))

    def test_predicting_none_everywhere_scores_zero_not_a_crash(self):
        s = channel_scores(self.POSTS, self.LABELS, None, lambda _p, _t: None)
        self.assertEqual((s["tp"], s["fp"], s["fn"]), (0, 0, 2))
        self.assertEqual(s["f1"], 0.0)

    def test_unlabelled_posts_are_skipped_not_counted_as_negative(self):
        """Post 4 has no label. Scoring it as a negative would inflate whatever
        it agreed with by accident."""
        s = channel_scores(self.POSTS, self.LABELS, None,
                           lambda _p, _t: "compute_memory")
        self.assertEqual(s["tp"] + s["fp"] + s["fn"], 5)   # 3 labelled, not 4


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

    def test_no_label_claims_to_be_audited(self):
        """If this ever fails someone audited the set — good, but the report's
        'agreement, not accuracy' caveat then needs updating."""
        self.assertEqual(sum(r["audited"] for r in load_labels().values()), 0)


if __name__ == "__main__":
    unittest.main()
