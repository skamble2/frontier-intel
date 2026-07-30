"""The push path: the few things that should not wait for the digest.

An alert is a different product from a digest and the difference is entirely
about cost. A digest is opened when the reader chooses to; an alert interrupts.
So the bar is not "interesting" — the digest already carries everything
interesting — it is "a reader would want to know before the next report".

WHAT FIRES, AND WHY IT IS NOT THE SCORE. The obvious rule is a score
threshold, and it is wrong here. Measured on the corpus, the two events that
carry a signed reading score -0.02 and 1.13 against a p90 of 1.59: OpenAI
launching Health in ChatGPT — the one event in 734 that the deterministic layer
calls a threat to a named holding — sits near the MIDDLE of the ranking,
because the rubric rewards specificity and shipped-ness, not portfolio
consequence. A p90 alert rule would have missed it and fired on ten model
releases instead.

So the trigger is the READING, not the rank:

  signed_position  a deterministic event->holding edge whose direction is not
                   `unclear`. That requires a classifier-established mechanism
                   plus a holding the mechanism can actually move, which 2 of
                   59 edges satisfy.
  signed_reading   a persona hypothesis that commits to a direction (threat,
                   tailwind, adopt) at medium or better confidence. Low
                   confidence is excluded: it means the reader themself flagged
                   the evidence as thin, and interrupting on it is how a channel
                   gets muted.

Both are additionally bounded by the reporting period — a 2024 post is not
news — and by the `alerts` table, whose UNIQUE constraint means an alert can
fire exactly once. A channel that repeats itself every run trains its reader to
ignore it, so this is enforced in the schema rather than left to the caller.

Over the whole corpus these rules fire 3 times. That is the intended order of
magnitude: rare enough to be read.

DELIVERY IS PLUGGABLE AND STDOUT IS A REAL SINK. `SINKS` maps a name to a
function; a Slack webhook or an email relay is a few lines each. None is
written because a delivery target that nobody receives is not evidence the
path works — the recorded row and the printed line are.

Free and deterministic: no LLM call, no network.

Run:  python3 -m fli.cli alerts --days 7 --dry-run
      python3 -m fli.cli alerts --days 7
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from fli import storage

# Directions that commit to an action, per persona. `unclear`, `monitor` and
# `investigate` are honest answers but they are not news.
ACTIONABLE = {"investment": ("threat", "tailwind"), "ai_team": ("adopt",)}
MIN_CONFIDENCE = ("high", "medium")


def _sink_stdout(alert: dict) -> None:
    print(f"\n  ALERT  [{alert['rule']}] {alert['persona']}"
          f"  event {alert['event_id']}")
    print(f"    {alert['reason']}")
    print(f"    {alert['claim'][:150]}")
    print(f"    {alert['url']}")


def _sink_null(alert: dict) -> None:
    """For tests and dry runs: the alert is formed and recorded, not shown."""


SINKS = {"stdout": _sink_stdout, "null": _sink_null}


def candidates(conn, days: int = 7) -> list[dict]:
    """Everything the rules select, whether or not it has already fired."""
    out: list[dict] = []

    # ONE EVENT IS ONE ALERT, even when it moves several holdings. The Health
    # launch signs an edge to both HNGE and OSCR; delivering that twice is two
    # interruptions carrying one fact, and the UNIQUE key on `alerts` would
    # have recorded only the first — so the send would have been noisier than
    # the record, which is the wrong way round.
    by_event: dict[int, dict] = {}
    for r in conn.execute(
            "SELECT ep.event_id, ep.ticker, ep.direction, ep.channel,"
            " i.claim, d.url, substr(d.published_at,1,10) day"
            " FROM event_positions ep"
            " JOIN insights i ON i.id = ep.event_id"
            " JOIN evidence ev ON ev.id = i.evidence_id"
            " JOIN raw_documents d ON d.id = ev.document_id"
            " WHERE ep.direction <> 'unclear'"
            "   AND d.published_at IS NOT NULL"
            "   AND julianday('now') - julianday(d.published_at) <= ?"
            " ORDER BY ep.event_id, ep.ticker", (days,)):
        a = by_event.setdefault(r["event_id"], {
            "event_id": r["event_id"], "persona": "investment",
            "rule": "signed_position", "_hits": [],
            "claim": r["claim"], "url": r["url"], "_day": r["day"]})
        a["_hits"].append(f"{r['ticker']} {r['direction']} via {r['channel']}")
    for a in by_event.values():
        a["reason"] = (f"{'; '.join(a.pop('_hits'))} ({a.pop('_day')}) — "
                       f"mechanism established by the channel classifier, not "
                       f"a keyword match.")
        out.append(a)

    for persona, dirs in ACTIONABLE.items():
        qs = ",".join("?" * len(dirs))
        cs = ",".join("?" * len(MIN_CONFIDENCE))
        for r in conn.execute(
                f"SELECT h.insight_id event_id, h.direction, h.confidence,"
                f" h.time_horizon, h.hypothesis, i.claim, d.url,"
                f" substr(d.published_at,1,10) day"
                f" FROM hypotheses h"
                f" JOIN insights i ON i.id = h.insight_id"
                f" JOIN evidence ev ON ev.id = i.evidence_id"
                f" JOIN raw_documents d ON d.id = ev.document_id"
                f" WHERE h.persona = ? AND h.direction IN ({qs})"
                f"   AND h.confidence IN ({cs})"
                f"   AND d.published_at IS NOT NULL"
                f"   AND julianday('now') - julianday(d.published_at) <= ?"
                f" ORDER BY h.insight_id",
                (persona, *dirs, *MIN_CONFIDENCE, days)):
            out.append({
                "event_id": r["event_id"], "persona": persona,
                "rule": "signed_reading",
                "reason": (f"{r['direction']} ({r['confidence']} confidence, "
                           f"{r['time_horizon']}) — {r['hypothesis']}"),
                "claim": r["claim"], "url": r["url"]})
    return out


def already_fired(conn) -> set[tuple[int, str, str]]:
    return {(r["event_id"], r["persona"], r["rule"])
            for r in conn.execute(
                "SELECT event_id, persona, rule FROM alerts")}


def run(conn: sqlite3.Connection, days: int = 7, sink: str = "stdout",
        dry_run: bool = False, verbose: bool = True) -> dict:
    fired = already_fired(conn)
    cands = candidates(conn, days)
    fresh = [a for a in cands
             if (a["event_id"], a["persona"], a["rule"]) not in fired]

    if verbose:
        print(f"alerts — {len(cands)} candidate(s) in the last {days} days, "
              f"{len(fresh)} not yet raised")
    if dry_run:
        for a in fresh:
            _sink_stdout(a)
        if verbose:
            print("\n  dry run: nothing recorded, nothing delivered.")
        return {"candidates": len(cands), "fired": 0, "suppressed":
                len(cands) - len(fresh)}

    deliver = SINKS[sink]
    ts = storage.now_utc()
    for a in fresh:
        deliver(a)
        conn.execute(
            "INSERT OR IGNORE INTO alerts (event_id, persona, rule, reason,"
            " fired_at, delivered_via) VALUES (?,?,?,?,?,?)",
            (a["event_id"], a["persona"], a["rule"], a["reason"], ts, sink))
    conn.commit()
    if verbose and not fresh:
        print("  nothing new — every candidate has already been raised once.")
    return {"candidates": len(cands), "fired": len(fresh),
            "suppressed": len(cands) - len(fresh)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(storage.DEFAULT_DB))
    ap.add_argument("--days", type=int, default=7,
                    help="how recent an event must be to be worth pushing")
    ap.add_argument("--sink", choices=list(SINKS), default="stdout")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would fire; record and deliver nothing")
    args = ap.parse_args()
    conn = storage.connect(Path(args.db))
    storage.init_db(conn)
    run(conn, days=args.days, sink=args.sink, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
