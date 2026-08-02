"""Mobility synthesis: turn affiliation history into personnel events."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from fli import storage
from fli.core.policy import load_policy

LOCATOR_KIND = "mobility_synthesis"


def _days_between(earlier: str, later: str) -> float:
    fmt = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))
    return (fmt(later) - fmt(earlier)).total_seconds() / 86400


def _already_synthesized(conn, person_id: int, from_lab: int, to_lab: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM insights i JOIN evidence e ON e.id = i.evidence_id"
        " WHERE i.event_type='personnel' AND e.locator LIKE ?"
        " AND json_extract(e.locator,'$.person_id')=?"
        " AND json_extract(e.locator,'$.from_lab_id')=?"
        " AND json_extract(e.locator,'$.to_lab_id')=?",
        (f'%{LOCATOR_KIND}%', person_id, from_lab, to_lab)).fetchone() is not None


def detect_mobility_events(conn: sqlite3.Connection,
                           window_days: int | None = None) -> dict:
    """Scan affiliation history and synthesize one `personnel` insight per
    detected move, with mover_from / mover_to lab entities."""
    if window_days is None:
        window_days = load_policy().mobility_window_days
    rows = conn.execute(
        "SELECT a.person_id, a.lab_id, p.canonical_name, l.name lab_name,"
        " MIN(a.observed_at) first_seen, MAX(a.observed_at) last_seen,"
        " (SELECT a2.evidence_id FROM affiliations a2 WHERE a2.person_id=a.person_id"
        "  AND a2.lab_id=a.lab_id AND a2.basis='page_verbatim'"
        "  ORDER BY a2.observed_at DESC LIMIT 1) evidence_id"
        " FROM affiliations a JOIN people p ON p.id=a.person_id"
        " JOIN labs l ON l.id=a.lab_id"
        " WHERE a.lab_id IS NOT NULL AND a.basis='page_verbatim'"
        " GROUP BY a.person_id, a.lab_id ORDER BY a.person_id, first_seen").fetchall()

    by_person: dict[int, list] = {}
    for r in rows:
        by_person.setdefault(r["person_id"], []).append(r)

    created = skipped_overlap = skipped_window = skipped_existing = 0
    for stints in by_person.values():
        if len(stints) < 2:
            continue
        for prev, nxt in zip(stints, stints[1:]):
            if nxt["first_seen"] <= prev["last_seen"]:
                skipped_overlap += 1
                continue
            gap = _days_between(prev["last_seen"], nxt["first_seen"])
            if gap > window_days:
                skipped_window += 1
                continue
            if _already_synthesized(conn, prev["person_id"], prev["lab_id"],
                                    nxt["lab_id"]):
                skipped_existing += 1
                continue
            created += 1
            _synthesize(conn, prev, nxt)
    conn.commit()
    summary = {"created": created, "skipped_existing": skipped_existing,
               "skipped_overlap": skipped_overlap,
               "skipped_window": skipped_window}
    print(f"mobility: {created} move(s) synthesized"
          f" ({skipped_existing} already known, {skipped_overlap} concurrent,"
          f" {skipped_window} outside {window_days}d pairing window)")
    return summary


def _synthesize(conn, prev: sqlite3.Row, nxt: sqlite3.Row) -> None:
    """One personnel insight for the move prev.lab -> nxt.lab."""
    doc_id = conn.execute("SELECT document_id FROM evidence WHERE id=?",
                          (nxt["evidence_id"],)).fetchone()["document_id"]
    locator = json.dumps({
        "kind": LOCATOR_KIND,
        "person_id": prev["person_id"],
        "from_lab_id": prev["lab_id"], "to_lab_id": nxt["lab_id"],
        "from_last_observed": prev["last_seen"],
        "to_first_observed": nxt["first_seen"],
    })
    ev = storage.insert_evidence(conn, doc_id, locator, nxt["canonical_name"],
                                 "exact", 1.0)
    claim = (f"{nxt['canonical_name']} moved from {prev['lab_name']} to"
             f" {nxt['lab_name']}: last observed at {prev['lab_name']}"
             f" {prev['last_seen'][:10]}, first observed at {nxt['lab_name']}"
             f" {nxt['first_seen'][:10]}.")
    event_id = storage.insert_insight(
        conn, ev, "personnel", claim,
        attributed_lab_id=nxt["lab_id"],
        attributed_person_id=prev["person_id"],
        basis="source_inferred")
    storage.insert_event_entity(conn, event_id, "lab", prev["lab_id"],
                                "mover_from", prev["evidence_id"],
                                basis="source_inferred", commit=False)
    storage.insert_event_entity(conn, event_id, "lab", nxt["lab_id"],
                                "mover_to", ev,
                                basis="source_inferred", commit=False)
    print(f"  MOVE {nxt['canonical_name']}: {prev['lab_name']}"
          f" -> {nxt['lab_name']} (arrival observed {nxt['first_seen'][:10]})")
