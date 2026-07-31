"""Synthetic ground truth: plant a known policy, ask the machinery for it back.

Every figure on real data is capped at AGREEMENT, because no gold standard for
"which frontier-lab event matters more" exists. This module is the one place
F1 / precision / recall / ROC-AUC are honest: relevance is known by
construction, because we planted it.

Four policies of increasing difficulty are planted over the REAL standardized
feature matrix — real, so the training has to cope with the actual feature
correlations (official-channel co-varies with specificity, and so on) rather
than an independent-Gaussian fantasy where recovery is trivially easy. From
each planted policy, noisy pairwise labels are generated the way the judge
produces them (a forced winner per pair, some fraction wrong), and the SAME
training path as the bake-off (antisymmetric pair differences ->
fit_intercept=False logistic) is asked to recover the planted ranking.

What this validates is the machinery, not the product: a high recovery score
says "IF the judge's preferences are near-linear in these features, this
pipeline finds them through 10% label noise". It says nothing about whether
the judge's preferences are correct — that is what the human sittings measure.

Run:  exercised by `python3 -m fli.cli evaluate` (figure f17).
"""
from __future__ import annotations

import numpy as np

from fli.core.config import RANDOM_SEED

# Label noise: fraction of pair verdicts flipped. 10% is the same order as the
# judge's observed disagreement with the human audit (f6: 0.86-0.88 agreement),
# so the synthetic labeler is about as unreliable as the real one.
NOISE = 0.10
N_PAIRS = 400   # same order as one rubric's real training set


def planted_policies(names: list[str]) -> dict[str, dict[str, float]]:
    """Four known weight vectors, easiest to hardest.

    single_feature   one feature is the whole policy — sanity floor.
    hand_shape       the shape of config/policy.yml's hand weights — the
                     policy the system actually believes readers hold.
    dense_mixed      many features, mixed signs — a fussy reader.
    anti_prior       recency NEGATIVE — a policy that contradicts the priors
                     baked into the baselines, so nothing recovers it by luck.
    """
    pol = {
        "single_feature": {"recency": 1.0},
        "hand_shape": {"recency": 1.0, "corroboration": 0.6,
                       "specificity": 0.5, "attribution_confidence": 0.4,
                       "quote_len_words": 0.2},
        "dense_mixed": {"specificity": 0.8, "corroboration": 0.7,
                        "attribution_confidence": 0.5, "recency": 0.4,
                        "quote_len_words": -0.3, "channel_official": -0.5},
        "anti_prior": {"recency": -1.0, "specificity": 0.8,
                       "corroboration": 0.6},
    }
    known = set(names)
    return {k: {f: w for f, w in v.items() if f in known}
            for k, v in pol.items()}


def _plant_and_recover(Xz: np.ndarray, w: np.ndarray,
                       rng: np.random.RandomState) -> dict:
    """One policy: truth by construction -> noisy pairs -> the bake-off's own
    training path -> recovery metrics that are honestly F1/AUC."""
    from fli.intelligence.scoring import _fit_logistic, _pair_xy, _split

    n = len(Xz)
    s_true = Xz @ w
    # Relevance by construction: above the (standardized) average event. A
    # natural threshold rather than a top-k cut, so precision and recall are
    # free to differ and the metrics are not three copies of one number.
    truth = s_true > 0

    pairs = []
    for _ in range(N_PAIRS):
        a, b = rng.choice(n, size=2, replace=False)
        winner = "a" if s_true[a] > s_true[b] else "b"
        if rng.rand() < NOISE:
            winner = "b" if winner == "a" else "a"
        pairs.append((int(a), int(b), winner, "synth"))

    row = {i: i for i in range(n)}          # ids ARE row indices here
    tr, te = _split(pairs)
    Xtr, ytr = _pair_xy(tr, Xz, row)
    coef = _fit_logistic(Xtr, ytr)
    s_hat = Xz @ coef

    pred = s_hat > 0
    tp = int(np.sum(pred & truth))
    fp = int(np.sum(pred & ~truth))
    fn = int(np.sum(~pred & truth))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = (2 * precision * recall / (precision + recall)
          if precision + recall else 0.0)

    from sklearn.metrics import roc_auc_score
    auc = float(roc_auc_score(truth, s_hat))

    ok = tot = 0
    for a, b, wnr, _lab in te:
        tot += 1
        ok += (("a" if s_hat[a] > s_hat[b] else "b") == wnr)
    return {"roc_auc": round(auc, 3), "precision": round(precision, 3),
            "recall": round(recall, 3), "f1": round(f1, 3),
            "heldout_acc": round(ok / tot, 3) if tot else float("nan"),
            "n_pos": int(truth.sum()), "n": n}


def recover(Xz: np.ndarray, names: list[str],
            seed: int = RANDOM_SEED) -> dict[str, dict]:
    """Plant each policy over the given standardized matrix and measure
    recovery. Pure over arrays, so tests need no database."""
    fidx = {f: j for j, f in enumerate(names)}
    out = {}
    for pname, weights in planted_policies(names).items():
        if not weights:
            continue
        w = np.zeros(len(names))
        for f, wt in weights.items():
            w[fidx[f]] = wt
        rng = np.random.RandomState(seed)    # fresh per policy: independent
        out[pname] = _plant_and_recover(Xz, w, rng)
    return out


def run_synthetic(conn, seed: int = RANDOM_SEED) -> dict[str, dict]:
    """Plant the four policies over the real corpus's feature matrix."""
    from fli.intelligence import features as featmod
    from fli.intelligence.scoring import _standardize

    ids, names, X = featmod.feature_matrix(conn)
    if len(ids) < 50:
        raise ValueError(f"only {len(ids)} events with features — synthetic "
                         "recovery over so few rows measures noise")
    Xz, _, _ = _standardize(X)
    return recover(Xz, names, seed=seed)
