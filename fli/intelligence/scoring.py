"""Task 4 (§10 / §20.4 / day5-spec): the scoring bake-off.

Ranking is turned into binary classification on pairwise feature DIFFERENCES
(x_a - x_b -> a wins), the standard trick that works at ~150 labels. Every model
scores the identical 406-event set, so comparison is fair. A hand-weighted sum is
included as the baseline to beat (the brief calls an arbitrary weighted sum a red
flag); whichever model wins on held-out pairs ships, even if it is the simple one.

Lab identity is never a feature (§22.5); pairwise labels are lab-stratified;
per-lab precision@10 for the winner is the fairness check. No embeddings / vector
store / second DB (§24.3): scikit-learn over 406 rows.

Run:  python -m fli.cli score --bakeoff
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

from fli.intelligence import features as featmod
from fli import storage
from fli.core.config import RANDOM_SEED, TEST_FRAC
from fli.core.policy import load_policy


def hand_weights() -> dict[str, float]:
    """The hand-weighted baseline, read from config/policy.yml at call time.

    A business judgement, so not a constant here. The defence is not that these
    numbers are right but that they are attributable, versioned and owned by
    someone other than the engineer (§10).
    """
    return load_policy().hand_weights


def load_pairs(conn) -> list[tuple[int, int, str]]:
    """Training judgements only.

    `human:%` labels are the audit sample — the one independent signal against
    which the labeler's reliability is measured. Training on them would
    contaminate that measurement, so they are excluded here
    (docs/scoring-without-ground-truth.md §3).
    """
    return [(r["event_a"], r["event_b"], r["winner"]) for r in
            conn.execute("SELECT event_a, event_b, winner FROM pairwise_labels"
                         " WHERE labeler NOT LIKE 'human:%'")]


def _standardize(X):
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd = np.where(sd == 0, 1.0, sd)          # constant features -> 0, no effect
    return (X - mu) / sd, mu, sd


def _pair_xy(pairs, Xz, row):
    """Both directions per non-tie pair -> an antisymmetric training set that a
    fit_intercept=False model turns into a per-event score w·x."""
    xs, ys = [], []
    for a, b, w in pairs:
        if w == "tie":
            continue
        d = Xz[row[a]] - Xz[row[b]]
        y = 1 if w == "a" else 0
        xs.append(d); ys.append(y)
        xs.append(-d); ys.append(1 - y)
    return np.array(xs), np.array(ys)


def _split(pairs, seed=RANDOM_SEED, test_frac=TEST_FRAC):
    rng = np.random.RandomState(seed)
    idx = rng.permutation(len(pairs))
    cut = int(len(pairs) * (1 - test_frac))
    tr = [pairs[i] for i in idx[:cut]]
    te = [pairs[i] for i in idx[cut:]]
    return tr, te


def _pairwise_accuracy(pairs, scores, row) -> float:
    ok = tot = 0
    for a, b, w in pairs:
        if w == "tie":
            continue
        tot += 1
        pred = "a" if scores[row[a]] > scores[row[b]] else "b"
        ok += (pred == w)
    return ok / tot if tot else float("nan")


def _gold_net_wins(pairs) -> dict:
    """Relevance per event: wins minus losses over the labeled pairs."""
    net = defaultdict(int)
    for a, b, w in pairs:
        if w == "a":
            net[a] += 1; net[b] -= 1
        elif w == "b":
            net[b] += 1; net[a] -= 1
        else:
            # A tie still enters both events at 0 — they were judged, so they
            # belong in the p@k / ndcg candidate pool. Not a no-op: the
            # defaultdict access is what creates the keys.
            net[a] += 0; net[b] += 0
    return net


def _precision_at_k(scores, row, gold, k=10) -> float:
    ranked = sorted(gold, key=lambda e: -scores[row[e]])[:k]
    if not ranked:
        return float("nan")
    return sum(1 for e in ranked if gold[e] > 0) / len(ranked)


def _ndcg_at_k(scores, row, gold, k=20) -> float:
    ranked = sorted(gold, key=lambda e: -scores[row[e]])[:k]
    dcg = sum(max(0, gold[e]) / math.log2(i + 2) for i, e in enumerate(ranked))
    ideal = sorted((max(0, g) for g in gold.values()), reverse=True)[:k]
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg > 0 else float("nan")


def _fit_logistic(Xtr, ytr):
    from sklearn.linear_model import LogisticRegression
    # L2 is the default penalty; C=1.0 is the regularization strength.
    clf = LogisticRegression(C=1.0, fit_intercept=False, max_iter=2000)
    clf.fit(Xtr, ytr)
    return clf.coef_[0]


def _fit_gbm(Xtr, ytr):
    """A gradient-boosted contender. Prefer LightGBM/XGBoost if installed;
    otherwise sklearn's HistGradientBoosting (a real GBM) so the bake-off always
    has one. Returns (predict_win_prob, label)."""
    try:
        from lightgbm import LGBMClassifier
        m = LGBMClassifier(n_estimators=200, max_depth=3, verbosity=-1)
        m.fit(Xtr, ytr); return m.predict_proba, "lgbm"
    except ImportError:
        pass
    try:
        from xgboost import XGBClassifier
        m = XGBClassifier(n_estimators=200, max_depth=3, verbosity=0)
        m.fit(Xtr, ytr); return m.predict_proba, "xgb"
    except ImportError:
        pass
    from sklearn.ensemble import HistGradientBoostingClassifier
    m = HistGradientBoostingClassifier(max_iter=200, max_depth=3)
    m.fit(Xtr, ytr); return m.predict_proba, "gbm_sklearn"


def _dense_rank(scores):
    order = np.argsort(-scores)
    rank = np.empty(len(scores), dtype=int)
    rank[order] = np.arange(1, len(scores) + 1)
    return rank


def bakeoff(conn) -> dict:
    ids, names, X = featmod.feature_matrix(conn)
    row = {iid: i for i, iid in enumerate(ids)}
    Xz, _, _ = _standardize(X)
    fidx = {f: j for j, f in enumerate(names)}
    pairs = load_pairs(conn)
    if len(pairs) < 10:
        raise SystemExit(f"only {len(pairs)} labels — run `python -m fli.cli label` first")
    tr, te = _split(pairs)
    gold = _gold_net_wins(pairs)   # relevance from all labels (labeled subset)

    model_scores: dict[str, np.ndarray] = {}
    extras: dict[str, dict] = {}

    # baselines: rank by a single raw feature
    model_scores["baseline_recency"] = X[:, fidx["recency"]].copy()
    model_scores["baseline_corroboration"] = X[:, fidx["corroboration"]].copy()
    # hand-weighted sum on standardized features (the red flag to beat)
    hw = np.zeros(len(ids))
    for f, w in hand_weights().items():
        if f in fidx:
            hw += w * Xz[:, fidx[f]]
    model_scores["hand_weights"] = hw
    # logistic — primary candidate
    Xtr, ytr = _pair_xy(tr, Xz, row)
    coef = _fit_logistic(Xtr, ytr)
    model_scores["logistic"] = Xz @ coef
    extras["logistic"] = {"coef": {names[j]: round(float(coef[j]), 3) for j in range(len(names))}}
    # GBM contender — per-event score = P(beats the average event)
    predict, gbm_label = _fit_gbm(Xtr, ytr)
    xmean = Xz.mean(axis=0)
    gbm_scores = np.array([predict((Xz[i] - xmean).reshape(1, -1))[0][1] for i in range(len(ids))])
    model_scores[gbm_label] = gbm_scores

    # held-out pairwise accuracy + ranking metrics
    ts = storage.now_utc()
    pol = load_policy()   # stamped on every row (check C17)
    conn.execute("DELETE FROM event_scores")
    report = {}
    for model, scores in model_scores.items():
        acc = _pairwise_accuracy(te, scores, row)
        p10 = _precision_at_k(scores, row, gold, 10)
        ndcg = _ndcg_at_k(scores, row, gold, 20)
        report[model] = {"heldout_acc": acc, "p@10": p10, "ndcg@20": ndcg}
        ranks = _dense_rank(scores)
        for i, iid in enumerate(ids):
            conn.execute(
                "INSERT INTO event_scores (event_id, model, score, rank,"
                " policy_version, created_at) VALUES (?,?,?,?,?,?)",
                (iid, model, float(scores[i]), int(ranks[i]), pol.version, ts))
    conn.commit()

    winner = max(report, key=lambda m: (report[m]["heldout_acc"]
                                        if not math.isnan(report[m]["heldout_acc"]) else -1))
    # Ablation refits LOGISTIC and is reported against logistic's own accuracy.
    # It previously used report["logistic"] as the base while the printed winner
    # could be the GBM, so the table described a model that did not win. The
    # base model is now named in the output so the two can never be confused.
    ablation = {}
    ablation_model = "logistic"
    base_acc = report[ablation_model]["heldout_acc"]
    for j, f in enumerate(names):
        keep = [k for k in range(len(names)) if k != j]
        xa, ya = _pair_xy(tr, Xz[:, keep], row)
        s = Xz[:, keep] @ _fit_logistic(xa, ya)
        ablation[f] = round(base_acc - _pairwise_accuracy(te, s, row), 4)

    # per-lab precision@10 for the winner (fairness, §22.5)
    lab_of = {r["id"]: r["lab"] for r in conn.execute(
        "SELECT i.id, COALESCE(l.name,'(none)') lab FROM insights i"
        " LEFT JOIN labs l ON l.id=i.attributed_lab_id")}
    per_lab = {}
    for lab in sorted(set(lab_of.values())):
        g = {e: gold[e] for e in gold if lab_of.get(e) == lab}
        if g:
            per_lab[lab] = round(_precision_at_k(model_scores[winner], row, g, 10), 3)

    _write_winner_scores(conn, ids, names, Xz, model_scores[winner], winner, extras)

    # p@10's base rate: with many ties, few events have net_wins > 0, so a high
    # p@10 can be an artifact of a tiny relevant set rather than good ranking.
    n_relevant = sum(1 for v in gold.values() if v > 0)
    return {"n_labels": len(pairs), "n_test": len(te), "winner": winner,
            "ablation_model": ablation_model,
            "n_gold_events": len(gold), "n_relevant": n_relevant,
            "report": report, "ablation": ablation, "per_lab_p10": per_lab,
            "logistic_coef": extras["logistic"]["coef"], "gbm": gbm_label}


def _write_winner_scores(conn, ids, names, Xz, scores, winner, extras):
    """Populate insights.score + score_components (reader-facing decomposition)
    from the winning model only."""
    fidx = {f: j for j, f in enumerate(names)}
    coef = extras.get("logistic", {}).get("coef", {})
    ranks = _dense_rank(scores)
    for i, iid in enumerate(ids):
        # top contributing features for the reader (standardized value x coef)
        contribs = sorted(((f, round(coef.get(f, 0.0) * Xz[i, fidx[f]], 3)) for f in names),
                          key=lambda kv: -abs(kv[1]))[:5]
        comp = {"model": winner, "rank": int(ranks[i]),
                "top_factors": [{"feature": f, "contribution": c} for f, c in contribs if c]}
        conn.execute("UPDATE insights SET score=?, score_components=? WHERE id=?",
                     (float(scores[i]), json.dumps(comp), iid))
    conn.commit()


def print_report(res: dict) -> None:
    print(f"\n=== bake-off ({res['n_labels']} labels, {res['n_test']} held-out pairs) ===")
    print(f"{'model':<24}{'heldout_acc':>12}{'p@10':>8}{'ndcg@20':>9}")
    for m, d in sorted(res["report"].items(), key=lambda kv: -(kv[1]['heldout_acc'] or 0)):
        print(f"{m:<24}{d['heldout_acc']:>12.3f}{d['p@10']:>8.3f}{d['ndcg@20']:>9.3f}")
    print(f"\nwinner: {res['winner']}  (GBM contender: {res['gbm']})")
    print(f"  p@10 base rate: {res['n_relevant']} of {res['n_gold_events']} labelled "
          f"events have net_wins>0 ({res['n_relevant']/max(res['n_gold_events'],1):.0%}); "
          f"a high p@10 over so few positives is weak evidence.")
    print("\nlogistic coefficients (interpretability):")
    for f, c in sorted(res["logistic_coef"].items(), key=lambda kv: -abs(kv[1])):
        print(f"  {f:<26}{c:>7.3f}")
    print(f"\nablation — refit {res['ablation_model']}, base acc "
          f"{res['report'][res['ablation_model']]['heldout_acc']:.3f} "
          f"(NOT the winner if the winner differs):")
    for f, d in sorted(res["ablation"].items(), key=lambda kv: -kv[1]):
        print(f"  {f:<26}{d:>+8.4f}")
    print("\nper-lab precision@10 (winner — fairness check §22.5):")
    for lab, p in res["per_lab_p10"].items():
        print(f"  {lab:<18}{p:>6.3f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(storage.DEFAULT_DB))
    ap.add_argument("--bakeoff", action="store_true")
    args = ap.parse_args()
    conn = storage.connect(Path(args.db))
    storage.init_db(conn)
    print_report(bakeoff(conn))


if __name__ == "__main__":
    main()
