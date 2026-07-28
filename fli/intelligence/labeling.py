"""Elicit ~150 pairwise labels.

"Which of these two would you rather see?" is faster and more consistent than
absolute importance scores. Sampling is STRATIFIED — ~50% cross-lab,
~30% cross-type, ~20% random — so the ranker can't just learn one lab's writing
style, and the deliberate cross pairs carry the most information. Deterministic
sample (seeded) + resumable: a labeled pair is never re-asked.

Single annotator by design, and reported as such rather than hidden. If a
second annotator labels 40 overlapping pairs, report agreement.

Run:  python -m fli.cli label --n 150 --by soham
"""
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
    """Deterministic stratified pair sample. Same corpus + n -> same pairs, so
    labeling is resumable by simply skipping pairs already in pairwise_labels."""
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
        if frac < 0.5 and len(labs) >= 2:                 # cross-lab
            la, lb = rng.sample(labs, 2)
            add(rng.choice(by_lab[la]), rng.choice(by_lab[lb]))
        elif frac < 0.8 and len(types) >= 2:              # cross-type
            ta, tb = rng.sample(types, 2)
            add(rng.choice(by_type[ta]), rng.choice(by_type[tb]))
        else:                                             # random fill
            a, b = rng.sample(ids, 2)
            add(a, b)
    return sorted(pairs)


def _fmt(conn, event_id: int) -> str:
    """Render an event for a human, with the lab name withheld.

    The rubric bans lab identity as a reason and the cheapest way to enforce
    that is not to show it. Per-lab precision@10 is the fairness check, so a
    labeler primed by lab prestige would invalidate it."""
    r = conn.execute(
        "SELECT i.event_type, i.claim, ev.verbatim_content q"
        " FROM insights i JOIN evidence ev ON ev.id=i.evidence_id"
        " WHERE i.id=?", (event_id,)).fetchone()
    return f"[{r['event_type']}] {r['claim']}\n       quote: \"{r['q'][:140]}\""


def record_label(conn, event_a: int, event_b: int, winner: str, labeler: str,
                 thesis_channel: str | None = None, reason: str | None = None) -> None:
    """One judgement from one labeler.

    `labeler` is 'llm:<model>' | 'human:<name>' | 'lf:<function>'. The same pair
    may be judged by many labelers — that is what lets reliability be estimated
    from disagreement rather than assumed.
    """
    conn.execute(
        "INSERT OR IGNORE INTO pairwise_labels (event_a, event_b, winner,"
        " labeler, thesis_channel, reason, labeled_at) VALUES (?,?,?,?,?,?,?)",
        (event_a, event_b, winner, labeler, thesis_channel, reason,
         storage.now_utc()))
    conn.commit()


def _audit_queue(conn, labeler: str, n: int) -> list[tuple]:
    """Pairs an LLM has already judged and this human has not — the audit
    sample. Deterministic order, so the pass is resumable."""
    return [(r["event_a"], r["event_b"], r["labeler"], r["thesis_channel"], r["reason"])
            for r in conn.execute(
                "SELECT l.event_a, l.event_b, l.labeler, l.thesis_channel, l.reason"
                " FROM pairwise_labels l"
                " WHERE l.labeler LIKE 'llm:%' AND NOT EXISTS ("
                "   SELECT 1 FROM pairwise_labels h WHERE h.event_a=l.event_a"
                "   AND h.event_b=l.event_b AND h.labeler=?)"
                " ORDER BY l.event_a, l.event_b LIMIT ?", (labeler, n))]


def run_cli(conn, n: int, labeler: str, audit: bool = False) -> None:
    if audit:
        queue = _audit_queue(conn, labeler, n)
        print(f"AUDIT pass as {labeler} — {len(queue)} pair(s) to review.\n"
              f"You are checking RUBRIC COMPLIANCE (docs/labeling-rubric.md),\n"
              f"not deciding what is important. Give your own call; disagreement\n"
              f"is the signal.\n")
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
                    help="your name; stored as human:<name>")
    ap.add_argument("--audit", action="store_true",
                    help="review pairs an LLM already judged, to measure agreement")
    args = ap.parse_args()
    conn = storage.connect(Path(args.db))
    storage.init_db(conn)
    labeler = args.by if ":" in args.by else f"human:{args.by}"
    run_cli(conn, args.n, labeler, audit=args.audit)


if __name__ == "__main__":
    main()
