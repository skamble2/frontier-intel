"""Affiliation currency: re-observe who is where, at most once per
person/lab/day, so a stale affiliation is visibly stale rather than
silently wrong."""
import json
import sqlite3

from fli import storage
from fli.core.text import contains_verbatim
from fli.knowledge.register.seeding import (LAB_PAGES, PERSON_PAGES,
                                            _fetch_pages)


def observe(conn: sqlite3.Connection) -> None:
    """Re-verify each EXISTING (person, lab) affiliation against a fresh
    fetch of that lab's pages, appending a new dated observation on success.
    Strictly re-observation: a name appearing on some other lab's page is a
    mention, not an affiliation, and never creates a link here — new links
    come only from seeding or candidate approval. One observation per
    person/lab per day; failures to re-verify are reported, not deleted."""
    today = storage.now_utc()[:10]
    # only page-verbatim links are page-re-verifiable; inferred links are
    # re-examined when new corroborating evidence arrives, not by page scan
    pairs = conn.execute(
        "SELECT DISTINCT a.person_id, a.lab_id, p.canonical_name, l.name AS lab_name"
        " FROM affiliations a JOIN people p ON p.id=a.person_id"
        " JOIN labs l ON l.id=a.lab_id WHERE a.lab_id IS NOT NULL"
        " AND a.basis='page_verbatim'").fetchall()
    pages_by_lab: dict[str, list] = {}
    observed = failed = unlisted = 0
    for pair in pairs:
        lab_name = pair["lab_name"]
        if lab_name not in pages_by_lab:
            pages_by_lab[lab_name] = _fetch_pages(
                conn, LAB_PAGES[lab_name], f"{lab_name} page", lab_id=pair["lab_id"])
        candidates = list(pages_by_lab[lab_name])
        if pair["canonical_name"] in PERSON_PAGES and not any(
                contains_verbatim(t, pair["canonical_name"])
                for _, _, t in candidates):
            candidates += _fetch_pages(
                conn, PERSON_PAGES[pair["canonical_name"]],
                f"{pair['canonical_name']} page", lab_id=pair["lab_id"])
        hit = next(((d, u, t) for d, u, t in candidates
                    if contains_verbatim(t, pair["canonical_name"])), None)
        if hit is None:
            # Two very different states, and conflating them manufactures
            # attrition. A person registered from an X bio was NEVER on a lab
            # page, so failing to find them there is the expected result, not
            # a departure. Only the second case is a candidate mobility signal.
            never_on_a_page = not conn.execute(
                "SELECT 1 FROM identities WHERE person_id=? AND platform='lab_page'",
                (pair["person_id"],)).fetchone()
            if never_on_a_page:
                unlisted += 1
                print(f"  n/a  {pair['canonical_name']} @ {lab_name}:"
                      f" not a lab-page registration (X profile); nothing to re-verify")
            else:
                failed += 1
                print(f"  MISS {pair['canonical_name']} @ {lab_name}:"
                      f" was on a lab page, no longer verbatim — CHECK FOR A MOVE")
            continue
        if conn.execute(
                "SELECT 1 FROM affiliations WHERE person_id=? AND lab_id=?"
                " AND substr(observed_at,1,10)=?",
                (pair["person_id"], pair["lab_id"], today)).fetchone():
            continue
        doc_id, _, _ = hit
        ev = storage.insert_evidence(
            conn, doc_id,
            json.dumps({"kind": "page_text", "match": pair["canonical_name"]}),
            pair["canonical_name"], "exact", 1.0)
        conn.execute(
            "INSERT INTO affiliations (person_id, lab_id, role, observed_at,"
            " evidence_id) VALUES (?,?,?,?,?)",
            (pair["person_id"], pair["lab_id"], None, storage.now_utc(), ev))
        observed += 1
    conn.commit()
    print(f"re-observed: {observed} affiliations; failed to re-verify: {failed}"
          f" (candidate moves); not lab-page registrations: {unlisted}")
