"""Populate insights.cluster_id — near-duplicate event clustering.

Corroboration ("how many independent sources reported this event") is a scoring
feature and the resolution of the same-type multiplicity flagged in G5. No
embeddings, no vector DB: Jaccard overlap on normalized claim tokens,
reusing the shared norm(). Clusters never span event_type, so within-document
splits and cross-document restatements of ONE event merge, while distinct events
of the same type (which merely share vocabulary) stay apart.

theta is MEASURED, not guessed (see histogram(), recorded in report-notes.md):
the Jaccard distribution over same-type pairs collapses after 0.2 — 14,320 pairs
<=0.1, then 408 (0.2), 29 (0.3), 11 (0.4), <=1 (>=0.5). The ~10 pairs >=0.4 are
the genuine near-duplicates; 0.4 is the conservative cut (under-clustering beats
merging distinct events).

Run:  python -m fli.cli cluster              # assign cluster_id
      python -m fli.cli cluster --histogram  # print the Jaccard distribution
"""
from __future__ import annotations

import argparse
import sqlite3
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

from fli import storage
from fli.core.text import norm
from fli.core.config import CLUSTER_THETA


def _tokens(claim: str) -> frozenset[str]:
    return frozenset(norm(claim).split())


def jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class _UnionFind:
    def __init__(self, ids):
        self.parent = {i: i for i in ids}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)  # keep the smaller root (stable)


def _rows(conn):
    return [dict(r) for r in conn.execute(
        "SELECT i.id, e.document_id, i.event_type, i.claim FROM insights i"
        " JOIN evidence e ON e.id = i.evidence_id ORDER BY i.id")]


def cluster_all(conn: sqlite3.Connection, theta: float = CLUSTER_THETA) -> dict:
    """Assign a stable cluster_id to every insight. Deterministic and idempotent:
    same corpus -> same clusters. Clusters are numbered 1..N ordered by their
    smallest member id, so ids don't churn on re-run."""
    rows = _rows(conn)
    toks = {r["id"]: _tokens(r["claim"]) for r in rows}
    uf = _UnionFind([r["id"] for r in rows])
    by_type: dict[str, list] = defaultdict(list)
    for r in rows:
        by_type[r["event_type"]].append(r)
    # edges only within an event_type -> clusters never span types (C14)
    for group in by_type.values():
        for a, b in combinations(group, 2):
            if jaccard(toks[a["id"]], toks[b["id"]]) >= theta:
                uf.union(a["id"], b["id"])
    comp: dict[int, list] = defaultdict(list)
    for r in rows:
        comp[uf.find(r["id"])].append(r["id"])
    ordered = sorted(comp.values(), key=min)
    for cid, member_ids in enumerate(ordered, 1):
        for iid in member_ids:
            conn.execute("UPDATE insights SET cluster_id=? WHERE id=?", (cid, iid))
    conn.commit()
    sizes = [len(m) for m in ordered]
    return {"insights": len(rows), "clusters": len(ordered),
            "largest_cluster": max(sizes) if sizes else 0,
            "clustered_events": sum(s for s in sizes if s > 1)}


def histogram(conn: sqlite3.Connection) -> None:
    """Print the Jaccard distribution over same-type pairs — the evidence behind
    the theta choice. Pure measurement, writes nothing."""
    rows = _rows(conn)
    toks = {r["id"]: _tokens(r["claim"]) for r in rows}
    by_type: dict[str, list] = defaultdict(list)
    for r in rows:
        by_type[r["event_type"]].append(r)
    hist: Counter = Counter()
    total = 0
    for group in by_type.values():
        for a, b in combinations(group, 2):
            ta, tb = toks[a["id"]], toks[b["id"]]
            if not ta or not tb:
                continue
            hist[round(jaccard(ta, tb), 1)] += 1
            total += 1
    print(f"Jaccard over {total} same-event-type pairs (theta={CLUSTER_THETA}):")
    for bucket in sorted(hist):
        bar = "#" * min(60, hist[bucket] // 20)
        mark = "  <- theta" if abs(bucket - CLUSTER_THETA) < 0.05 else ""
        print(f"  {bucket:.1f}: {hist[bucket]:6d}  {bar}{mark}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(storage.DEFAULT_DB))
    ap.add_argument("--histogram", action="store_true")
    args = ap.parse_args()
    conn = storage.connect(Path(args.db))
    storage.init_db(conn)
    if args.histogram:
        histogram(conn)
        return
    stats = cluster_all(conn)
    print(f"clusters: {stats['clusters']} over {stats['insights']} insights"
          f" ({stats['clustered_events']} in multi-event clusters,"
          f" largest={stats['largest_cluster']})")


if __name__ == "__main__":
    main()
