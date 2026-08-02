"""Elicit ~150 pairwise labels."""
from __future__ import annotations

import argparse
import random
from collections import defaultdict
from pathlib import Path

from fli import storage
from fli.core.config import RANDOM_SEED


def _events(conn):
    return [dict(r) for r in conn.execute(
        "SELECT i.id, i.event_type, COALESCE(l.name,'(none)') AS lab"
        " FROM insights i LEFT JOIN labs l ON l.id = i.attributed_lab_id ORDER BY i.id")]


def sample_pairs(conn, n: int = 150, seed: int = RANDOM_SEED) -> list[tuple[int, int]]:
    """Deterministic stratified pair sample. """
    rng = random.Random(seed)
    events = _events(conn)
    if len(events) < 2:
        return []
    by_lab = defaultdict(list)
    by_type = defaultdict(list)
    for e in events:
        by_lab[e["lab"]].append(e["id"])
        by_type[e["event_type"]].append(e["id"])
    labs = [k for k in by_lab if len(by_lab[k])]
    types = [k for k in by_type if len(by_type[k])]
    ids = [e["id"] for e in events]
    pairs: set = set()

    def add(a, b):
        if a != b:
            pairs.add((min(a, b), max(a, b)))

    guard = 0
    while len(pairs) < n and guard < n * 100:
        guard += 1
        frac = len(pairs) / n
        if frac < 0.5 and len(labs) >= 2:
            la, lb = rng.sample(labs, 2)
            add(rng.choice(by_lab[la]), rng.choice(by_lab[lb]))
        elif frac < 0.8 and len(types) >= 2:
            ta, tb = rng.sample(types, 2)
            add(rng.choice(by_type[ta]), rng.choice(by_type[tb]))
        else:
            a, b = rng.sample(ids, 2)
            add(a, b)
    return sorted(pairs)


def _fmt(conn, event_id: int) -> str:
    """Render an event for a human, with the lab name withheld."""
    r = conn.execute(
        "SELECT i.event_type, i.claim, ev.verbatim_content q"
        " FROM insights i JOIN evidence ev ON ev.id=i.evidence_id"
        " WHERE i.id=?", (event_id,)).fetchone()
    return f"[{r['event_type']}] {r['claim']}\n       quote: \"{r['q'][:140]}\""


def record_label(conn, event_a: int, event_b: int, winner: str, labeler: str,
                 thesis_channel: str | None = None, reason: str | None = None) -> None:
    """One judgement from one labeler."""
    conn.execute(
        "INSERT OR IGNORE INTO pairwise_labels (event_a, event_b, winner,"
        " labeler, thesis_channel, reason, labeled_at) VALUES (?,?,?,?,?,?,?)",
        (event_a, event_b, winner, labeler, thesis_channel, reason,
         storage.now_utc()))
    conn.commit()


def _disagreement_queue(conn, labeler: str, n: int) -> list[tuple]:
    """Pairs where two LLM labelers on this labeler's rubric picked OPPOSITE
    winners and this human has not judged yet."""
    suffix = labeler.split("/", 1)[1] if "/" in labeler else ""
    return [(r["event_a"], r["event_b"], None, None, None)
            for r in conn.execute(
                "SELECT x.event_a, x.event_b FROM pairwise_labels x"
                " JOIN pairwise_labels y ON y.event_a=x.event_a"
                "  AND y.event_b=x.event_b AND y.labeler > x.labeler"
                " WHERE x.labeler LIKE 'llm:%' AND y.labeler LIKE 'llm:%'"
                "  AND x.labeler LIKE '%/' || ? AND y.labeler LIKE '%/' || ?"
                "  AND x.winner != y.winner"
                "  AND NOT EXISTS (SELECT 1 FROM pairwise_labels h"
                "    WHERE h.event_a=x.event_a AND h.event_b=x.event_b"
                "    AND h.labeler=?)"
                " ORDER BY x.event_a, x.event_b LIMIT ?",
                (suffix, suffix, labeler, n))]


def _near_tie_queue(conn, labeler: str, n: int) -> list[tuple]:
    """Pairs whose Dawid-Skene posterior sits closest to 0.5 — the label model's
    least confident calls — that this human has not judged yet."""
    import numpy as np

    from fli.intelligence.weak_supervision import dawid_skene
    suffix = labeler.split("/", 1)[1] if "/" in labeler else ""
    rows = conn.execute(
        "SELECT event_a, event_b, winner, labeler FROM pairwise_labels"
        " WHERE winner != 'tie' AND labeler LIKE '%/' || ?", (suffix,)).fetchall()
    if not rows:
        return []
    labelers = sorted({r["labeler"] for r in rows})
    items = sorted({(r["event_a"], r["event_b"]) for r in rows})
    ii = {p: i for i, p in enumerate(items)}
    jj = {l: j for j, l in enumerate(labelers)}
    votes = np.zeros((len(items), len(labelers)))
    for r in rows:
        votes[ii[(r["event_a"], r["event_b"])], jj[r["labeler"]]] = \
            1 if r["winner"] == "a" else -1
    post, _acc = dawid_skene(votes)
    done = {(r["event_a"], r["event_b"]) for r in conn.execute(
        "SELECT event_a, event_b FROM pairwise_labels WHERE labeler=?", (labeler,))}
    ranked = sorted((abs(post[i] - 0.5), p) for p, i in ii.items() if p not in done)
    return [(a, b, None, None, None) for _, (a, b) in ranked[:n]]


def _audit_queue(conn, labeler: str, n: int) -> list[tuple]:
    """Pairs an LLM has already judged and this human has not — the audit sample.
    Pairs an LLM has already judged and this human has not — the audit sample."""
    return [(r["event_a"], r["event_b"], r["labeler"], r["thesis_channel"], r["reason"])
            for r in conn.execute(
                "SELECT l.event_a, l.event_b, l.labeler, l.thesis_channel, l.reason"
                " FROM pairwise_labels l"
                " WHERE l.labeler LIKE 'llm:%' AND NOT EXISTS ("
                "   SELECT 1 FROM pairwise_labels h WHERE h.event_a=l.event_a"
                "   AND h.event_b=l.event_b AND h.labeler=?)"
                " ORDER BY l.event_a, l.event_b LIMIT ?", (labeler, n))]


def run_cli(conn, n: int, labeler: str, audit: bool = False,
            disagreements: bool = False, near_ties: bool = False) -> None:
    if audit:
        queue = _audit_queue(conn, labeler, n)
        print(f"AUDIT pass as {labeler} — {len(queue)} pair(s) to review.\n"
              f"You are checking RUBRIC COMPLIANCE (docs/labeling-rubric.md),\n"
              f"not deciding what is important. Give your own call; disagreement\n"
              f"is the signal.\n")
    elif disagreements:
        queue = _disagreement_queue(conn, labeler, n)
        print(f"DISAGREEMENT pass as {labeler} — {len(queue)} pair(s) where the\n"
              f"LLM labelers split. Their verdicts are hidden so your call stays\n"
              f"independent; each label here settles a model-family coin flip.\n")
    elif near_ties:
        queue = _near_tie_queue(conn, labeler, n)
        print(f"NEAR-TIE pass as {labeler} — {len(queue)} pair(s) where the\n"
              f"label model is least confident (Dawid-Skene posterior nearest\n"
              f"0.5). Verdicts hidden; each label here moves the posterior most.\n")
    else:
        done = {(r["event_a"], r["event_b"]) for r in conn.execute(
            "SELECT event_a, event_b FROM pairwise_labels WHERE labeler=?", (labeler,))}
        queue = [(a, b, None, None, None) for a, b in sample_pairs(conn, n)
                 if (a, b) not in done]
        print(f"labeling as {labeler} — {len(done)} done, {len(queue)} of {n} left.\n")
    print("a / b / t(ie) / s(kip) / q(uit)\n")

    for a, b, other, channel, reason in queue:
        print(f"A: {_fmt(conn, a)}")
        print(f"B: {_fmt(conn, b)}")
        if other:
            print(f"  {other} said: {channel or '-'} — {reason or '(no reason given)'}")
        ans = input("  a/b/t/s/q > ").strip().lower()
        if ans in ("q", "quit"):
            break
        winner = {"a": "a", "b": "b", "t": "tie"}.get(ans)
        if winner is None:
            print("  (skipped)\n")
            continue
        record_label(conn, a, b, winner, labeler)
        print("  saved\n")

    print("\nlabels by labeler:")
    for r in conn.execute("SELECT labeler, count(*) c FROM pairwise_labels"
                          " GROUP BY 1 ORDER BY c DESC"):
        print(f"  {r['labeler']:<28} {r['c']}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Record pairwise judgements. See docs/labeling-rubric.md.")
    ap.add_argument("--db", default=str(storage.DEFAULT_DB))
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--by", default="soham",
                    help="your name; stored as human:<name>/<rubric>/r<version>")
    ap.add_argument("--rubric", default=None,
                    help="which rubric you are applying (config/rubrics/NAME.yml; "
                         "default: the policy's `primary_rubric`). "
                         "Recorded in the labeler id, because a human judging "
                         "'what moves a position' and one judging 'what should we "
                         "adopt' are answering different questions")
    ap.add_argument("--audit", action="store_true",
                    help="review pairs an LLM already judged, to measure agreement")
    ap.add_argument("--disagreements", action="store_true",
                    help="label only pairs where the LLM labelers disagree "
                         "(verdicts hidden) — the highest-information labels")
    ap.add_argument("--near-ties", action="store_true",
                    help="label pairs where the label model is least confident "
                         "(Dawid-Skene posterior nearest 0.5; verdicts hidden) "
                         "— the refill once the disagreement queue is empty")
    args = ap.parse_args()
    conn = storage.connect(Path(args.db))
    storage.init_db(conn)
    if ":" in args.by:
        labeler = args.by
    else:
        from fli.core.policy import load_policy
        from fli.core.rubric import load_rubric
        rubric_name = args.rubric or load_policy().primary_rubric
        labeler = f"human:{args.by}/{load_rubric(rubric_name).label_suffix}"
    print(f"labeler id: {labeler}\n")
    run_cli(conn, args.n, labeler, audit=args.audit,
            disagreements=args.disagreements, near_ties=args.near_ties)


if __name__ == "__main__":
    main()
