"""Report figures and tables.

Writes `docs/evaluation-report.md` and PNGs to `docs/figures/`. One command
regenerates every number and chart in the report, so nothing in it is
hand-copied.

Two rules this module enforces:

1. Metrics are tiered by what ground truth exists. F1/precision/recall are only
   reported where truth is known by construction or against a stated human
   reference; on real data with no gold standard the module reports AGREEMENT,
   never "accuracy". Every caption carries its tier.

2. A missing figure says why it is missing. This runs before the pipeline is
   fully populated, so a chart with no data states the command that would
   produce it rather than crashing or drawing an empty axis that looks like a
   result.

Figures read the database; they never write to it. matplotlib and seaborn are
imported lazily, so the pipeline runs without them.

Run:  python3 -m fli.cli evaluate
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from fli import storage
from fli.core.paths import ROOT

FIG_DIR = ROOT / "docs" / "figures"
REPORT_PATH = ROOT / "docs" / "evaluation-report.md"

# Tier labels, printed on every figure so the distinction survives copy-paste.
SYNTHETIC = "SYNTHETIC — ground truth known by construction"
REFERENCE = "vs HUMAN REFERENCE — agreement, not accuracy"
MECHANICAL = "MECHANICAL — arithmetic over the database, no labels needed"
JUDGED = "JUDGED — against an unaudited LLM reference; treat as provisional"


def _style():
    import matplotlib
    matplotlib.use("Agg")                       # headless; no display needed
    import matplotlib.pyplot as plt
    import seaborn as sns
    sns.set_theme(style="whitegrid", context="talk", palette="deep")
    plt.rcParams.update({"figure.dpi": 130, "savefig.bbox": "tight",
                         "axes.titlesize": 13, "axes.labelsize": 11,
                         "figure.titlesize": 14})
    return plt, sns


def _save(plt, fig, name: str, tier: str) -> str:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    fig.text(0.01, 0.005, tier, fontsize=7, style="italic", alpha=0.75)
    path = FIG_DIR / f"{name}.png"
    fig.savefig(path)
    plt.close(fig)
    return f"docs/figures/{name}.png"


class Skipped(Exception):
    """Raised with the command that would produce the missing data."""


# --------------------------------------------------------------------------
# MECHANICAL — available now, no labels required
# --------------------------------------------------------------------------

def fig_funnel(conn) -> tuple[str, str]:
    """Firehose -> signal: the whole filtering funnel in one picture."""
    plt, _ = _style()
    docs = conn.execute("SELECT count(*) FROM raw_documents").fetchone()[0]
    rej = dict(conn.execute(
        "SELECT reason, count(*) FROM rejections GROUP BY 1").fetchall())
    insights = conn.execute("SELECT count(*) FROM insights").fetchone()[0]
    # A funnel must stay a funnel: the last bar has to be a subset of the first.
    # One document yields several events, so plotting the insight count makes
    # the output exceed the input. Count surviving DOCUMENTS and carry the event
    # count as an annotation.
    surviving = conn.execute(
        "SELECT count(DISTINCT ev.document_id) FROM insights i"
        " JOIN evidence ev ON ev.id = i.evidence_id").fetchone()[0]
    stages = [("documents fetched", docs)]
    for reason, n in sorted(rej.items(), key=lambda kv: -kv[1])[:6]:
        stages.append((f"rejected: {reason}", -n))
    stages.append(("documents yielding insights", surviving))

    fig, ax = plt.subplots(figsize=(9, 5))
    labels = [s[0] for s in stages]
    vals = [abs(s[1]) for s in stages]
    colors = ["#2563eb"] + ["#cbd5e1"] * (len(stages) - 2) + ["#16a34a"]
    ax.barh(labels[::-1], vals[::-1], color=colors[::-1])
    for i, v in enumerate(vals[::-1]):
        ax.text(v, i, f" {v}", va="center", fontsize=9)
    ax.set_xlabel("documents")
    ax.set_title(f"Signal-vs-noise funnel — {surviving} of {docs} documents "
                 f"survived, yielding {insights} events")
    return _save(plt, fig, "f1_funnel", MECHANICAL), (
        f"{docs} documents fetched; {surviving} survived filtering "
        f"({surviving / docs:.1%}), yielding {insights} events "
        f"({insights / surviving:.2f} per surviving document)."
        if docs and surviving else "no documents")


def fig_feature_correlation(conn) -> tuple[str, str]:
    """Redundant features inflate a model's apparent confidence, and correlated
    labeling functions violate the Dawid-Skene independence assumption. Both
    are visible here."""
    import pandas as pd
    plt, sns = _style()
    rows = conn.execute(
        "SELECT event_id, feature, value FROM insight_features").fetchall()
    if not rows:
        raise Skipped("python3 -m fli.cli features")
    df = (pd.DataFrame(rows, columns=["event_id", "feature", "value"])
          .pivot(index="event_id", columns="feature", values="value"))
    corr = df.corr()
    fig, ax = plt.subplots(figsize=(8, 6.5))
    sns.heatmap(corr, annot=True, fmt=".2f", cmap="RdBu_r", center=0,
                vmin=-1, vmax=1, square=True, cbar_kws={"shrink": .7},
                annot_kws={"size": 7}, ax=ax)
    ax.set_title("Feature correlation")
    # mask the self-correlation diagonal before taking the strongest pair
    worst = corr.mask(corr.eq(1.0)).abs().stack().idxmax()
    return _save(plt, fig, "f2_feature_correlation", MECHANICAL), (
        f"{len(df.columns)} features over {len(df)} events; "
        f"most correlated pair: {worst[0]} / {worst[1]}.")


def fig_event_type_distribution(conn) -> tuple[str, str]:
    plt, sns = _style()
    rows = conn.execute(
        "SELECT i.event_type, COALESCE(l.name,'(unattributed)') lab, count(*) n"
        " FROM insights i LEFT JOIN labs l ON l.id = i.attributed_lab_id"
        " GROUP BY 1,2").fetchall()
    if not rows:
        raise Skipped("python3 -m fli.cli pipeline")
    import pandas as pd
    df = pd.DataFrame(rows, columns=["event_type", "lab", "n"])
    piv = df.pivot_table(index="lab", columns="event_type", values="n",
                         aggfunc="sum", fill_value=0)
    fig, ax = plt.subplots(figsize=(10, 5))
    piv.plot(kind="barh", stacked=True, ax=ax, colormap="tab20", width=.8)
    ax.set_xlabel("events")
    ax.set_ylabel("")
    ax.set_title("Event types per lab (register balance)")
    ax.legend(fontsize=7, ncol=2, loc="lower right")
    top = df.groupby("event_type")["n"].sum().sort_values(ascending=False)
    return _save(plt, fig, "f3_event_types_by_lab", MECHANICAL), (
        f"most common: {top.index[0]} ({top.iloc[0]}); "
        f"rarest: {top.index[-1]} ({top.iloc[-1]}).")


def fig_cost(conn) -> tuple[str, str]:
    """Token usage and cost, per task."""
    plt, _ = _style()
    rows = conn.execute(
        "SELECT task, model, sum(cost_usd) usd, count(*) n,"
        " sum(input_tokens) tin, sum(output_tokens) tout"
        " FROM llm_calls GROUP BY 1,2 ORDER BY usd DESC").fetchall()
    if not rows:
        raise Skipped("python3 -m fli.cli pipeline")
    labels = [f"{r['task']}\n{r['model'].split('-2')[0]}" for r in rows]
    usd = [r["usd"] or 0 for r in rows]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    bars = ax.bar(labels, usd, color="#7c3aed")
    for b, r in zip(bars, rows):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height(),
                f"${r['usd']:.2f}\n{r['n']} calls", ha="center", va="bottom",
                fontsize=8)
    ax.set_ylabel("USD")
    ax.set_title("Cost per task (tokenomics)")
    total = sum(usd)
    return _save(plt, fig, "f4_cost_by_task", MECHANICAL), (
        f"${total:.2f} total across {sum(r['n'] for r in rows)} calls.")


# --------------------------------------------------------------------------
# JUDGED — against the X reference labels
# --------------------------------------------------------------------------

def fig_lexicon_vs_classifier(conn) -> tuple[str, str]:
    """Does the LLM channel classifier beat the keyword lexicon it replaced?"""
    from fli.core.policy import load_policy
    from fli.validation.x_benchmark import (channel_scores, load_benchmark,
                                            load_labels)
    plt, _ = _style()
    posts, labels = load_benchmark(), load_labels()
    if not labels:
        raise Skipped("fixtures/x-benchmark-29-labels-frozen.json is missing")
    policy = load_policy()
    series = {"keyword lexicon": channel_scores(
        posts, labels, policy, lambda p, t: p.channel_for(t))}

    cache = Path("fixtures/channel-classifier-cache.json")
    if cache.exists():
        from fli.knowledge.channels import _key, _load_cache
        from fli.ops.llm import MODEL_FOR_TASK
        c, model = _load_cache(), MODEL_FOR_TASK["channel"]
        def clf(_p, text):
            v = c.get(_key(text, policy.version, model))
            return None if not v or v["channel"] == "none" else v["channel"]
        series["LLM classifier"] = channel_scores(posts, labels, policy, clf)

    fig, ax = plt.subplots(figsize=(8, 4.5))
    metrics = ["precision", "recall", "f1"]
    width, xs = 0.35, range(len(metrics))
    for i, (name, s) in enumerate(series.items()):
        vals = [s[m] for m in metrics]
        b = ax.bar([x + i * width for x in xs], vals, width, label=name)
        for r, v in zip(b, vals):
            ax.text(r.get_x() + r.get_width() / 2, v, f"{v:.3f}",
                    ha="center", va="bottom", fontsize=9)
    ax.set_xticks([x + width / 2 for x in xs])
    ax.set_xticklabels(metrics)
    ax.set_ylim(0, 1.05)
    ax.axhline(0.80, ls="--", c="#dc2626", lw=1)
    ax.text(0.02, 0.82, "target 0.80", color="#dc2626", fontsize=8)
    ax.set_title("Channel assignment: lexicon vs LLM classifier")
    ax.legend(fontsize=9)
    note = " · ".join(f"{k} F1={v['f1']:.3f}" for k, v in series.items())
    note += (f" — scored on {len(labels)} labels, none human-audited, under "
             f"policy v{policy.version}. Agreement with a stated labeler, not "
             f"accuracy; a lexicon edit moves these numbers by design.")
    if "LLM classifier" not in series:
        note += "  (classifier cache absent: run `python3 -m fli.cli channels`)"
    return _save(plt, fig, "f5_lexicon_vs_classifier", JUDGED), note


# --------------------------------------------------------------------------
# JUDGED — labelers and their estimated reliability
# --------------------------------------------------------------------------

def _labeler_family(labeler: str) -> str:
    """The part of a labeler id that decides whether two labelers are
    independent: `llm:claude-sonnet-5/investment/r1` -> `llm:claude-sonnet-5`.

    Two prompt versions of one model are the same family — they share weights,
    training data and failure modes, so their agreement says nothing about
    whether either is right.
    """
    if labeler.startswith("llm:"):
        return "llm:" + labeler[4:].split("/")[0]
    return labeler.split(":")[0]


def fig_labeler_reliability(conn) -> tuple[str, str]:
    """Dawid-Skene accuracy per labeler, estimated with no gold data.

    Refuses to report a number from a single labeler family. Dawid-Skene infers
    reliability from disagreement under an assumption of conditional
    independence; with one family there is no disagreement to read and the
    algorithm just returns its prior. Prompt variants of one model agreed
    92-100% and were all rated ~0.99, which is why independence is checked at
    the family level rather than per labeler id.
    """
    from fli.intelligence.weak_supervision import dawid_skene
    from fli.intelligence.scoring import PRIMARY_RUBRIC
    import numpy as np
    plt, _ = _style()
    # ONE RUBRIC ONLY. Dawid-Skene assumes every labeler is estimating the SAME
    # latent truth, and rubrics estimate different ones by design. Pooling them
    # scored the technical labeler at 0.523 — barely above chance — not because
    # it judges badly but because it answers a different question from the three
    # investment labelers it was compared against. That is the same pooling
    # error the bake-off had, in a different figure.
    rows = conn.execute(
        "SELECT event_a, event_b, winner, labeler FROM pairwise_labels"
        " WHERE winner != 'tie' AND labeler LIKE ?",
        (f"llm:%/{PRIMARY_RUBRIC}/%",)).fetchall()
    if not rows:
        raise Skipped(f"python3 -m fli.cli judge --rubric {PRIMARY_RUBRIC} --n 300")
    labelers = sorted({r["labeler"] for r in rows})
    families = {_labeler_family(l) for l in labelers}
    if len(families) < 2:
        raise Skipped(
            "python3 -m fli.cli judge --model <second-provider-model> --n 300"
            f"   [Dawid-Skene needs >=2 INDEPENDENT labeler families; the "
            f"database has {len(labelers)} labeler(s) from one family "
            f"({', '.join(sorted(families))}). Estimating reliability from a "
            f"single family reports the algorithm's prior, not a measurement.]")
    items = sorted({(r["event_a"], r["event_b"]) for r in rows})
    ii = {p: i for i, p in enumerate(items)}
    jj = {l: j for j, l in enumerate(labelers)}
    votes = np.zeros((len(items), len(labelers)))
    for r in rows:
        votes[ii[(r["event_a"], r["event_b"])], jj[r["labeler"]]] = \
            1 if r["winner"] == "a" else -1
    _post, acc = dawid_skene(votes)
    order = np.argsort(acc)
    colors = ["#2563eb" if labelers[j].startswith("llm:")
              else "#16a34a" if labelers[j].startswith("human:")
              else "#94a3b8" for j in order]
    fig, ax = plt.subplots(figsize=(9, 4.5))
    bars = ax.barh([labelers[j] for j in order], [acc[j] for j in order], color=colors)
    for b, j in zip(bars, order):
        ax.text(acc[j], b.get_y() + b.get_height() / 2,
                f" {acc[j]:.3f}  ({int((votes[:, j] != 0).sum())} votes)",
                va="center", fontsize=8)
    ax.axvline(0.5, ls="--", c="#dc2626", lw=1)
    ax.text(0.505, -0.6, "chance", color="#dc2626", fontsize=8)
    ax.set_xlim(0, 1.15)
    ax.set_xlabel("estimated accuracy (Dawid-Skene, no gold labels)")
    ax.set_title("Labeler reliability")
    per = ", ".join(f"{labelers[j]} {acc[j]:.3f}" for j in order)
    return _save(plt, fig, "f6_labeler_reliability", JUDGED), (
        f"Rubric `{PRIMARY_RUBRIC}` only: {len(items)} pairs x {len(labelers)} "
        f"labelers across {len(families)} independent model families "
        f"({', '.join(sorted(families))}). Estimated accuracy: {per}. "
        f"Reliability is inferred from disagreement between families, so the "
        f"figure is not produced from a single family — and labelers working to "
        f"a DIFFERENT rubric are excluded, since they are estimating a "
        f"different truth and would be scored as unreliable for disagreeing.")


# --------------------------------------------------------------------------
# JUDGED — the bake-off
# --------------------------------------------------------------------------

def fig_bakeoff(conn) -> tuple[str, str]:
    plt, _ = _style()
    rows = conn.execute(
        "SELECT model, count(*) n FROM event_scores GROUP BY 1").fetchall()
    if not rows:
        raise Skipped("python3 -m fli.cli score --bakeoff")
    from fli.intelligence import scoring
    # persist=False: a figure must not rewrite the scores it is describing.
    res = scoring.bakeoff(conn, rubric=scoring.PRIMARY_RUBRIC, persist=False)
    models = sorted(res["report"], key=lambda m: -(res["report"][m]["heldout_acc"] or 0))
    accs = [res["report"][m]["heldout_acc"] for m in models]
    colors = ["#16a34a" if m == res["winner"] else
              "#94a3b8" if m.startswith("baseline") or m == "hand_weights"
              else "#2563eb" for m in models]
    fig, ax = plt.subplots(figsize=(9, 4.5))
    bars = ax.bar(models, accs, color=colors)
    for b, a in zip(bars, accs):
        ax.text(b.get_x() + b.get_width() / 2, a, f"{a:.3f}",
                ha="center", va="bottom", fontsize=9)
    ax.axhline(0.5, ls="--", c="#dc2626", lw=1)
    ax.text(0.02, 0.51, "chance", color="#dc2626", fontsize=8)
    ax.set_ylabel("held-out pairwise accuracy")
    ax.set_title(f"Bake-off — winner: {res['winner']}")
    ax.tick_params(axis="x", rotation=25)
    return _save(plt, fig, "f7_bakeoff", JUDGED), (
        f"{res['n_labels']} labels, {res['n_test']} held-out pairs; "
        f"winner {res['winner']}.")


def fig_ablation(conn) -> tuple[str, str]:
    plt, _ = _style()
    if not conn.execute("SELECT count(*) FROM event_scores").fetchone()[0]:
        raise Skipped("python3 -m fli.cli score --bakeoff")
    from fli.intelligence import scoring
    abl = scoring.bakeoff(conn, rubric=scoring.PRIMARY_RUBRIC,
                          persist=False)["ablation"]
    items = sorted(abl.items(), key=lambda kv: kv[1])
    fig, ax = plt.subplots(figsize=(9, 5))
    colors = ["#dc2626" if v > 0 else "#94a3b8" for _, v in items]
    ax.barh([k for k, _ in items], [v for _, v in items], color=colors)
    ax.axvline(0, c="black", lw=.8)
    ax.set_xlabel("accuracy LOST when the feature is removed")
    ax.set_title("Feature ablation")
    useful = [k for k, v in items if v > 0.01]
    return _save(plt, fig, "f8_ablation", JUDGED), (
        f"features that matter: {', '.join(useful) if useful else 'none above 0.01'}.")


def fig_per_lab_fairness(conn) -> tuple[str, str]:
    """Lab identity is never a feature, so precision@10 should not
    depend on which lab published the event."""
    plt, _ = _style()
    if not conn.execute("SELECT count(*) FROM event_scores").fetchone()[0]:
        raise Skipped("python3 -m fli.cli score --bakeoff")
    from fli.intelligence import scoring
    per_lab = scoring.bakeoff(conn, rubric=scoring.PRIMARY_RUBRIC,
                              persist=False)["per_lab_p10"]
    if not per_lab:
        raise Skipped("python3 -m fli.cli judge --n 150   (needs more labels)")
    labs = sorted(per_lab, key=lambda k: -per_lab[k])
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.bar(labs, [per_lab[l] for l in labs], color="#0891b2")
    ax.set_ylabel("precision@10")
    ax.set_title("Per-lab fairness check")
    ax.tick_params(axis="x", rotation=25)
    spread = max(per_lab.values()) - min(per_lab.values())
    best, worst = labs[0], labs[-1]
    # Name the two ends. The old text said "one lab dominates", which the data
    # does not show: a wide spread means the ranking serves the WORST-covered
    # lab poorly, and that lab is the finding, not the leader.
    verdict = ("acceptable" if spread < 0.3 else
               f"INVESTIGATE — the ranking serves {worst} "
               f"({per_lab[worst]:.2f}) markedly worse than {best} "
               f"({per_lab[best]:.2f})")
    return _save(plt, fig, "f9_per_lab_fairness", JUDGED), (
        f"spread {spread:.3f} across {len(per_lab)} labs ({verdict}).")


def fig_overfitting(conn) -> tuple[str, str]:
    """Train minus held-out accuracy.

    The bake-off reports held-out accuracy only, so a model that memorised the
    training pairs looks identical to one that generalised. The gap is the
    diagnosis: a large positive gap means the model is fitting noise, which at a
    few hundred pairs over 19 features is the default expectation.
    """
    import numpy as np
    plt, _ = _style()
    from fli.intelligence import scoring
    from fli.intelligence.features import feature_matrix
    if not conn.execute("SELECT count(*) FROM pairwise_labels").fetchone()[0]:
        raise Skipped("python3 -m fli.cli judge --n 200")
    ids, names, X = feature_matrix(conn)
    row = {i: k for k, i in enumerate(ids)}
    Xz, _, _ = scoring._standardize(X)
    # One rubric only: pooling labels from audiences that disagree by design
    # would give a train/test gap describing no ranking that ships.
    pairs = scoring.load_pairs(conn, rubric=scoring.PRIMARY_RUBRIC)
    tr, te = scoring._split(pairs)
    Xtr, ytr = scoring._pair_xy(tr, Xz, row)
    if len(Xtr) < 8:
        raise Skipped("python3 -m fli.cli judge --n 300   (too few decided pairs)")

    gaps = {}
    coef = scoring._fit_logistic(Xtr, ytr)
    s = Xz @ coef
    gaps["logistic"] = (scoring._pairwise_accuracy(tr, s, row),
                        scoring._pairwise_accuracy(te, s, row))
    predict, lbl = scoring._fit_gbm(Xtr, ytr)
    xm = Xz.mean(axis=0)
    sg = np.array([predict((Xz[i] - xm).reshape(1, -1))[0][1] for i in range(len(ids))])
    gaps[lbl] = (scoring._pairwise_accuracy(tr, sg, row),
                 scoring._pairwise_accuracy(te, sg, row))

    fig, ax = plt.subplots(figsize=(8, 4.5))
    models = list(gaps)
    w, xs = 0.35, range(len(models))
    ax.bar([x - w / 2 for x in xs], [gaps[m][0] for m in models], w,
           label="train", color="#94a3b8")
    ax.bar([x + w / 2 for x in xs], [gaps[m][1] for m in models], w,
           label="held-out", color="#2563eb")
    for i, m in enumerate(models):
        gap = gaps[m][0] - gaps[m][1]
        ax.text(i, max(gaps[m]) + .02, f"gap {gap:+.3f}", ha="center",
                fontsize=9, color="#dc2626" if gap > 0.1 else "#16a34a")
    ax.set_xticks(list(xs)); ax.set_xticklabels(models)
    ax.axhline(0.5, ls="--", c="#dc2626", lw=1)
    ax.set_ylim(0, 1.05); ax.set_ylabel("pairwise accuracy"); ax.legend(fontsize=9)
    ax.set_title("Overfitting check — train vs held-out")
    worst = max(gaps, key=lambda m: gaps[m][0] - gaps[m][1])
    g = gaps[worst][0] - gaps[worst][1]
    # A NEGATIVE gap — held-out beating train — is not a better result than a
    # small positive one, and calling it "trustworthy" hides that. On a few
    # dozen held-out pairs it usually means the split happened to be easier,
    # so the number is noisy in BOTH directions.
    if g > 0.1:
        verdict = ("MEMORISING: held-out accuracy is not evidence of "
                   "generalisation.")
    elif g < -0.02:
        verdict = (f"held-out scores ABOVE train, which is not a good sign but "
                   f"a noisy one: on {len(te)} held-out pairs the split was "
                   f"easier than the training half. Treat the accuracy as "
                   f"approximate and buy more labels before trusting it.")
    else:
        verdict = "gap is small; the held-out number is trustworthy."
    return _save(plt, fig, "f10_overfitting", JUDGED), (
        f"largest gap {worst} {g:+.3f} — " + verdict)


def fig_learning_curve(conn) -> tuple[str, str]:
    """Accuracy vs number of training pairs. Answers ONE question directly:
    would labelling more pairs help, or has it plateaued?"""
    import numpy as np
    plt, _ = _style()
    from fli.intelligence import scoring
    from fli.intelligence.features import feature_matrix
    pairs = scoring.load_pairs(conn, rubric=scoring.PRIMARY_RUBRIC)
    decided = [p for p in pairs if p[2] != "tie"]
    if len(decided) < 30:
        raise Skipped("python3 -m fli.cli judge --n 300")
    ids, names, X = feature_matrix(conn)
    row = {i: k for k, i in enumerate(ids)}
    Xz, _, _ = scoring._standardize(X)
    tr_all, te = scoring._split(decided)
    sizes = [n for n in (10, 20, 40, 80, 160, 320) if n <= len(tr_all)]
    rng = np.random.RandomState(42)
    curve = []
    for n in sizes:
        accs = []
        for _ in range(5):                      # average over subsamples
            sub = [tr_all[i] for i in rng.choice(len(tr_all), n, replace=False)]
            Xtr, ytr = scoring._pair_xy(sub, Xz, row)
            if len(set(ytr)) < 2:
                continue
            accs.append(scoring._pairwise_accuracy(
                te, Xz @ scoring._fit_logistic(Xtr, ytr), row))
        if accs:
            curve.append((n, float(np.mean(accs)), float(np.std(accs))))
    if len(curve) < 2:
        raise Skipped("python3 -m fli.cli judge --n 300")
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ns = [c[0] for c in curve]; ms = [c[1] for c in curve]; sd = [c[2] for c in curve]
    ax.errorbar(ns, ms, yerr=sd, marker="o", capsize=4, color="#2563eb")
    ax.axhline(0.5, ls="--", c="#dc2626", lw=1)
    ax.set_xscale("log"); ax.set_xticks(ns); ax.set_xticklabels(ns)
    ax.set_xlabel("training pairs"); ax.set_ylabel("held-out accuracy")
    ax.set_title("Learning curve — does more labelling help?")
    delta = ms[-1] - ms[-2]
    return _save(plt, fig, "f11_learning_curve", JUDGED), (
        f"last step {ns[-2]}->{ns[-1]} pairs moved accuracy {delta:+.3f}; "
        + ("still climbing — more labels are worth buying."
           if delta > 0.02 else
           "plateaued — more labels will NOT help; the features are the limit."))


def fig_rank_skew(conn) -> tuple[str, str]:
    """Does a lab's share of the top ranks match its share of the corpus?

    Per-lab precision@10 measures accuracy; this measures exposure. A lab with
    8% of events holding 40% of the top 50 is a skew the reader notices even
    when the ranking is accurate.
    """
    import pandas as pd
    plt, _ = _style()
    rows = conn.execute(
        "SELECT COALESCE(l.name,'(unattributed)') lab, i.score,"
        " RANK() OVER (ORDER BY i.score DESC) rk"
        " FROM insights i LEFT JOIN labs l ON l.id = i.attributed_lab_id"
        " WHERE i.score IS NOT NULL").fetchall()
    if not rows:
        raise Skipped("python3 -m fli.cli score --bakeoff")
    df = pd.DataFrame(rows, columns=["lab", "score", "rk"])
    corpus = df.groupby("lab").size() / len(df)
    top = df[df.rk <= 50].groupby("lab").size().reindex(corpus.index).fillna(0) / 50
    comp = pd.DataFrame({"corpus share": corpus, "top-50 share": top}).sort_values(
        "top-50 share", ascending=False)
    fig, ax = plt.subplots(figsize=(9, 4.5))
    comp.plot(kind="bar", ax=ax, color=["#94a3b8", "#2563eb"], width=.8)
    ax.set_ylabel("share"); ax.set_xlabel("")
    ax.set_title("Rank skew — top-50 share vs corpus share")
    ax.tick_params(axis="x", rotation=25); ax.legend(fontsize=9)
    # A ratio needs a denominator worth dividing by. xAI has 2 events of 556, so
    # one top-50 placement reads as "11.1x over-represented" next to labs with
    # 150 events — a number produced by n=2, not by skew. Small-n labs are
    # counted and named, never given a multiplier.
    from fli.intelligence.scoring import MIN_FAIRNESS_N
    counts = df.groupby("lab").size()
    big = comp.loc[counts[counts >= MIN_FAIRNESS_N].index.intersection(comp.index)]
    small = sorted(counts[counts < MIN_FAIRNESS_N].index)
    note_small = (f" Not rated: {', '.join(small)} "
                  f"(<{MIN_FAIRNESS_N} events; a ratio on so few is noise)."
                  if small else "")
    if big.empty:
        return _save(plt, fig, "f12_rank_skew", JUDGED), (
            f"no lab has >={MIN_FAIRNESS_N} scored events.{note_small}")
    big = big.assign(ratio=big["top-50 share"] / big["corpus share"].replace(0, float("nan")))
    over = big["ratio"].idxmax()
    return _save(plt, fig, "f12_rank_skew", JUDGED), (
        f"most over-represented: {over} at {big.loc[over,'ratio']:.1f}x its "
        f"corpus share ({big.loc[over,'corpus share']:.0%} of events, "
        f"{big.loc[over,'top-50 share']:.0%} of the top 50).{note_small}")


# (title, builder, png stem). The stem is listed rather than derived from the
# title so a figure that fails to build can have its previous image deleted:
# an out-of-date chart is worse than a missing one, because nothing on the
# image says how old it is.
FIGURES = [
    ("Signal-vs-noise funnel", fig_funnel, "f1_funnel"),
    ("Feature correlation", fig_feature_correlation, "f2_feature_correlation"),
    ("Event types per lab", fig_event_type_distribution, "f3_event_types_by_lab"),
    ("Cost per task", fig_cost, "f4_cost_by_task"),
    ("Lexicon vs LLM classifier", fig_lexicon_vs_classifier, "f5_lexicon_vs_classifier"),
    ("Labeler reliability (Dawid-Skene)", fig_labeler_reliability, "f6_labeler_reliability"),
    ("Bake-off", fig_bakeoff, "f7_bakeoff"),
    ("Feature ablation", fig_ablation, "f8_ablation"),
    ("Per-lab fairness", fig_per_lab_fairness, "f9_per_lab_fairness"),
    ("Overfitting (train vs held-out)", fig_overfitting, "f10_overfitting"),
    ("Learning curve", fig_learning_curve, "f11_learning_curve"),
    ("Rank skew by lab", fig_rank_skew, "f12_rank_skew"),
]


# --------------------------------------------------------------------------
# tables
# --------------------------------------------------------------------------

def corpus_table(conn) -> str:
    q = lambda s: conn.execute(s).fetchone()[0]
    rows = [
        ("documents", q("SELECT count(*) FROM raw_documents")),
        ("evidence spans", q("SELECT count(*) FROM evidence")),
        ("insights (events)", q("SELECT count(*) FROM insights")),
        ("clustered", q("SELECT count(*) FROM insights WHERE cluster_id IS NOT NULL")),
        ("feature rows", q("SELECT count(*) FROM insight_features")),
        ("pairwise labels", q("SELECT count(*) FROM pairwise_labels")),
        ("scored events", q("SELECT count(*) FROM event_scores")),
        ("tracked labs", q("SELECT count(*) FROM labs")),
        ("tracked people", q("SELECT count(*) FROM people")),
        ("LLM spend (USD)", f"{q('SELECT COALESCE(sum(cost_usd),0) FROM llm_calls'):.2f}"),
    ]
    out = ["| quantity | value |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in rows]
    return "\n".join(out)


def labeler_table(conn) -> str:
    rows = conn.execute(
        "SELECT labeler, count(*) n, sum(winner='tie') ties FROM pairwise_labels"
        " GROUP BY 1 ORDER BY n DESC").fetchall()
    if not rows:
        return "_No pairwise labels yet — run `python3 -m fli.cli judge --n 150`._"
    out = ["| labeler | judgements | ties |", "|---|---|---|"]
    out += [f"| `{r['labeler']}` | {r['n']} | {r['ties']} |" for r in rows]
    return "\n".join(out)


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def _discard_stale(stem: str) -> None:
    """Delete the PNG of a figure that did not regenerate this run.

    A figure is either current or absent. Leaving the previous image behind let
    `f6_labeler_reliability.png` survive for a day showing a reliability
    estimate the figure now refuses to compute — and nothing on the image said
    how old it was.
    """
    png = FIG_DIR / f"{stem}.png"
    if png.exists():
        png.unlink()
        print(f"        discarded stale {png.name}")


def build(conn) -> int:
    print(f"figures -> {FIG_DIR}")
    made, skipped = [], []
    for title, fn, stem in FIGURES:
        try:
            path, note = fn(conn)
            made.append((title, path, note))
            print(f"  OK    {title}")
        except Skipped as e:
            skipped.append((title, str(e)))
            print(f"  SKIP  {title}  (needs: {e})")
            _discard_stale(stem)
        except Exception as e:                       # never let one chart kill the run
            skipped.append((title, f"ERROR: {type(e).__name__}: {e}"))
            print(f"  FAIL  {title}  {type(e).__name__}: {e}")
            _discard_stale(stem)

    lines = [
        "# Evaluation report",
        "",
        "Generated by `python3 -m fli.cli evaluate`. Every number and figure "
        "here is reproducible by re-running that one command — nothing is "
        "hand-copied.",
        "",
        "**Metric tiers.** F1/precision/recall are only reported where ground "
        "truth exists: known by construction (synthetic) or against a stated "
        "human reference. On real data with no gold standard this report gives "
        "AGREEMENT, never \"accuracy\". Each figure carries its tier.",
        "", "## Corpus", "", corpus_table(conn), "",
        "## Pairwise labelers", "", labeler_table(conn), "",
        "## Figures", "",
    ]
    for title, path, note in made:
        lines += [f"### {title}", "", f"![{title}]({Path(path).name})", "",
                  f"{note}", ""]
    if skipped:
        lines += ["## Not yet produced", "",
                  "Listed with the command that would produce each — an absent "
                  "figure is a stated gap, not a silent one.", "",
                  "| figure | needs |", "|---|---|"]
        lines += [f"| {t} | `{r}` |" for t, r in skipped]
        lines += [""]
    REPORT_PATH.write_text("\n".join(lines))
    print(f"\nreport -> {REPORT_PATH.relative_to(ROOT)}"
          f"   ({len(made)} figures, {len(skipped)} pending)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Figures + tables for the report.")
    ap.add_argument("--db", default=str(storage.DEFAULT_DB))
    args = ap.parse_args()
    conn = storage.connect(Path(args.db))
    conn.row_factory = sqlite3.Row
    storage.init_db(conn)
    return build(conn)


if __name__ == "__main__":
    raise SystemExit(main())
