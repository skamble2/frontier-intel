"""The scoring bake-off.

Ranking becomes binary classification on pairwise feature differences
(x_a - x_b -> a wins), which works at the label counts available here. Every
model scores the identical event set, so the comparison is fair, and a
hand-weighted sum is included as the baseline to beat. Whichever model wins on
held-out pairs ships, even if it is the simple one.

Lab identity is never a feature, pairwise labels are lab-stratified, and
per-lab precision@10 for the winner is the fairness check. No embeddings,
vector store or second database — scikit-learn over a few hundred rows.

Run:  python3 -m fli.cli score --bakeoff
      python3 -m fli.cli score --bakeoff --rubric investment
      python3 -m fli.cli score --top 10 --rubric investment
"""
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

# Below this many labelled events, a per-lab precision@10 is arithmetic on too
# little to be a fairness measurement. Not tuned: it is the smallest n at which
# a p@10 can differ from 1.0 or 0.0 by more than one event.
MIN_FAIRNESS_N = 10


def primary_rubric() -> str:
    """The persona whose ranking also lands in insights.score, and which the
    evaluation figures describe unless told otherwise. Which audience the fund
    serves by default is a business decision, so it comes from
    config/policy.yml at call time rather than being hard-coded here."""
    return load_policy().primary_rubric


def hand_weights() -> dict[str, float]:
    """The hand-weighted baseline, read from config/policy.yml at call time.

    A business judgement, so not a constant here. The defence is not that these
    numbers are right but that they are attributable, versioned and owned by
    the fund rather than hard-coded here.
    """
    return load_policy().hand_weights


def load_pairs(conn, include_lf: bool = False, verbose: bool = True,
               rubric: str | None = None) -> list[tuple[int, int, str, str]]:
    """Training judgements, as (event_a, event_b, winner, labeler).

    Four exclusions:

    `human:%` — the audit sample, the one independent signal labeler reliability
    is measured against. Training on it would contaminate that.

    `conf=low` — a forced choice on an equal pair is a coin flip that would
    enter training looking like signal. The judge prompt promises this.

    `lf:%` — circular. The labeling functions are deterministic functions of the
    features the models train on (`lf:specificity` <-> `specificity`, and so on),
    so training on their votes partly fits an identity function. Including them
    scored gbm 0.697 / logistic 0.672; excluding them, gbm 0.663 / logistic
    0.684, which flips the winner to the interpretable model. `include_lf=True`
    reproduces that comparison as a diagnostic, not a training mode.

    `rubric` — only judgements made under this rubric train this model.
    Audiences disagree about which event matters, and pooling the label sets
    would average that into a ranking serving neither.
    """
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
    sd = np.where(sd == 0, 1.0, sd)          # constant features -> 0, no effect
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
    """Split at the PAIR level, not the row level. The same (a, b) pair judged
    by several labelers is several rows; a row-level split puts the identical
    pair on both sides, so "held-out" accuracy partly measures memorisation of
    training pairs. All rows for one pair land on the same side."""
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


def bakeoff(conn, include_lf: bool = False, rubric: str | None = None,
            persist: bool = True) -> dict:
    """Train and compare models on one rubric's judgements.

    `rubric=None` pools every judgement regardless of rubric, which is only
    correct while a single rubric exists. Pass a name for one audience's
    ranking: extraction, clustering and features underneath are shared, and only
    the definition of "important" differs. The split sits at the judge rather
    than the renderer, because a render-time filter cannot rescue an audience
    whose events the score already calls worthless.

    `persist=False` is reporting mode — see the note at the write below.
    """
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
    # IN-SAMPLE by construction: relevance comes from ALL labels, train and
    # test alike, so p@10 and NDCG are not held out. Only `heldout_acc` is.
    # Building gold from test labels only would leave too few positives to rank.
    gold = _gold_net_wins(pairs)

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

    # `acc_llm` excludes labeling-function votes, which are deterministic
    # functions of the training features (lf:specificity <-> specificity, and so
    # on) — accuracy against those is partly a model recovering its own inputs.
    te_llm = [p for p in te if p[3].startswith("llm:")] if te and len(te[0]) > 3 else []
    ts = storage.now_utc()
    pol = load_policy()   # stamped on every row (check C17)
    # `model` carries the rubric as a prefix: "investment:gbm_sklearn". A prefix
    # rather than a column because event_scores is UNIQUE (event_id, model), and
    # SQLite cannot alter a UNIQUE constraint without rebuilding a table that
    # checks C15-C17 depend on and that holds every scored event.
    tag = f"{rubric}:" if rubric else ""

    # Score every model first, pick the winner, then write, so the winner flag
    # can be set in the same pass.
    report = {}
    for model, scores in model_scores.items():
        report[model] = {
            "heldout_acc": _pairwise_accuracy(te, scores, row),
            "heldout_acc_llm": _pairwise_accuracy(te_llm, scores, row),
            "p@10": _precision_at_k(scores, row, gold, 10),
            "ndcg@20": _ndcg_at_k(scores, row, gold, 20)}
    winner = max(report, key=lambda m: (report[m]["heldout_acc"]
                                        if not math.isnan(report[m]["heldout_acc"]) else -1))

    # persist=False is reporting mode: a figure must never mutate the thing it
    # describes. The evaluation figures call bakeoff() to read numbers, and
    # writing here would drop the per-rubric rankings and replace them with one
    # trained on both rubrics' labels pooled.
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
                     # flags the shipped model for this rubric, so top_events
                     # can find the ranking that counts without re-running the
                     # bake-off to discover who won
                     '{"winner": true}' if model == winner else None,
                     pol.version, ts))
        conn.commit()
    # Ablation refits logistic and is reported against logistic's own accuracy,
    # which is not the winner's when the GBM wins. The base model is named in
    # the output so the two cannot be confused.
    ablation = {}
    ablation_model = "logistic"
    base_acc = report[ablation_model]["heldout_acc"]
    for j, f in enumerate(names):
        keep = [k for k in range(len(names)) if k != j]
        xa, ya = _pair_xy(tr, Xz[:, keep], row)
        s = Xz[:, keep] @ _fit_logistic(xa, ya)
        ablation[f] = round(base_acc - _pairwise_accuracy(te, s, row), 4)

    # per-lab precision@10 for the winner (fairness check)
    lab_of = {r["id"]: r["lab"] for r in conn.execute(
        "SELECT i.id, COALESCE(l.name,'(none)') lab FROM insights i"
        " LEFT JOIN labs l ON l.id=i.attributed_lab_id")}
    # Below MIN_FAIRNESS_N, p@10 is arithmetic on too few events to mean
    # anything: a lab with 2 good events scores 1.000, indistinguishable in the
    # figure from a lab with 152. Small-n labs are counted, not scored.
    per_lab, per_lab_small = {}, {}
    for lab in sorted(set(lab_of.values())):
        g = {e: gold[e] for e in gold if lab_of.get(e) == lab}
        if not g:
            continue
        target = per_lab if len(g) >= MIN_FAIRNESS_N else per_lab_small
        target[lab] = round(_precision_at_k(model_scores[winner], row, g, 10), 3)
        if target is per_lab_small:
            per_lab_small[lab] = (per_lab_small[lab], len(g))

    # insights.score is a single column and can only hold one audience's
    # opinion, so it is written for the pooled run and the primary rubric only.
    # Other rubrics are read through event_scores via top_events(rubric=...).
    if persist and rubric in (None, primary_rubric()):
        _write_winner_scores(conn, ids, names, Xz, model_scores[winner], winner, extras)

    # p@10's base rate: with many ties, few events have net_wins > 0, so a high
    # p@10 can be an artifact of a tiny relevant set rather than good ranking.
    n_relevant = sum(1 for v in gold.values() if v > 0)
    return {"n_labels": len(pairs), "n_test": len(te), "winner": winner,
            "ablation_model": ablation_model,
            "n_gold_events": len(gold), "n_relevant": n_relevant,
            "report": report, "ablation": ablation, "per_lab_p10": per_lab,
            "per_lab_p10_small_n": per_lab_small,
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


_EDGE = re.compile(r"^[^\w]+|[^\w]+$")


def _story_tokens(claim: str) -> set[str]:
    """Tokens for same-story matching, with edge punctuation removed.

    `norm()` keeps punctuation attached because it backs the verbatim quote
    check (C2), where stripping would loosen an invariant. That leaves `cyber,`
    and `cyber` as different tokens — harmless there, wrong here, so the
    stripping is local. Single characters are dropped as list markers.
    """
    return {t for t in (_EDGE.sub("", w) for w in norm(claim).split()) if len(t) > 1}


class SlateFilter:
    """Decides what a reader is shown, given the ordering the scorer produced.

    A class because it is stateful: the window and undated rules judge each
    event on its own, while the cluster, story and lab rules judge a candidate
    against what has already been selected. This object is the slate so far.

    Nothing here touches `event_scores`. Every decision is reversible by editing
    config/policy.yml and re-printing — no re-train required.
    """

    def __init__(self, policy, corpus_claims: list[str]):
        self.pol = policy
        self.cutoff = (datetime.now(timezone.utc)
                       - timedelta(days=policy.window_days)).isoformat()
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
        """True if an already-chosen item is the same announcement.

        Compares only against the handful already selected, so there is no
        transitive chaining — a union-find version of this merged 41 unrelated
        events into one "story" by hopping A-B-C.
        """
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
        Counts are printed with the slate: a filter that silently discards is
        indistinguishable from a bug."""
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
        if row["cluster_id"] is not None:   # NULL is "unclustered", not a shared id
            self.seen_clusters.add(row["cluster_id"])
        self.by_lab[row["lab"]] += 1
        self.chosen.append(row)
        return True


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def top_events(conn, k: int = 10, window_days: int | None = None,
               dedupe: bool = True, show_undated: bool | None = None,
               rubric: str | None = None) -> list[dict]:
    """The ranked list a reader sees.

    Scoring produces an ordering; this applies the editorial boundaries on top,
    all configured in policy.yml. What to SHOW is a separate decision from how
    to score, so none of it is a scoring change.

    1. Window — recent events only. The model gives recency a coefficient near
       zero, because the judge rewards "shipped vs intended", not publication
       date.
    2. Undated out — an event we cannot date cannot be presented as recent.
       Undated events take a neutral recency of 0.5 in the features.
    3. One per cluster — keep the highest-scoring member and count the rest as
       corroboration. That is what a cluster means.
    4. One per story — clusters are too fine to be news. One model launch
       produced 12 events across 9 clusters at a peak pairwise Jaccard of 0.158
       against a 0.4 threshold, so no clustering setting merges them. Grouped
       here rather than in `insights`, where it would corrupt the corroboration
       feature that scoring depends on.
    5. Lab cap — without it one lab took half the top 10.

    Rules 1-2 are per-event; 3-5 depend on what has already been chosen, which
    is why they live in `SlateFilter` rather than in SQL.
    """
    pol = load_policy()
    window_days = pol.window_days if window_days is None else window_days
    show_undated = pol.show_undated if show_undated is None else show_undated

    # With a rubric, the score comes from that rubric's winning model in
    # event_scores rather than from the single-valued insights.score.
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
        # A synthesized mobility event's document is a lab page, which has no
        # published_at; its honest date is the arrival observation recorded in
        # its locator. Without this the digest's undated rule would silently
        # hide every move the register detects.
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

    # Document frequency is computed over the WHOLE corpus, not the window, so
    # the meaning of "uncommon token" does not drift as the window slides.
    all_claims = [r[0] for r in conn.execute(
        "SELECT claim FROM insights WHERE claim IS NOT NULL")]

    # `dedupe=False` turns off all three slate-composition rules, not just the
    # cluster one: evaluation code uses it to see the scorer's raw ordering, and
    # a half-disabled filter would be a misleading baseline.
    pol = replace(pol, window_days=window_days, show_undated=show_undated)
    if not dedupe:
        pol = replace(pol, max_per_lab=0, story_rare_df=0.0)
    filt = SlateFilter(pol, all_claims)

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
    print(f"{'model':<24}{'heldout_acc':>12}{'p@10*':>8}{'ndcg@20*':>9}")
    print("  * p@10 and ndcg are IN-SAMPLE (relevance built from all labels); "
          "only heldout_acc is out-of-sample.")
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
