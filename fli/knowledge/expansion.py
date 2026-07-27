"""Co-author expansion: discover candidate people from seeds' arXiv papers.

For each seed person, their recent arXiv papers are fetched and stored.
A paper counts only if corroborated — another tracked person or a lab
collective author appears on it; bare name matches (homonym risk) get a
low-tier identity and produce no candidates. Discovered co-authors land in
the approval queue; promotion is manual (fli.register approve).

Idempotent: re-runs never double-count papers or duplicate identities.

Run:  python -m fli.cli expand [--db PATH]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fli import storage
from fli.core.text import name_key
from fli.core.http import FetchError, http_get
from fli.knowledge.register import valid_candidate_name
from fli.core.config import ARXIV_DELAY_S, EXPANSION_WINDOW_DAYS

ARXIV_API = "https://export.arxiv.org/api/query"

ATOM = "{http://www.w3.org/2005/Atom}"

# Collective author strings labs publish under; presence on a paper is
# structural evidence that it is a lab paper.
LAB_COLLECTIVE_AUTHORS = {
    "DeepSeek-AI": "DeepSeek",
    "Qwen Team": "Qwen",
    "Gemini Team": "Google DeepMind",
    "Llama Team": "Meta AI",
    "Mistral AI": "Mistral",
}


def _author_query(name: str) -> str:
    q = urllib.parse.urlencode({
        "search_query": f'au:"{name}"',
        "sortBy": "submittedDate", "sortOrder": "descending",
        "max_results": 25})
    return f"{ARXIV_API}?{q}"


def _parse_entries(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    return [{
        "id": e.findtext(f"{ATOM}id", ""),
        "published": e.findtext(f"{ATOM}published", ""),
        "title": " ".join((e.findtext(f"{ATOM}title") or "").split()),
        "authors": [a.findtext(f"{ATOM}name", "")
                    for a in e.findall(f"{ATOM}author")],
    } for e in root.findall(f"{ATOM}entry")]


def _record_identity(conn, seed, doc_id: int, corroborated, matched) -> None:
    if conn.execute("SELECT 1 FROM identities WHERE platform='arxiv' AND handle=?",
                    (seed["canonical_name"],)).fetchone():
        return
    strong = bool(corroborated)
    anchor = (corroborated or matched)[0]
    ev = storage.insert_evidence(
        conn, doc_id,
        json.dumps({"entry_id": anchor["id"], "section": "authors"}),
        seed["canonical_name"], "structural", None)
    conn.execute(
        "INSERT INTO identities (person_id, platform, handle, confidence_tier,"
        " resolution_method, evidence_id) VALUES (?,?,?,?,?,?)",
        (seed["id"], "arxiv", seed["canonical_name"],
         "corroborated" if strong else "name_match_only",
         "coauthor_overlap" if strong else "exact", ev))
    conn.commit()


def _queue_candidate(conn, author: str, entry: dict, seed_id: int,
                     seed_lab_id: int | None, lab_hint: str | None,
                     doc_id: int) -> bool:
    """Add or update one queue row. Returns True if the row is new. Accumulates
    every discovering seed and its lab (F2/F4 §22), so discovery is order-
    independent and a candidate is attributable to every lab it came through."""
    row = conn.execute("SELECT id, seed_person_ids, seed_lab_ids, entry_ids"
                       " FROM person_candidates WHERE name=?", (author,)).fetchone()
    if row:
        counted = json.loads(row["entry_ids"])
        if entry["id"] in counted:
            return False
        counted.append(entry["id"])
        seen = json.loads(row["seed_person_ids"])
        if seed_id not in seen:
            seen.append(seed_id)
        seen_labs = json.loads(row["seed_lab_ids"])
        if seed_lab_id and seed_lab_id not in seen_labs:
            seen_labs.append(seed_lab_id)
        conn.execute(
            "UPDATE person_candidates SET paper_count = paper_count + 1,"
            " entry_ids=?, seed_person_ids=?, seed_lab_ids=?, lab_hint=COALESCE(lab_hint, ?)"
            " WHERE id=?",
            (json.dumps(counted), json.dumps(seen), json.dumps(seen_labs),
             lab_hint, row["id"]))
        return False
    ev = storage.insert_evidence(
        conn, doc_id,
        json.dumps({"entry_id": entry["id"], "section": "authors"}),
        author, "structural", None)
    conn.execute(
        "INSERT INTO person_candidates (name, discovered_via, paper_count,"
        " entry_ids, seed_person_ids, seed_lab_ids, lab_hint, evidence_id,"
        " status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (author, "coauthor_expansion", 1, json.dumps([entry["id"]]),
         json.dumps([seed_id]), json.dumps([seed_lab_id] if seed_lab_id else []),
         lab_hint, ev, "pending", storage.now_utc()))
    return True


def expand_coauthors(conn: sqlite3.Connection) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=EXPANSION_WINDOW_DAYS)).isoformat()
    # F1 (§22): anchor expansion on RESEARCH seeds only. A founder co-signing a
    # broad institutional paper is an org signature, not a research collaboration,
    # and its huge author list swamps the queue with one lab's cluster. Founders
    # stay tracked entities; they are simply not co-authorship anchors.
    seeds = conn.execute(
        "SELECT id, canonical_name FROM people WHERE discovered_via='seed'"
        " AND seniority_tier IN ('research_lead','senior_ic')").fetchall()
    tracked = {name_key(r["canonical_name"]) for r in
               conn.execute("SELECT canonical_name FROM people").fetchall()}

    for i, seed in enumerate(seeds):
        if i:
            time.sleep(ARXIV_DELAY_S)
        url = _author_query(seed["canonical_name"])
        source_id = storage.upsert_source(
            conn, "arxiv", f'arXiv au:"{seed["canonical_name"]}"', url,
            purpose="register")
        try:
            xml_text, _ = http_get(url)
            entries = _parse_entries(xml_text)
        except (FetchError, ET.ParseError) as e:
            storage.log_fetch(conn, source_id, "error", detail=str(e))
            print(f"  ERR  {seed['canonical_name']}: {e}")
            continue

        recent = [e for e in entries if e["published"] >= cutoff]
        if not recent:
            storage.log_fetch(conn, source_id, "empty", items_found=0,
                              detail=f"no submissions in last {EXPANSION_WINDOW_DAYS}d")
            print(f"  none {seed['canonical_name']}: 0 papers in window")
            continue

        doc_id, _ = storage.store_document(conn, source_id, "arxiv", url, xml_text, None)
        storage.log_fetch(conn, source_id, "ok", items_found=len(recent),
                          detail=f"{len(recent)} papers in window")

        seed_key = name_key(seed["canonical_name"])
        matched = [e for e in recent
                   if any(name_key(a) == seed_key for a in e["authors"])]
        corroborated = [e for e in matched if any(
            (name_key(a) != seed_key and name_key(a) in tracked)
            or a in LAB_COLLECTIVE_AUTHORS
            for a in e["authors"])]
        if matched:
            _record_identity(conn, seed, doc_id, corroborated, matched)

        # the seed's own lab, so discovered candidates carry a per-lab tag (F2)
        seed_lab_row = conn.execute(
            "SELECT lab_id FROM affiliations WHERE person_id=? AND lab_id IS NOT NULL"
            " AND basis='page_verbatim' LIMIT 1", (seed["id"],)).fetchone()
        seed_lab_id = seed_lab_row["lab_id"] if seed_lab_row else None

        added = 0
        for entry in corroborated:
            hints = {LAB_COLLECTIVE_AUTHORS[a] for a in entry["authors"]
                     if a in LAB_COLLECTIVE_AUTHORS}
            lab_hint = hints.pop() if len(hints) == 1 else None
            for author in entry["authors"]:
                if (name_key(author) in tracked
                        or author in LAB_COLLECTIVE_AUTHORS
                        or not valid_candidate_name(author)):
                    continue
                if _queue_candidate(conn, author, entry, seed["id"], seed_lab_id,
                                    lab_hint, doc_id):
                    added += 1
        conn.commit()
        print(f"  ok   {seed['canonical_name']}: {len(matched)} name-matched,"
              f" {len(corroborated)} corroborated, {added} new candidates")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(storage.DEFAULT_DB))
    args = ap.parse_args()
    conn = storage.connect(Path(args.db))
    storage.init_db(conn)
    expand_coauthors(conn)


if __name__ == "__main__":
    main()
