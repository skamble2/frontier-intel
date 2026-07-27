"""Validation battery. Pure function of the database — no network, no LLM,
no randomness. Exit 0 = green.

C1  document immutability   sha256(raw_content) == content_hash
C2  evidence re-verifies    every evidence row re-matches its document
C3  people are evidenced    every person has an identity
C4  identity hygiene        tier/method consistent, evidence present
C5  affiliations evidenced  dated, and the evidence re-verifies
C6  insights evidenced      every insight's evidence row exists
C7  fetch coverage          every source has a fetch_log row
C8  candidates evidenced    every queue row's evidence re-verifies
C9  people name hygiene     no promoted person has a malformed name
C10 no orphan evidence      every evidence row is referenced by something
C11 event entities cited     every event_entities attribution re-verifies
C12 inferred basis is official  source_inferred attributions trace to an official channel
(register balance per lab is PRINTED, not checked: it has no failing
 condition, so it is reported as evidence rather than enforced as a gate)
C14 clusters well-formed     clustering absent-or-complete; no cluster spans event_type
C15 scored events have features  every event in event_scores has an insight_features row
C16 bake-off covers identical set  every model in event_scores scores the same event set
C17 scores cite a known policy     every event_scores row names the current policy version,
                                   and every event type in the corpus is ranked by it

Run:  python -m fli.cli checks [--db PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from pathlib import Path

from fli import storage
from fli.core.text import contains_verbatim, html_to_text
from fli.core.policy import PolicyError, describe, load_policy
from fli.knowledge.register import valid_candidate_name


def check(name: str, failures: list[str], all_failures: list[str]) -> None:
    if failures:
        all_failures.extend(f"{name}: {f}" for f in failures)
        print(f"FAIL {name} ({len(failures)})")
        for f in failures[:5]:
            print(f"     - {f}")
    else:
        print(f"PASS {name}")


def evidence_reverifies(conn: sqlite3.Connection, ev_row: sqlite3.Row) -> bool:
    """Re-match an evidence row against its stored document.
    exact: verbatim in raw or extracted text; structural: verbatim in raw."""
    doc = conn.execute("SELECT raw_content FROM raw_documents WHERE id=?",
                       (ev_row["document_id"],)).fetchone()
    raw = doc["raw_content"]
    if ev_row["verification"] == "structural":
        return contains_verbatim(raw, ev_row["verbatim_content"])
    return (contains_verbatim(raw, ev_row["verbatim_content"])
            or contains_verbatim(html_to_text(raw), ev_row["verbatim_content"]))


def policy_attribution_failures(conn: sqlite3.Connection, policy) -> list[str]:
    """C17 — every ranking must trace back to an editorial policy version.

    Vacuous while `event_scores` is empty, so a green C17 is not evidence that
    the bake-off has been run (C15/C16 share that property).
    """
    failures = []
    for r in conn.execute("SELECT DISTINCT policy_version v FROM event_scores"):
        if r["v"] != policy.version:
            failures.append(
                f"event_scores cites policy v{r['v']}, but config/policy.yml is "
                f"v{policy.version} (re-run the bake-off, or restore that version)")
    # An event type the policy has not ranked silently gets the lowest prior.
    # The schema's CHECK constraint bounds the set; this catches drift where a
    # type is added there but not to the policy.
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

    tier_for_method = {"exact": {"verbatim", "name_match_only"},
                       "coauthor_overlap": {"corroborated"},
                       "manual": {"manual_approved"},
                       "self_link": {"verbatim"}}
    bad = []
    for r in conn.execute("SELECT * FROM identities"):
        if r["confidence_tier"] not in tier_for_method[r["resolution_method"]]:
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

    # Register balance. Printed, not checked: it has no failing
    # condition, but the de-skew claim must be read off measured numbers.
    print("\nregister balance (candidates / approved / insights per lab):")
    from fli.knowledge.register import balance_by_lab
    for lab, d in sorted(balance_by_lab(conn).items(), key=lambda x: -x[1]["insights"]):
        print(f"  {lab:<16} cand={d['candidates']:<4} approved={d['approved']:<3}"
              f" insights={d['insights']}")

    # --- Scoring. Each enforces only once its artifact exists. ---
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
