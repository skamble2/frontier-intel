"""Corpus drift monitoring: PSI and KS between a current window and history.

The pipeline's models (stage-1 filter, scoring bake-off, judge labels) were all
fitted against a corpus with a particular shape. When that shape moves — a lab
floods the feed, event-type mix tilts, score distribution shifts — those fits
quietly degrade before any invariant breaks. This module measures the movement:

  PSI (population stability index) over categorical mixes
      doc source_type, insight event_type
  KS  (two-sample Kolmogorov-Smirnov) over continuous distributions
      document length, insight score

The current window is the last `--days` anchored to the NEWEST document in the
DB, not to the wall clock, so the report is reproducible on a static corpus.
The reference is everything before the window.

Drift is a monitoring signal, not an invariant violation: it is deliberately
NOT part of `checks` (an organic news cycle must not turn the release gate
red). Exit code is the number of MAJOR drifts, so schedulers can still alarm.

Run:  python -m fli.cli drift [--db PATH] [--days N]
"""
from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from pathlib import Path

from fli import storage

# Conventional PSI bands (banking/scorecard practice; kept standard on purpose
# so the numbers are comparable to the literature rather than house-tuned).
PSI_MODERATE = 0.10
PSI_MAJOR = 0.25
KS_ALPHA_COEF = 1.358          # c(alpha) for alpha=0.05
_EPS = 1e-4                    # empty-bin smoothing for PSI


def psi(ref_counts: dict[str, int], cur_counts: dict[str, int]) -> float:
    """PSI between two categorical count distributions. Bins present in one
    side only are smoothed to _EPS rather than dropped — appearance of a new
    category IS drift and must register, not divide by zero."""
    cats = sorted(set(ref_counts) | set(cur_counts))
    ref_n = sum(ref_counts.values()) or 1
    cur_n = sum(cur_counts.values()) or 1
    total = 0.0
    for c in cats:
        p = max(ref_counts.get(c, 0) / ref_n, _EPS)
        q = max(cur_counts.get(c, 0) / cur_n, _EPS)
        total += (q - p) * math.log(q / p)
    return total


def ks(ref: list[float], cur: list[float]) -> tuple[float, float]:
    """Two-sample KS statistic and its alpha=0.05 critical value
    (1.358 * sqrt((n+m)/(n*m))). Computed directly — no scipy dependency."""
    if not ref or not cur:
        return 0.0, float("inf")
    ref_s, cur_s = sorted(ref), sorted(cur)
    n, m = len(ref_s), len(cur_s)
    i = j = 0
    stat = 0.0
    while i < n and j < m:
        # advance both sides past the smaller value ENTIRELY (ties included)
        # before measuring, else tied samples register a spurious gap
        v = min(ref_s[i], cur_s[j])
        while i < n and ref_s[i] <= v:
            i += 1
        while j < m and cur_s[j] <= v:
            j += 1
        stat = max(stat, abs(i / n - j / m))
    crit = KS_ALPHA_COEF * math.sqrt((n + m) / (n * m))
    return stat, crit


def _psi_verdict(value: float) -> str:
    if value >= PSI_MAJOR:
        return "MAJOR"
    if value >= PSI_MODERATE:
        return "moderate"
    return "stable"


def _cutoff(conn: sqlite3.Connection, days: int) -> str | None:
    """Window boundary anchored to the newest document, not the wall clock."""
    row = conn.execute(
        "SELECT date(max(published_at), ?) FROM raw_documents"
        " WHERE published_at IS NOT NULL", (f"-{days} days",)).fetchone()
    return row[0]


def _counts(conn, sql: str, cutoff: str, current: bool) -> dict[str, int]:
    op = ">=" if current else "<"
    return {r[0]: r[1] for r in conn.execute(sql.format(op=op), (cutoff,))}


def _values(conn, sql: str, cutoff: str, current: bool) -> list[float]:
    op = ">=" if current else "<"
    return [r[0] for r in conn.execute(sql.format(op=op), (cutoff,))]


_DOC_MIX = ("SELECT source_type, count(1) FROM raw_documents"
            " WHERE published_at IS NOT NULL AND published_at {op} ?"
            " GROUP BY source_type")
_EVENT_MIX = ("SELECT i.event_type, count(1) FROM insights i"
              " JOIN evidence e ON e.id = i.evidence_id"
              " JOIN raw_documents d ON d.id = e.document_id"
              " WHERE d.published_at IS NOT NULL AND d.published_at {op} ?"
              " GROUP BY i.event_type")
_DOC_LEN = ("SELECT length(raw_content) FROM raw_documents"
            " WHERE published_at IS NOT NULL AND published_at {op} ?")
_SCORE = ("SELECT i.score FROM insights i"
          " JOIN evidence e ON e.id = i.evidence_id"
          " JOIN raw_documents d ON d.id = e.document_id"
          " WHERE i.score IS NOT NULL"
          " AND d.published_at IS NOT NULL AND d.published_at {op} ?")


def build(conn: sqlite3.Connection, days: int = 14) -> list[dict]:
    """One row per metric: name, kind, value, threshold, verdict, n_ref, n_cur.
    Empty on a corpus too young to have a reference period."""
    cutoff = _cutoff(conn, days)
    if cutoff is None:
        return []
    rows = []
    for name, sql in (("doc source_type mix", _DOC_MIX),
                      ("insight event_type mix", _EVENT_MIX)):
        ref = _counts(conn, sql, cutoff, current=False)
        cur = _counts(conn, sql, cutoff, current=True)
        if not ref or not cur:
            continue
        value = psi(ref, cur)
        rows.append({"metric": name, "kind": "PSI", "value": value,
                     "threshold": PSI_MAJOR, "verdict": _psi_verdict(value),
                     "n_ref": sum(ref.values()), "n_cur": sum(cur.values())})
    for name, sql in (("doc length", _DOC_LEN),
                      ("insight score", _SCORE)):
        ref = _values(conn, sql, cutoff, current=False)
        cur = _values(conn, sql, cutoff, current=True)
        if not ref or not cur:
            continue
        stat, crit = ks(ref, cur)
        rows.append({"metric": name, "kind": "KS", "value": stat,
                     "threshold": crit,
                     "verdict": "MAJOR" if stat >= crit else "stable",
                     "n_ref": len(ref), "n_cur": len(cur)})
    return rows


def report(conn: sqlite3.Connection, days: int = 14) -> int:
    """Print the drift table; returns the number of MAJOR drifts."""
    cutoff = _cutoff(conn, days)
    rows = build(conn, days)
    if not rows:
        print("drift: corpus has no reference period yet — nothing to compare")
        return 0
    print(f"=== corpus drift: last {days} days (from {cutoff}, "
          f"anchored to newest doc) vs history ===")
    print(f"  {'metric':<24} {'kind':<4} {'value':>7} {'threshold':>9} "
          f"{'n_ref':>6} {'n_cur':>6}  verdict")
    for r in rows:
        print(f"  {r['metric']:<24} {r['kind']:<4} {r['value']:>7.3f} "
              f"{r['threshold']:>9.3f} {r['n_ref']:>6} {r['n_cur']:>6}  "
              f"{r['verdict']}")
    major = sum(1 for r in rows if r["verdict"] == "MAJOR")
    print(f"drift: {major} MAJOR of {len(rows)} metrics "
          f"(PSI bands 0.10/0.25; KS at alpha=0.05)")
    return major


def main() -> int:
    ap = argparse.ArgumentParser(
        description="PSI/KS corpus drift: current window vs history.")
    ap.add_argument("--db", default=str(storage.DEFAULT_DB))
    ap.add_argument("--days", type=int, default=14,
                    help="current-window size, anchored to the newest document")
    args = ap.parse_args()
    conn = storage.connect(Path(args.db))
    try:
        return report(conn, days=args.days)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
