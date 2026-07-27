"""fli.intelligence.scoring / features - LAYER 3 scoring units."""
import math
import unittest


class TestScoringUnits(unittest.TestCase):
    """Pure Day-5 helpers (§10)."""

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
