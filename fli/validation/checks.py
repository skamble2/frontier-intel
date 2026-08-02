"""Validation battery. """
from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from datetime import date
from pathlib import Path

from fli import storage
from fli.core.text import contains_verbatim, html_to_text, page_published
from fli.core.policy import PolicyError, describe, load_policy
from fli.knowledge.register import valid_candidate_name
from fli.knowledge.register.x_identities import TIER_FOR_METHOD

DATE_DRIFT_MAX_DAYS = 14


def check(name: str, failures: list[str], all_failures: list[str]) -> None:
    if failures:
        all_failures.extend(f"{name}: {f}" for f in failures)
        print(f"FAIL {name} ({len(failures)})")
        for f in failures[:5]:
            print(f"     - {f}")
    else:
        print(f"PASS {name}")


def evidence_reverifies(conn: sqlite3.Connection, ev_row: sqlite3.Row) -> bool:
    """Re-match an evidence row against its stored document. """
    doc = conn.execute("SELECT raw_content FROM raw_documents WHERE id=?",
                       (ev_row["document_id"],)).fetchone()
    raw = doc["raw_content"]
    if ev_row["verification"] == "structural":
        return contains_verbatim(raw, ev_row["verbatim_content"])
    return (contains_verbatim(raw, ev_row["verbatim_content"])
            or contains_verbatim(html_to_text(raw), ev_row["verbatim_content"]))


def policy_attribution_failures(conn: sqlite3.Connection, policy) -> list[str]:
    """C17 — every ranking must trace back to an editorial policy version."""
    failures = []
    for r in conn.execute("SELECT DISTINCT policy_version v FROM event_scores"):
        if r["v"] != policy.version:
            failures.append(
                f"event_scores cites policy v{r['v']}, but config/policy.yml is "
                f"v{policy.version} (re-run the bake-off, or restore that version)")
    unranked = [r["t"] for r in conn.execute(
        "SELECT DISTINCT event_type t FROM insights WHERE event_type IS NOT NULL")
        if r["t"] not in policy.event_type_prior]
    if unranked:
        failures.append(
            f"event type(s) {sorted(unranked)} present in insights but absent "
            f"from policy.event_type_prior (they default to lowest rank — rank "
            f"them, or record the omission deliberately)")
    return failures


def run(conn: sqlite3.Connection) -> int:
    all_failures: list[str] = []
    bad = [f"doc {r['id']}: hash mismatch" for r in
           conn.execute("SELECT id, content_hash, raw_content FROM raw_documents")
           if hashlib.sha256(r["raw_content"].encode()).hexdigest() != r["content_hash"]]
    check("C1 document immutability", bad, all_failures)

    bad = [f"evidence {r['id']}: no longer matches doc {r['document_id']}"
           for r in conn.execute("SELECT * FROM evidence")
           if not evidence_reverifies(conn, r)]
    check("C2 evidence re-verifies", bad, all_failures)

    bad = [f"person {r['id']} ({r['canonical_name']}): no identity"
           for r in conn.execute(
               "SELECT p.id, p.canonical_name FROM people p"
               " LEFT JOIN identities i ON i.person_id = p.id"
               " WHERE i.id IS NULL")]
    check("C3 people are evidenced", bad, all_failures)

    bad = []
    for r in conn.execute("SELECT * FROM identities"):
        if r["confidence_tier"] not in TIER_FOR_METHOD[r["resolution_method"]]:
            bad.append(f"identity {r['id']}: tier {r['confidence_tier']}"
                       f" inconsistent with method {r['resolution_method']}")
        if r["evidence_id"] is None:
            bad.append(f"identity {r['id']} ({r['handle']}): no evidence")
    check("C4 identity hygiene", bad, all_failures)

    bad = []
    for r in conn.execute(
            "SELECT a.id, a.observed_at, e.id ev_id, e.document_id, e.verification,"
            " e.verbatim_content FROM affiliations a JOIN evidence e ON e.id = a.evidence_id"):
        if not r["observed_at"]:
            bad.append(f"affiliation {r['id']}: no observed_at")
        elif not evidence_reverifies(conn, r):
            bad.append(f"affiliation {r['id']}: evidence {r['ev_id']} fails re-verify")
    check("C5 affiliations evidenced", bad, all_failures)

    bad = [f"insight {r['id']}: evidence {r['evidence_id']} missing" for r in
           conn.execute("SELECT i.id, i.evidence_id FROM insights i"
                        " LEFT JOIN evidence e ON e.id = i.evidence_id"
                        " WHERE e.id IS NULL")]
    check("C6 insights evidenced", bad, all_failures)

    bad = [f"source {r['id']} ({r['name']}): never fetched" for r in
           conn.execute("SELECT s.id, s.name FROM sources s"
                        " LEFT JOIN fetch_log f ON f.source_id = s.id"
                        " WHERE f.id IS NULL")]
    check("C7 fetch coverage", bad, all_failures)

    bad = [f"candidate {r['id']} ({r['name']}): evidence fails re-verify" for r in
           conn.execute("SELECT c.id, c.name, e.document_id, e.verification,"
                        " e.verbatim_content FROM person_candidates c"
                        " JOIN evidence e ON e.id = c.evidence_id")
           if not evidence_reverifies(conn, r)]
    check("C8 candidates evidenced", bad, all_failures)

    bad = [f"person {r['id']}: invalid name {r['canonical_name']!r}" for r in
           conn.execute("SELECT id, canonical_name FROM people")
           if not valid_candidate_name(r["canonical_name"])]
    check("C9 people pass name hygiene", bad, all_failures)

    bad = [f"evidence {r['id']}: referenced by nothing" for r in conn.execute(
        "SELECT e.id FROM evidence e"
        " LEFT JOIN insights i ON i.evidence_id = e.id"
        " LEFT JOIN identities n ON n.evidence_id = e.id"
        " LEFT JOIN affiliations a ON a.evidence_id = e.id"
        " LEFT JOIN person_candidates c ON c.evidence_id = e.id"
        " LEFT JOIN event_entities ee ON ee.evidence_id = e.id"
        " WHERE i.id IS NULL AND n.id IS NULL AND a.id IS NULL"
        " AND c.id IS NULL AND ee.id IS NULL")]
    check("C10 no orphan evidence", bad, all_failures)

    bad = [f"event_entity {r['id']} (event {r['event_id']}): evidence fails re-verify"
           for r in conn.execute(
               "SELECT ee.id, ee.event_id, e.document_id, e.verification,"
               " e.verbatim_content FROM event_entities ee"
               " JOIN evidence e ON e.id = ee.evidence_id")
           if not evidence_reverifies(conn, r)]
    check("C11 event entities cited", bad, all_failures)

    bad = [f"event_entity {r['id']}: source_inferred but channel {r['channel']!r}"
           for r in conn.execute(
               "SELECT ee.id, s.channel FROM event_entities ee"
               " JOIN evidence e ON e.id = ee.evidence_id"
               " JOIN raw_documents d ON d.id = e.document_id"
               " JOIN sources s ON s.id = d.source_id"
               " WHERE ee.basis = 'source_inferred'")
           if r["channel"] != "official"]
    check("C12 inferred basis is official", bad, all_failures)

    print("\nregister balance (candidates / approved / insights per lab):")
    from fli.knowledge.register import balance_by_lab
    for lab, d in sorted(balance_by_lab(conn).items(), key=lambda x: -x[1]["insights"]):
        print(f"  {lab:<16} cand={d['candidates']:<4} approved={d['approved']:<3}"
              f" insights={d['insights']}")

    cc = conn.execute("SELECT count(cluster_id) c, count(*) t FROM insights").fetchone()
    bad = []
    if 0 < cc["c"] < cc["t"]:
        bad.append(f"partial clustering: {cc['c']}/{cc['t']} have cluster_id")
    bad += [f"cluster {r['cluster_id']} spans >1 event_type" for r in conn.execute(
        "SELECT cluster_id FROM insights WHERE cluster_id IS NOT NULL"
        " GROUP BY cluster_id HAVING count(DISTINCT event_type) > 1")]
    check("C14 clusters well-formed", bad, all_failures)

    bad = [f"scored event {r['event_id']} has no features" for r in conn.execute(
        "SELECT DISTINCT es.event_id FROM event_scores es"
        " LEFT JOIN insight_features f ON f.event_id = es.event_id"
        " WHERE f.event_id IS NULL")]
    check("C15 scored events have features", bad, all_failures)

    union = conn.execute("SELECT count(DISTINCT event_id) c FROM event_scores").fetchone()["c"]
    bad = [f"model {r['model']} scores {r['c']} events != {union}" for r in conn.execute(
        "SELECT model, count(DISTINCT event_id) c FROM event_scores GROUP BY model")
        if r["c"] != union]
    check("C16 bake-off covers identical set", bad, all_failures)

    policy = None
    try:
        policy = load_policy()
    except PolicyError as e:
        bad = [f"config/policy.yml unreadable: {e}"]
    else:
        bad = policy_attribution_failures(conn, policy)
    check("C17 scores cite a known policy", bad, all_failures)

    bad = []
    for r in conn.execute(
            "SELECT id, url, published_at, raw_content FROM raw_documents"
            " WHERE published_at IS NOT NULL AND raw_content LIKE '%<h%'"):
        own = page_published(r["raw_content"])
        if not own:
            continue
        try:
            drift = abs((date.fromisoformat(r["published_at"][:10])
                         - date.fromisoformat(own)).days)
        except ValueError:
            continue
        if drift > DATE_DRIFT_MAX_DAYS:
            bad.append(f"doc {r['id']}: stored {r['published_at'][:10]} but the"
                       f" page says {own} ({drift}d apart) {r['url']}")
    check("C18 dates are the page's own", bad, all_failures)

    bad = [f"insight {r['id']}: evidence {r['ev_id']} is {r['verification']!r},"
           f" not 'exact'" for r in conn.execute(
               "SELECT i.id, e.id ev_id, e.verification FROM insights i"
               " JOIN evidence e ON e.id = i.evidence_id"
               " WHERE e.verification != 'exact'")]
    check("C19 insights quote at exact tier", bad, all_failures)

    import json as _json
    bad = []
    for r in conn.execute("SELECT name, seed_lab_ids FROM person_candidates"):
        labs_n = len(set(_json.loads(r["seed_lab_ids"] or "[]")))
        if labs_n > 2:
            bad.append(f"candidate {r['name']!r} seeded from {labs_n} labs —"
                       f" likely {labs_n} people merged under one name")
    for r in conn.execute(
            "SELECT p.canonical_name, count(DISTINCT a.lab_id) labs_n"
            " FROM affiliations a JOIN people p ON p.id = a.person_id"
            " WHERE a.lab_id IS NOT NULL"
            "   AND julianday(a.observed_at) >= julianday('now') - 90"
            " GROUP BY a.person_id HAVING labs_n > 2"):
        bad.append(f"person {r['canonical_name']!r} observed at {r['labs_n']}"
                   f" labs inside 90 days — homonym merge, not mobility")
    check("C20 one name, one person", bad, all_failures)

    if policy is not None:
        print(f"\n{describe(policy)}")
        if not policy.is_owned:
            print("  WARNING: policy has no domain owner — every weight in it is "
                  "provisional and must be reported as unvalidated.")

    print("\nidentity resolution (counts):")
    for r in conn.execute(
            "SELECT platform, confidence_tier, count(*) c"
            " FROM identities GROUP BY 1,2 ORDER BY 1,2"):
        print(f"  {r['platform']}/{r['confidence_tier']}: {r['c']}")
    for r in conn.execute("SELECT status, count(*) c FROM person_candidates GROUP BY 1"):
        print(f"  queue {r['status']}: {r['c']}")

    print(f"\n{'GREEN' if not all_failures else 'RED'} — {len(all_failures)} failure(s)")
    return 1 if all_failures else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(storage.DEFAULT_DB))
    args = ap.parse_args()
    conn = storage.connect(Path(args.db))
    storage.init_db(conn)
    sys.exit(run(conn))


if __name__ == "__main__":
    main()
