"""fli.intelligence.scoring / features - LAYER 3 scoring units."""
import math
import unittest


class TestScoringUnits(unittest.TestCase):
    """Pure scoring helpers."""

    def test_jaccard(self):
        from fli.intelligence.clustering import jaccard
        self.assertEqual(jaccard(frozenset("ab"), frozenset("ab")), 1.0)
        self.assertEqual(jaccard(frozenset("ab"), frozenset("cd")), 0.0)
        self.assertAlmostEqual(jaccard(frozenset({"a", "b"}), frozenset({"a", "c"})), 1 / 3)

    def test_specificity(self):
        from fli.intelligence.features import _specificity
        self.assertEqual(_specificity("frontier intelligence"), 0.0)
        self.assertEqual(_specificity("$5 per 2 million tokens (10%)"), 5.0)  # 5,2,10 + $ + %

    def test_recency_neutral_on_null(self):
        from datetime import datetime, timezone
        from fli.intelligence.features import _recency, NEUTRAL_RECENCY
        self.assertEqual(_recency(None, datetime.now(timezone.utc)), NEUTRAL_RECENCY)

    def test_pairwise_accuracy(self):
        import numpy as np
        from fli.intelligence.scoring import _pairwise_accuracy
        scores = np.array([2.0, 1.0])           # event 10 outranks event 20
        row = {10: 0, 20: 1}
        self.assertEqual(_pairwise_accuracy([(10, 20, "a")], scores, row), 1.0)
        self.assertEqual(_pairwise_accuracy([(10, 20, "b")], scores, row), 0.0)
        self.assertTrue(math.isnan(_pairwise_accuracy([(10, 20, "tie")], scores, row)))


class TestSlateFilter(unittest.TestCase):
    """What a READER is shown, which is a different question from what scores
    highest. Three tests, one per rule that depends on the slate so far — the
    per-event rules (window, undated) are plain comparisons and not worth a test.
    """

    # Deliberately unbalanced: "released" appears everywhere so it must never
    # bind two items together; "3.6" appears twice so it must.
    CORPUS = ([f"Google DeepMind released model number {i}" for i in range(20)]
              + ["Gemini 3.6 Flash cuts output tokens by 17%",
                 "Google DeepMind is rolling out Gemini 3.6 Flash"])

    def _policy(self, **over):
        from fli.core.policy import Policy
        base = dict(version=1, owner="test", effective_from="2026-01-01",
                    source="test", channels={"c": ("x",)}, event_type_prior={},
                    slate_k=5, hand_weights={}, window_days=3650,
                    max_per_lab=2, story_rare_df=0.10, story_days=7)
        return Policy(**{**base, **over})

    @staticmethod
    def _row(i, lab, claim, day="2026-07-21T00:00:00+00:00", cluster=None):
        return {"id": i, "lab": lab, "claim": claim, "published_at": day,
                "cluster_id": cluster if cluster is not None else i}

    def test_story_rule_uses_rare_tokens_not_common_ones(self):
        """The Gemini case: two claims about one launch, too dissimilar to
        cluster (measured Jaccard 0.125 against a 0.4 threshold), caught here
        because they share the version token. The control matters as much — a
        boilerplate word shared by the whole corpus must NOT merge two events."""
        from fli.intelligence.scoring import SlateFilter
        f = SlateFilter(self._policy(), self.CORPUS)
        self.assertTrue(f.accept(self._row(1, "DeepMind",
                                           "Gemini 3.6 Flash cuts output tokens by 17%")))
        self.assertFalse(f.accept(self._row(2, "DeepMind",
                                            "Google DeepMind is rolling out Gemini 3.6 Flash")))
        self.assertEqual(f.dropped["same_story"], 1)
        # same lab, same day, only the ubiquitous word in common -> distinct news
        self.assertTrue(f.accept(self._row(3, "DeepMind",
                                           "Google DeepMind released model number 4")))

    def test_lab_cap_bounds_one_lab_and_leaves_others_room(self):
        from fli.intelligence.scoring import SlateFilter
        f = SlateFilter(self._policy(story_rare_df=0.0), self.CORPUS)
        taken = [f.accept(self._row(i, "DeepMind", f"unrelated claim alpha {i}"))
                 for i in range(1, 5)]
        self.assertEqual(taken, [True, True, False, False])
        self.assertEqual(f.dropped["lab_cap"], 2)
        self.assertTrue(f.accept(self._row(9, "Mistral", "unrelated claim beta")))

    def test_unattributed_events_are_never_story_merged(self):
        """`lab` is the anchor of the story rule. With no lab there is no anchor,
        and merging on text alone would suppress unrelated events that happen to
        share a rare word — so the rule abstains rather than guesses."""
        from fli.intelligence.scoring import SlateFilter
        f = SlateFilter(self._policy(max_per_lab=0), self.CORPUS)
        claim = "Gemini 3.6 Flash cuts output tokens by 17%"
        self.assertTrue(f.accept(self._row(1, "(unattributed)", claim)))
        self.assertTrue(f.accept(self._row(2, "(unattributed)", claim)))
        self.assertEqual(f.dropped["same_story"], 0)
