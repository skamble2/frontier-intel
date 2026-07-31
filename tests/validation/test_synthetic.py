"""The synthetic recovery must actually recover.

These tests are the contract behind figure f17: with truth planted by
construction, the machinery's F1/AUC claims are checkable, so check them.
Pure over arrays — no database, no network, deterministic seed.
"""
import unittest

import numpy as np

from fli.validation.synthetic import NOISE, planted_policies, recover

NAMES = ["recency", "corroboration", "specificity", "attribution_confidence",
         "quote_len_words", "channel_official"]


def _matrix(n=300, seed=7):
    """A correlated feature matrix — recovery over independent features is
    trivially easy and would let a broken implementation pass."""
    rng = np.random.RandomState(seed)
    base = rng.randn(n, len(NAMES))
    base[:, 2] += 0.5 * base[:, 5]           # specificity ~ channel_official
    base[:, 1] += 0.3 * base[:, 0]           # corroboration ~ recency
    return (base - base.mean(0)) / base.std(0)


class TestPlantedPolicies(unittest.TestCase):
    def test_four_policies_and_anti_prior_negates_recency(self):
        pol = planted_policies(NAMES)
        self.assertEqual(len(pol), 4)
        self.assertLess(pol["anti_prior"]["recency"], 0)

    def test_unknown_features_are_dropped_not_crashed(self):
        pol = planted_policies(["recency"])
        self.assertEqual(pol["single_feature"], {"recency": 1.0})
        self.assertEqual(pol["hand_shape"], {"recency": 1.0})


class TestRecovery(unittest.TestCase):
    def setUp(self):
        self.results = recover(_matrix(), NAMES, seed=42)

    def test_all_policies_recovered_above_chance(self):
        for name, r in self.results.items():
            self.assertGreater(r["roc_auc"], 0.9,
                               f"{name}: planted policy not recovered")
            self.assertGreater(r["f1"], 0.7, name)

    def test_metrics_are_probabilities(self):
        for r in self.results.values():
            for m in ("roc_auc", "precision", "recall", "f1", "heldout_acc"):
                self.assertGreaterEqual(r[m], 0.0)
                self.assertLessEqual(r[m], 1.0)

    def test_heldout_acc_bounded_by_label_noise(self):
        # The labeler flips NOISE of verdicts, so even perfect recovery cannot
        # agree with the held-out labels much beyond 1 - NOISE.
        for name, r in self.results.items():
            self.assertLessEqual(r["heldout_acc"], 1.0 - NOISE / 2, name)

    def test_deterministic(self):
        again = recover(_matrix(), NAMES, seed=42)
        self.assertEqual(self.results, again)


if __name__ == "__main__":
    unittest.main()
