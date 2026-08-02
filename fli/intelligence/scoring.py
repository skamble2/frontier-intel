"""The scoring bake-off."""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from fli.intelligence import features as featmod
from fli import storage
from fli.core.config import RANDOM_SEED, TEST_FRAC
from fli.core.policy import load_policy
from fli.core.text import norm

MIN_FAIRNESS_N = 10


def primary_rubric() -> str:
    """The persona whose ranking also lands in insights.score, and which the
    evaluation figures describe unless told otherwise."""
    return load_policy().primary_rubric


def hand_weights() -> dict[str, float]:
    """The hand-weighted baseline, read from config/policy.yml at call time."""
    return load_policy().hand_weights


def load_pairs(conn, include_lf: bool = False, verbose: bool = True,
               rubric: str | None = None) -> list[tuple[int, int, str, str]]:
    """Training judgements, as (event_a, event_b, winner, labeler)."""
    where = ["labeler NOT LIKE 'human:%'",
             "NOT (labeler LIKE 'llm:%' AND reason LIKE '%conf=low%')"]
    if not include_lf:
        where.append("labeler NOT LIKE 'lf:%'")
    params: list = []
    if rubric is not None:
        where.append("labeler LIKE ?")
        params.append(f"llm:%/{rubric}/%")
    rows = [(r["event_a"], r["event_b"], r["winner"], r["labeler"]) for r in
            conn.execute("SELECT event_a, event_b, winner, labeler"
                         " FROM pairwise_labels WHERE " + " AND ".join(where),
                         params)]
    if verbose:
        n_lf = conn.execute("SELECT count(*) FROM pairwise_labels"
                            " WHERE labeler LIKE 'lf:%'").fetchone()[0]
        n_low = conn.execute("SELECT count(*) FROM pairwise_labels"
                             " WHERE labeler LIKE 'llm:%'"
                             " AND reason LIKE '%conf=low%'").fetchone()[0]
        notes = []
        if n_lf and not include_lf:
            notes.append(f"{n_lf} labeling-function votes (circular with features)")
        if n_low:
            notes.append(f"{n_low} low-confidence LLM verdicts")
        if notes:
            print(f"  excluded {'; '.join(notes)} -> training on {len(rows)}")
        if include_lf:
            print("  WARNING --include-lf: LF votes ARE circular with the "
                  "feature set. Diagnostic only.")
    return rows


def _standardize(X):
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd = np.where(sd == 0, 1.0, sd)
    return (X - mu) / sd, mu, sd


def _pair_xy(pairs, Xz, row):
    """Both directions per non-tie pair -> an antisymmetric training set that a
    fit_intercept=False model turns into a per-event score w·x."""
    xs, ys = [], []
    for a, b, w, *_ in pairs:
        if w == "tie":
            continue
        d = Xz[row[a]] - Xz[row[b]]
        y = 1 if w == "a" else 0
        xs.append(d); ys.append(y)
        xs.append(-d); ys.append(1 - y)
    return np.array(xs), np.array(ys)


def _split(pairs, seed=RANDOM_SEED, test_frac=TEST_FRAC):
    """Split at the PAIR level, not the row level. """
    keys = sorted({(p[0], p[1]) for p in pairs})
    rng = np.random.RandomState(seed)
    idx = rng.permutation(len(keys))
    cut = int(len(keys) * (1 - test_frac))
    test_keys = {keys[i] for i in idx[cut:]}
    tr = [p for p in pairs if (p[0], p[1]) not in test_keys]
    te = [p for p in pairs if (p[0], p[1]) in test_keys]
    return tr, te


def _pairwise_accuracy(pairs, scores, row) -> float:
    ok = tot = 0
    for a, b, w, *_ in pairs:
        if w == "tie":
            continue
        tot += 1
        pred = "a" if scores[row[a]] > scores[row[b]] else "b"
        ok += (pred == w)
    return ok / tot if tot else float("nan")


def _gold_net_wins(pairs) -> dict:
    """Relevance per event: wins minus losses over the labeled pairs."""
    net = defaultdict(int)
    for a, b, w, *_ in pairs:
        if w == "a":
            net[a] += 1; net[b] -= 1
        elif w == "b":
            net[b] += 1; net[a] -= 1
        else:
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
    clf = LogisticRegression(C=1.0, fit_intercept=False, max_iter=2000)
    clf.fit(Xtr, ytr)
    return clf.coef_[0]


def _fit_gbm(Xtr, ytr):
    """A gradient-boosted contender. """
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


def bakeoff(conn, include_lf: bool = False, rubric: str | None = None,
            persist: bool = True) -> dict:
    """Train and compare models on one rubric's judgements."""
    ids, names, X = featmod.feature_matrix(conn)
    row = {iid: i for i, iid in enumerate(ids)}
    Xz, _, _ = _standardize(X)
    fidx = {f: j for j, f in enumerate(names)}
    pairs = load_pairs(conn, include_lf=include_lf, rubric=rubric)
    if len(pairs) < 10:
        raise SystemExit(
            f"only {len(pairs)} labels"
            + (f" under rubric {rubric!r}" if rubric else "")
            + " — run `python3 -m fli.cli judge --n 300"
            + (f" --rubric {rubric}" if rubric else "") + "` first")
    tr, te = _split(pairs)
    gold = _gold_net_wins(pairs)
    gold_te = _gold_net_wins(te)

    model_scores: dict[str, np.ndarray] = {}
    extras: dict[str, dict] = {}

    model_scores["baseline_recency"] = X[:, fidx["recency"]].copy()
    model_scores["baseline_corroboration"] = X[:, fidx["corroboration"]].copy()
    hw = np.zeros(len(ids))
    for f, w in hand_weights().items():
        if f in fidx:
            hw += w * Xz[:, fidx[f]]
    model_scores["hand_weights"] = hw
    Xtr, ytr = _pair_xy(tr, Xz, row)
    coef = _fit_logistic(Xtr, ytr)
    model_scores["logistic"] = Xz @ coef
    extras["logistic"] = {"coef": {names[j]: round(float(coef[j]), 3) for j in range(len(names))}}
    predict, gbm_label = _fit_gbm(Xtr, ytr)
    xmean = Xz.mean(axis=0)
    gbm_scores = np.array([predict((Xz[i] - xmean).reshape(1, -1))[0][1] for i in range(len(ids))])
    model_scores[gbm_label] = gbm_scores

    te_llm = [p for p in te if p[3].startswith("llm:")] if te and len(te[0]) > 3 else []
    human_pairs = [(r["event_a"], r["event_b"], r["winner"], r["labeler"])
                   for r in conn.execute(
                       "SELECT event_a, event_b, winner, labeler"
                       " FROM pairwise_labels WHERE labeler LIKE ?"
                       " AND winner != 'tie'",
                       (f"human:%/{rubric}/%" if rubric else "human:%",))]
    ts = storage.now_utc()
    pol = load_policy()
    tag = f"{rubric}:" if rubric else ""

    report = {}
    for model, scores in model_scores.items():
        report[model] = {
            "heldout_acc": _pairwise_accuracy(te, scores, row),
            "heldout_acc_llm": _pairwise_accuracy(te_llm, scores, row),
            "human_acc": _pairwise_accuracy(human_pairs, scores, row),
            "p@10": _precision_at_k(scores, row, gold, 10),
            "ndcg@20": _ndcg_at_k(scores, row, gold, 20),
            "p@10_heldout": _precision_at_k(scores, row, gold_te, 10),
            "ndcg@20_heldout": _ndcg_at_k(scores, row, gold_te, 20)}
    winner = max(report, key=lambda m: (report[m]["heldout_acc"]
                                        if not math.isnan(report[m]["heldout_acc"]) else -1))

    if persist:
        if rubric:
            conn.execute("DELETE FROM event_scores WHERE model LIKE ?", (f"{tag}%",))
        else:
            conn.execute("DELETE FROM event_scores")
        for model, scores in model_scores.items():
            ranks = _dense_rank(scores)
            for i, iid in enumerate(ids):
                conn.execute(
                    "INSERT INTO event_scores (event_id, model, score, rank,"
                    " components, policy_version, created_at) VALUES (?,?,?,?,?,?,?)",
                    (iid, tag + model, float(scores[i]), int(ranks[i]),
                     '{"winner": true}' if model == winner else None,
                     pol.version, ts))
        conn.commit()
    ablation = {}
    ablation_model = "logistic"
    base_acc = report[ablation_model]["heldout_acc"]
    for j, f in enumerate(names):
        keep = [k for k in range(len(names)) if k != j]
        xa, ya = _pair_xy(tr, Xz[:, keep], row)
        s = Xz[:, keep] @ _fit_logistic(xa, ya)
        ablation[f] = round(base_acc - _pairwise_accuracy(te, s, row), 4)

    lab_of = {r["id"]: r["lab"] for r in conn.execute(
        "SELECT i.id, COALESCE(l.name,'(none)') lab FROM insights i"
        " LEFT JOIN labs l ON l.id=i.attributed_lab_id")}
    per_lab, per_lab_small, per_lab_detail = {}, {}, {}
    for lab in sorted(set(lab_of.values())):
        g = {e: gold[e] for e in gold if lab_of.get(e) == lab}
        if not g:
            continue
        p10 = round(_precision_at_k(model_scores[winner], row, g, 10), 3)
        rel = sum(1 for v in g.values() if v > 0)
        miss_types: dict = {}
        for e in sorted(g, key=lambda e: -model_scores[winner][row[e]])[:10]:
            if g[e] <= 0:
                et = conn.execute("SELECT event_type FROM insights WHERE id=?",
                                  (e,)).fetchone()[0]
                miss_types[et] = miss_types.get(et, 0) + 1
        per_lab_detail[lab] = {"n": len(g), "relevant": rel,
                               "base": round(rel / len(g), 3), "p10": p10,
                               "miss_types": miss_types}
        if len(g) >= MIN_FAIRNESS_N:
            per_lab[lab] = p10
        else:
            per_lab_small[lab] = (p10, len(g))

    if persist and rubric in (None, primary_rubric()):
        _write_winner_scores(conn, ids, names, Xz, model_scores[winner], winner, extras)

    n_relevant = sum(1 for v in gold.values() if v > 0)
    n_relevant_te = sum(1 for v in gold_te.values() if v > 0)
    return {"n_labels": len(pairs), "n_test": len(te), "winner": winner,
            "ablation_model": ablation_model,
            "n_human": len(human_pairs),
            "n_gold_events": len(gold), "n_relevant": n_relevant,
            "n_gold_events_heldout": len(gold_te),
            "n_relevant_heldout": n_relevant_te,
            "report": report, "ablation": ablation, "per_lab_p10": per_lab,
            "per_lab_p10_small_n": per_lab_small,
            "per_lab_detail": per_lab_detail,
            "logistic_coef": extras["logistic"]["coef"], "gbm": gbm_label}


def _write_winner_scores(conn, ids, names, Xz, scores, winner, extras):
    """Populate insights.score + score_components (reader-facing decomposition)
    from the winning model only."""
    fidx = {f: j for j, f in enumerate(names)}
    coef = extras.get("logistic", {}).get("coef", {})
    ranks = _dense_rank(scores)
    for i, iid in enumerate(ids):
        contribs = sorted(((f, round(coef.get(f, 0.0) * Xz[i, fidx[f]], 3)) for f in names),
                          key=lambda kv: -abs(kv[1]))[:5]
        comp = {"model": winner, "rank": int(ranks[i]),
                "top_factors": [{"feature": f, "contribution": c} for f, c in contribs if c]}
        conn.execute("UPDATE insights SET score=?, score_components=? WHERE id=?",
                     (float(scores[i]), json.dumps(comp), iid))
    conn.commit()


_EDGE = re.compile(r"^[^\w]+|[^\w]+$")


def _story_tokens(claim: str) -> set[str]:
    """Tokens for same-story matching, with edge punctuation removed."""
    return {t for t in (_EDGE.sub("", w) for w in norm(claim).split()) if len(t) > 1}


class SlateFilter:
    """Decides what a reader is shown, given the ordering the scorer produced.
    Decides what a reader is shown, given the ordering the scorer produced."""

    def __init__(self, policy, corpus_claims: list[str],
                 no_mech_quotes: set[str] | None = None,
                 not_entailed: set[int] | None = None):
        self.pol = policy
        self.cutoff = (datetime.now(timezone.utc)
                       - timedelta(days=policy.window_days)).isoformat()
        self.no_mech_quotes = no_mech_quotes
        self.not_entailed = not_entailed or set()
        self.rare_cut = policy.story_rare_df * max(len(corpus_claims), 1)
        df: Counter = Counter()
        for c in corpus_claims:
            for w in _story_tokens(c):
                df[w] += 1
        self.df = df
        self.chosen: list[dict] = []
        self.seen_clusters: set = set()
        self.by_lab: Counter = Counter()
        self.dropped: Counter = Counter()

    def _rare(self, claim: str) -> set[str]:
        if self.rare_cut <= 0:
            return set()
        return {w for w in _story_tokens(claim) if self.df[w] <= self.rare_cut}

    def _same_story(self, row) -> bool:
        """True if an already-chosen item is the same announcement."""
        if not row["published_at"] or row["lab"] == "(unattributed)":
            return False
        rare = self._rare(row["claim"])
        if not rare:
            return False
        when = _parse_ts(row["published_at"])
        for s in self.chosen:
            if s["lab"] != row["lab"] or not s["published_at"]:
                continue
            if abs((_parse_ts(s["published_at"]) - when).days) > self.pol.story_days:
                continue
            if rare & self._rare(s["claim"]):
                return True
        return False

    def accept(self, row) -> bool:
        """Apply every rule in order, counting the reason for each rejection.
        Apply every rule in order, counting the reason for each rejection."""
        if row["id"] in self.not_entailed:
            self.dropped["not_entailed"] += 1
            return False
        if self.no_mech_quotes is not None and (row["quote"] or "") in self.no_mech_quotes:
            self.dropped["no_mechanism"] += 1
            return False
        if not row["published_at"]:
            if not self.pol.show_undated:
                self.dropped["undated"] += 1
                return False
        elif row["published_at"] < self.cutoff:
            self.dropped["outside_window"] += 1
            return False
        if row["cluster_id"] is not None and row["cluster_id"] in self.seen_clusters:
            self.dropped["duplicate_cluster"] += 1
            return False
        if self._same_story(row):
            self.dropped["same_story"] += 1
            return False
        if self.pol.max_per_lab and self.by_lab[row["lab"]] >= self.pol.max_per_lab:
            self.dropped["lab_cap"] += 1
            return False
        if row["cluster_id"] is not None:
            self.seen_clusters.add(row["cluster_id"])
        self.by_lab[row["lab"]] += 1
        self.chosen.append(row)
        return True


def _parse_ts(s: str) -> datetime:
    """ISO timestamp or bare date -> aware datetime. """
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def top_events(conn, k: int = 10, window_days: int | None = None,
               dedupe: bool = True, show_undated: bool | None = None,
               rubric: str | None = None) -> list[dict]:
    """The ranked list a reader sees."""
    pol = load_policy()
    window_days = pol.window_days if window_days is None else window_days
    show_undated = pol.show_undated if show_undated is None else show_undated

    if rubric:
        src = ("(SELECT event_id, score FROM event_scores"
               " WHERE model LIKE ? AND components LIKE '%\"winner\"%')")
        params = (f"{rubric}:%",)
        score_expr = "s.score"
        join = f" JOIN {src} s ON s.event_id = i.id"
    else:
        params = ()
        score_expr = "i.score"
        join = ""

    rows = conn.execute(
        f"SELECT i.id, i.claim, {score_expr} AS score, i.score_components,"
        " i.event_type, i.cluster_id, COALESCE(l.name,'(unattributed)') lab,"
        " COALESCE(d.published_at,"
        "   CASE WHEN ev.locator LIKE '%mobility_synthesis%'"
        "        THEN json_extract(ev.locator,'$.to_first_observed') END)"
        "   AS published_at,"
        " d.url, d.source_type, ev.verbatim_content quote"
        " FROM insights i"
        " JOIN evidence ev ON ev.id = i.evidence_id"
        " JOIN raw_documents d ON d.id = ev.document_id"
        " LEFT JOIN labs l ON l.id = i.attributed_lab_id"
        + join +
        f" WHERE {score_expr} IS NOT NULL"
        f" ORDER BY {score_expr} DESC", params).fetchall()

    all_claims = [r[0] for r in conn.execute(
        "SELECT claim FROM insights WHERE claim IS NOT NULL")]

    no_mech: set | None = None
    if rubric and rubric in pol.require_mechanism:
        from fli.knowledge.channels import cached_verdicts
        quotes = [r["quote"] or "" for r in rows]
        no_mech = {t for t, v in cached_verdicts(quotes).items()
                   if v["channel"] == "none"}

    bad = {r[0] for r in conn.execute(
        "SELECT DISTINCT insight_id FROM claim_checks"
        " WHERE verdict='not_entailed'")}

    pol = replace(pol, window_days=window_days, show_undated=show_undated)
    if not dedupe:
        pol = replace(pol, max_per_lab=0, story_rare_df=0.0)
        no_mech = None
    filt = SlateFilter(pol, all_claims, no_mech_quotes=no_mech,
                       not_entailed=bad)

    out = []
    for r in rows:
        row = dict(r)
        if not dedupe:
            row = {**row, "cluster_id": None}
        if filt.accept(row):
            out.append(row)
            if len(out) >= k:
                break
    return out, dict(filt.dropped)


def print_top(conn, k: int = 10, rubric: str | None = None) -> None:
    pol = load_policy()
    items, dropped = top_events(conn, k, rubric=rubric)
    print(f"top {len(items)} [{rubric or 'all-labels'}] — window {pol.window_days}d · one per cluster · "
          f"one per story (<={pol.story_rare_df:.0%} df, {pol.story_days}d) · "
          f"max {pol.max_per_lab}/lab · "
          f"undated {'shown' if pol.show_undated else 'excluded'}")
    if dropped:
        print(f"  skipped: {dropped}")
    print()
    for i, e in enumerate(items, 1):
        print(f"{i:>2}. [{e['event_type']:<14} {e['lab']:<16} {e['source_type']:<8}"
              f" {(e['published_at'] or '?')[:10]}]  score {e['score']:.3f}")
        print(f"    {e['claim'][:110]}")
        print(f"    \"{(e['quote'] or '')[:100]}\"")
        print(f"    {e['url']}")


def print_report(res: dict) -> None:
    print(f"\n=== bake-off ({res['n_labels']} labels, {res['n_test']} held-out pairs) ===")
    print(f"{'model':<24}{'heldout_acc':>12}{'p@10':>8}{'ndcg@20':>9}{'p@10*':>8}{'ndcg*':>8}")
    print("  p@10 / ndcg@20: relevance from HELD-OUT pairs only — out-of-sample,"
          " like heldout_acc.\n"
          "  * starred columns: relevance from ALL labels (in-sample; kept as a"
          " coverage diagnostic).")
    for m, d in sorted(res["report"].items(), key=lambda kv: -(kv[1]['heldout_acc'] or 0)):
        print(f"{m:<24}{d['heldout_acc']:>12.3f}"
              f"{d['p@10_heldout']:>8.3f}{d['ndcg@20_heldout']:>9.3f}"
              f"{d['p@10']:>8.3f}{d['ndcg@20']:>8.3f}")
    print(f"\nwinner: {res['winner']}  (GBM contender: {res['gbm']})")
    if res["n_human"]:
        h = res["report"][res["winner"]]["human_acc"]
        print(f"  human audit: winner agrees with {h:.3f} of {res['n_human']} "
              f"decided human pairs — fully out-of-sample (humans never train "
              f"the model; heldout_acc above is measured against the JUDGES).")
    print(f"  p@10 base rate: {res['n_relevant']} of {res['n_gold_events']} labelled "
          f"events have net_wins>0 ({res['n_relevant']/max(res['n_gold_events'],1):.0%}); "
          f"held-out gold: {res['n_relevant_heldout']} of "
          f"{res['n_gold_events_heldout']} — with positives this scarce, a "
          f"high p@10 over either gold is weak evidence.")
    print("\nlogistic coefficients (interpretability):")
    for f, c in sorted(res["logistic_coef"].items(), key=lambda kv: -abs(kv[1])):
        print(f"  {f:<26}{c:>7.3f}")
    print(f"\nablation — refit {res['ablation_model']}, base acc "
          f"{res['report'][res['ablation_model']]['heldout_acc']:.3f} "
          f"(NOT the winner if the winner differs):")
    for f, d in sorted(res["ablation"].items(), key=lambda kv: -kv[1]):
        print(f"  {f:<26}{d:>+8.4f}")
    print(f"\nper-lab precision@10 (winner — fairness check, "
          f"labs with >={MIN_FAIRNESS_N} labelled events):")
    for lab, p in res["per_lab_p10"].items():
        print(f"  {lab:<18}{p:>6.3f}")
    if res["per_lab_p10_small_n"]:
        print("  not scored — too few labelled events for p@10 to mean anything:")
        for lab, (p, n) in res["per_lab_p10_small_n"].items():
            print(f"  {lab:<18}{'n/a':>6}   (n={n}; raw value would be {p:.3f})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(storage.DEFAULT_DB))
    ap.add_argument("--bakeoff", action="store_true")
    ap.add_argument("--top", type=int, metavar="K",
                    help="print the reader-facing top-K (window + dedupe applied)")
    ap.add_argument("--include-lf", action="store_true",
                    help="ALSO train on labeling-function votes. Circular with "
                         "the features; use only to reproduce the leakage study.")
    ap.add_argument("--rubric", metavar="NAME",
                    help="train/rank for ONE audience, e.g. investment or "
                         "technical. Omit to pool every judgement, which is "
                         "only correct while a single rubric exists.")
    ap.add_argument("--all-rubrics", action="store_true",
                    help="run the bake-off once per rubric in config/rubrics/")
    args = ap.parse_args()
    conn = storage.connect(Path(args.db))
    storage.init_db(conn)
    if args.top:
        print_top(conn, args.top, rubric=args.rubric)
        return
    if args.all_rubrics:
        from fli.core.rubric import available
        for name in available():
            print(f"\n{'=' * 70}\nRUBRIC: {name}\n{'=' * 70}")
            print_report(bakeoff(conn, include_lf=args.include_lf, rubric=name))
        return
    print_report(bakeoff(conn, include_lf=args.include_lf, rubric=args.rubric))


if __name__ == "__main__":
    main()
