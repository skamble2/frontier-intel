"""The push path: the few things that should not wait for the digest."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import urllib.request
from pathlib import Path

from fli import storage

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


def _sink_slack(alert: dict) -> None:
    """Post one alert to a Slack incoming webhook."""
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        raise RuntimeError("sink 'slack' needs SLACK_WEBHOOK_URL set")
    text = (f":rotating_light: *[{alert['rule']}]* {alert['persona']} — "
            f"event {alert['event_id']}\n{alert['reason']}\n"
            f"> {alert['claim'][:300]}\n{alert['url']}")
    req = urllib.request.Request(
        url, data=json.dumps({"text": text}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        if resp.status != 200:
            raise RuntimeError(f"slack webhook returned {resp.status}")


SINKS = {"stdout": _sink_stdout, "null": _sink_null, "slack": _sink_slack}


def candidates(conn, days: int = 7) -> list[dict]:
    """Everything the rules select, whether or not it has already fired."""
    out: list[dict] = []

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
