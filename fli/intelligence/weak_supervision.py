"""Labeling functions + Dawid-Skene EM.

The problem this solves: nobody on this project can credibly say which of two
events matters more to a portfolio manager, so there is no gold standard to
measure the LLM judge against. The usual fallback — "trust the LLM" — just
moves the unanswered question one level up.

Dawid & Skene (1979) estimates each labeler's accuracy AND the latent true
label from the DISAGREEMENT STRUCTURE ALONE, with no gold data. Six cheap
deterministic labeling functions vote on the same pairs the LLM judged; where
they agree the true label is probably that, and a labeler that agrees with the
consensus more often is probably more accurate. EM alternates between the two
until it converges.

That yields the number this project actually needs: **an estimate of how good
the LLM judge is, derived without ground truth**, cross-checkable against the
human audit.

STATED LIMITATION, reported not hidden: DS assumes labelers are conditionally
independent given the true label. Ours are not — `lf_specificity` and
`lf_quote_len` both key off text length. The correlation matrix is printed so
the violation is visible, and near-duplicate LFs are dropped.

No new dependency: ~60 lines of numpy.

Run:  python3 -m fli.cli weak            # vote + estimate, $0, no API key
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import numpy as np

from fli import storage
from fli.core.config import RANDOM_SEED
from fli.core.policy import load_policy
from fli.intelligence.labeling import record_label, sample_pairs

ABSTAIN = 0
A_WINS = 1
B_WINS = -1

_NUMERIC = re.compile(r"\d")


# --------------------------------------------------------------------------
# the six labeling functions — each cheap, each fallible, each independent-ish
# --------------------------------------------------------------------------

def _features(conn) -> dict[int, dict]:
    """Everything the LFs need, in one query."""
    policy = load_policy()
    rows = conn.execute(
        "SELECT i.id, i.event_type, i.cluster_id, ev.verbatim_content q,"
        " d.published_at, s.channel"
        " FROM insights i"
        " JOIN evidence ev ON ev.id = i.evidence_id"
        " JOIN raw_documents d ON d.id = ev.document_id"
        " JOIN sources s ON s.id = d.source_id").fetchall()
    sizes = {r["cluster_id"]: r["n"] for r in conn.execute(
        "SELECT cluster_id, count(*) n FROM insights GROUP BY cluster_id")}
    earliest = {r["cluster_id"]: r["first_id"] for r in conn.execute(
        "SELECT cluster_id, min(id) first_id FROM insights"
        " WHERE cluster_id IS NOT NULL GROUP BY cluster_id")}
    out = {}
    for r in rows:
        q = r["q"] or ""
        out[r["id"]] = {
            "corroboration": sizes.get(r["cluster_id"], 1),
            "numbers": sum(1 for ch in q if ch.isdigit()),
            "quote_len": len(q.split()),
            "type_prior": policy.type_prior(r["event_type"]),
            "channel_hit": 1 if policy.channel_for(q) else 0,
            "position_hits": len(policy.positions_for(q)),
            "published": r["published_at"] or "",
            "is_canonical": 1 if earliest.get(r["cluster_id"]) == r["id"] else 0,
            "official": 1 if r["channel"] == "official" else 0,
        }
    return out


def _cmp(x, y) -> int:
    """Higher wins; equal abstains. Abstention is a real answer — an LF that
    cannot separate a pair must say so rather than guess, or Dawid-Skene will
    read its coin-flips as evidence."""
    return A_WINS if x > y else B_WINS if y > x else ABSTAIN


LABELING_FUNCTIONS = {
    "lf:corroboration":   lambda a, b: _cmp(a["corroboration"], b["corroboration"]),
    "lf:specificity":     lambda a, b: _cmp(a["numbers"], b["numbers"]),
    "lf:channel_hit":     lambda a, b: _cmp(a["channel_hit"], b["channel_hit"]),
    "lf:position_hit":    lambda a, b: _cmp(a["position_hits"], b["position_hits"]),
    "lf:event_type_prior": lambda a, b: _cmp(a["type_prior"], b["type_prior"]),
    "lf:novelty":         lambda a, b: _cmp(a["is_canonical"], b["is_canonical"]),
}


def vote(conn, n: int = 150) -> dict:
    """Every LF votes on the sampled pairs and the votes are stored as labels.

    Written into `pairwise_labels` like any other labeler — that is the point of
    `UNIQUE (event_a, event_b, labeler)`. One table, one reliability model,
    LLM and heuristics treated identically.
    """
    feats = _features(conn)
    pairs = [(a, b) for a, b in sample_pairs(conn, n) if a in feats and b in feats]
    counts = {}
    for name, fn in LABELING_FUNCTIONS.items():
        cast = 0
        for a, b in pairs:
            v = fn(feats[a], feats[b])
            if v == ABSTAIN:
                continue
            record_label(conn, a, b, "a" if v == A_WINS else "b", name,
                         reason="deterministic labeling function")
            cast += 1
        counts[name] = cast
        print(f"  {name:<24} voted on {cast:>4} of {len(pairs)} pairs"
              f"  ({cast / len(pairs):.0%} coverage)" if pairs else "")
    return counts


# --------------------------------------------------------------------------
# Dawid-Skene EM
# --------------------------------------------------------------------------

def dawid_skene(votes: np.ndarray, iters: int = 100, tol: float = 1e-6):
    """Estimate labeler accuracies and latent labels with no gold data.

    votes: (n_items, n_labelers) with +1 / -1 / 0(abstain).
    Returns (posterior P(a wins) per item, accuracy per labeler).

    Binary specialisation of Dawid & Skene 1979: each labeler has a single
    accuracy p_j = P(vote == truth), which is the symmetric-error case. Enough
    here, and it keeps the estimate identifiable at ~150 items.
    """
    n_items, n_lab = votes.shape
    # init: majority vote, smoothed away from 0/1 so log() stays finite
    post = np.clip((votes.sum(axis=1) > 0).astype(float), 0.05, 0.95)
    acc = np.full(n_lab, 0.7)
    prev = None
    for _ in range(iters):
        # M-step: accuracy = agreement with the current posterior
        for j in range(n_lab):
            mask = votes[:, j] != 0
            if not mask.any():
                acc[j] = 0.5                       # never voted: uninformative
                continue
            agree = np.where(votes[mask, j] == 1, post[mask], 1 - post[mask])
            acc[j] = np.clip(agree.mean(), 0.01, 0.99)
        # E-step: posterior from the labelers that actually voted
        log_a = np.zeros(n_items)
        log_b = np.zeros(n_items)
        for j in range(n_lab):
            m = votes[:, j] != 0
            says_a = votes[:, j] == 1
            log_a[m] += np.log(np.where(says_a[m], acc[j], 1 - acc[j]))
            log_b[m] += np.log(np.where(says_a[m], 1 - acc[j], acc[j]))
        mx = np.maximum(log_a, log_b)
        pa, pb = np.exp(log_a - mx), np.exp(log_b - mx)
        post = pa / (pa + pb)
        if prev is not None and np.abs(post - prev).max() < tol:
            break
        prev = post.copy()
    return post, acc


def estimate(conn, n: int = 150) -> dict:
    """Build the vote matrix from pairwise_labels and run EM."""
    rows = conn.execute(
        "SELECT event_a, event_b, winner, labeler FROM pairwise_labels"
        " WHERE winner != 'tie'").fetchall()
    if not rows:
        raise SystemExit("no pairwise_labels — run `fli.cli judge` and "
                         "`fli.cli weak --vote` first")
    labelers = sorted({r["labeler"] for r in rows})
    items = sorted({(r["event_a"], r["event_b"]) for r in rows})
    idx_i = {p: i for i, p in enumerate(items)}
    idx_j = {l: j for j, l in enumerate(labelers)}

    votes = np.zeros((len(items), len(labelers)))
    for r in rows:
        votes[idx_i[(r["event_a"], r["event_b"])], idx_j[r["labeler"]]] = \
            A_WINS if r["winner"] == "a" else B_WINS

    post, acc = dawid_skene(votes)

    print(f"\nDawid-Skene over {len(items)} pairs x {len(labelers)} labelers"
          f"  (no gold labels used)")
    print(f"\n  {'labeler':<26}{'votes':>7}{'est. accuracy':>15}")
    order = np.argsort(-acc)
    for j in order:
        cast = int((votes[:, j] != 0).sum())
        flag = "  <- the LLM judge" if labelers[j].startswith("llm:") else ""
        print(f"  {labelers[j]:<26}{cast:>7}{acc[j]:>15.3f}{flag}")

    # The independence assumption is violated; show by how much rather than
    # asserting it holds.
    print("\n  labeler agreement matrix (DS assumes near-independence):")
    print("  " + "".join(f"{l.split(':')[1][:8]:>10}" for l in labelers))
    for j, lj in enumerate(labelers):
        cells = []
        for k in range(len(labelers)):
            both = (votes[:, j] != 0) & (votes[:, k] != 0)
            cells.append(f"{(votes[both, j] == votes[both, k]).mean():>10.2f}"
                         if both.sum() else f"{'-':>10}")
        print(f"  {lj.split(':')[1][:8]:<10}" + "".join(cells))

    human = [j for j, l in enumerate(labelers) if l.startswith("human:")]
    if human:
        print("\n  cross-check: DS estimated the human auditor at "
              f"{acc[human[0]]:.3f} without being told they are the reference.")
    else:
        print("\n  no human: audit some pairs to cross-check the DS estimate.")
    return {"labelers": labelers, "accuracy": dict(zip(labelers, acc.round(3))),
            "n_pairs": len(items), "posterior": post}


def main() -> None:
    ap = argparse.ArgumentParser(description="Labeling functions + Dawid-Skene. Free.")
    ap.add_argument("--db", default=str(storage.DEFAULT_DB))
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--vote-only", action="store_true")
    args = ap.parse_args()
    conn = storage.connect(Path(args.db))
    storage.init_db(conn)
    print(f"labeling functions voting on up to {args.n} pairs (seed {RANDOM_SEED})")
    vote(conn, args.n)
    if not args.vote_only:
        estimate(conn, args.n)


if __name__ == "__main__":
    main()
