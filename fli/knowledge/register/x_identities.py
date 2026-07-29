"""Resolve X handles to registered people, with evidence.

An X handle is the one identifier that links a researcher's informal output to
the formal record: the same person publishes as an arXiv author string, commits
under a GitHub login, and posts under an @handle. Without this table the
register knows people only as they appear on lab pages, which is where nobody
announces that they are leaving.

THE ADMISSION RULE. `identities.evidence_id` is NOT NULL, so a handle cannot be
asserted — it has to be proven by a document. The proof used here is the X
profile itself: a bio reading "Research scientist @AnthropicAI" is a verbatim,
self-declared affiliation, stored as an immutable document and quoted like any
other evidence. Three outcomes, and the tier records which one happened:

    bio names a tracked lab          -> verbatim         (self_link)
    bio silent, name matches register-> name_match_only  (exact)
    neither                          -> REJECTED, logged as x_handle_unverified

The second tier is `name_match_only`, not `corroborated`: agreeing with a name
already in the register is a name match, and `corroborated` is reserved for an
independent second source (check C4 enforces the pairing).

The third case is the point. The candidate list (config/register_seeds.yml,
`x_candidates`) is a starting guess, and
guesses about who works where go stale fastest of anything in this system —
people move, that is the signal we are chasing. Handles that fail land in
`rejections` with the bio that failed them, so the miss rate is a number in the
report rather than a silent gap.

WHY A CURATED LIST AT ALL. X has no "researchers at lab X" endpoint, and
guessing handles from canonical names (firstlast, flast) matches the wrong
person often enough to poison the register — the failure is silent and the
damage is attributed posts. A named list that must survive an API check is the
honest version of "part automated discovery, part judgment".

Cost: one User: Read per candidate, $0.010. ~50 candidates = $0.50.

Run:  python3 -m fli.cli register x_identities --dry-run   # cost only
      python3 -m fli.cli register x_identities
"""
from __future__ import annotations

import json
import sqlite3

from fli import storage
from fli.core.config import X_MAX_USER_LOOKUPS, X_USER_COST_USD
from fli.core.http import FetchError
from fli.core.text import name_key

# The curated candidate list and per-lab bio spellings live in
# config/register_seeds.yml (x_candidates / lab_bio_terms), next to the seed
# people they extend. Same shapes as before:
#
# CANDIDATES: (handle, lab, person name as we expect the profile to render it).
# Candidates, not facts — every row is checked against the live profile before
# anything is written. Lab attribution here only decides which lab name we look
# for in the bio; it is never itself stored as truth.
#
# LAB_BIO_TERMS: bio spellings that indicate a lab. Deliberately generous on
# surface form and strict on word boundaries, reusing the same matcher the
# policy lexicon uses.
from fli.knowledge.register.seeding import load_seeds

CANDIDATES: list[tuple[str, str, str]] = [
    (c["handle"], c["lab"], c["name"]) for c in load_seeds()["x_candidates"]]

LAB_BIO_TERMS = {lab: list(terms)
                 for lab, terms in load_seeds()["lab_bio_terms"].items()}


def bio_names_lab(bio: str, lab: str) -> bool:
    """Word-boundary match of any known spelling of the lab in the bio.

    Substring matching would score "metaphor" as Meta and "fairness" as FAIR —
    the same class of error that put `cluster` in the compute lexicon.
    """
    from fli.core.policy import term_pattern
    return any(term_pattern(t).search(bio) for t in LAB_BIO_TERMS.get(lab, []))


def _person_by_name(conn: sqlite3.Connection, name: str) -> int | None:
    """Order-insensitive name lookup, so 'Liang Wenfeng' finds 'Wenfeng Liang'."""
    key = name_key(name)
    for r in conn.execute("SELECT id, canonical_name FROM people"):
        if name_key(r["canonical_name"]) == key:
            return r["id"]
    return None


# The C4 pairing rule: the confidence tiers each resolution method may assert.
# Defined once, here, where the values are chosen; the validation battery (C4)
# and the unit tests both import it, so the rule cannot drift between them.
TIER_FOR_METHOD = {"exact": {"verbatim", "name_match_only"},
                   "coauthor_overlap": {"corroborated"},
                   "manual": {"manual_approved"},
                   "self_link": {"verbatim"}}


def classify(profile: dict, lab: str, person_id: int | None) -> tuple[str, str, str]:
    """(decision, tier, method) for one fetched profile. Pure — unit-testable
    without a network, which is why the API call is not inlined here."""
    bio = profile.get("description") or ""
    if bio_names_lab(bio, lab):
        return "accept", "verbatim", "self_link"
    if person_id is not None:
        return "accept", "name_match_only", "exact"
    return "reject", "", ""


def as_document(handle: str, profile: dict) -> str:
    """Render a profile so the BIO APPEARS VERBATIM in the stored bytes.

    Evidence must be a substring of what was stored, and JSON encoding is not a
    substring-preserving transform — `json.dumps(profile)` escapes newlines, so
    the real bio would no longer occur in the document and check C2 would fail.

    Same header convention as `x_api.as_document`, so readable_text and the
    verifier need no special case. Structured fields go below the bio, where
    they cannot interrupt it.
    """
    bio = profile.get("description") or ""
    metrics = profile.get("public_metrics") or {}
    return (f"@{handle}\n"
            f"https://x.com/{handle}\n"
            f"{profile.get('name') or handle}\n\n"
            f"{bio}\n\n"
            f"--\nx_user_id: {profile.get('id', '')}\n"
            f"verified: {profile.get('verified', '')}\n"
            f"followers: {metrics.get('followers_count', '')}\n")


def seed_x_identities(conn: sqlite3.Connection, dry_run: bool = False,
                      candidates: list[tuple[str, str, str]] | None = None) -> dict:
    cands = candidates if candidates is not None else CANDIDATES
    cands = cands[:X_MAX_USER_LOOKUPS]
    worst = len(cands) * X_USER_COST_USD

    existing = {r["handle"].lstrip("@").lower() for r in conn.execute(
        "SELECT handle FROM identities WHERE platform='x'")}
    todo = [c for c in cands if c[0].lower() not in existing]

    print(f"X identity seeding — {len(cands)} candidate(s), {len(todo)} not yet "
          f"resolved")
    print(f"  worst case: ${len(todo) * X_USER_COST_USD:.2f} "
          f"({len(todo)} x ${X_USER_COST_USD} User: Read)")
    if dry_run:
        print("DRY RUN — no requests made, nothing spent.")
        return {"dry_run": True, "worst_case_usd": worst, "candidates": len(todo)}
    if not todo:
        print("  nothing to do — every candidate already has an identity row.")
        return {"accepted": 0, "rejected": 0, "spend_usd": 0.0}

    from fli.ingestion.x_api import XClient, bearer_token
    token = bearer_token()
    if not token:
        raise SystemExit("X_BEARER_TOKEN not set (put it in .env).")
    client = XClient(token)

    accepted = rejected = errors = 0
    mismatches: list[tuple[str, str, str]] = []
    for handle, lab, expected in todo:
        lab_row = conn.execute("SELECT id FROM labs WHERE name=?", (lab,)).fetchone()
        if not lab_row:
            print(f"  SKIP @{handle}: lab '{lab}' not in register")
            continue
        # purpose='register': this source proves identity, it does not supply
        # content. `accounts_to_track` reads identities rather than sources, so
        # this never silently becomes an ingested feed.
        sid = storage.upsert_source(conn, "social", f"@{handle} profile",
                                    f"https://x.com/{handle}#profile",
                                    lab_id=None, channel="third_party",
                                    purpose="register")
        try:
            profile = client.user_profile(handle)
        except FetchError as e:
            storage.log_fetch(conn, sid, "error", 0, f"{handle}: {str(e)[:180]}")
            print(f"  ERR  @{handle:<18} {str(e)[:60]}")
            errors += 1
            continue
        if not profile:
            storage.log_fetch(conn, sid, "error", 0, f"{handle}: no such user")
            # stage='stage1': the schema allows only stage1|stage2|verification
            # and seeding.py files register rejections the same way — the stage
            # denotes a pre-LLM gate, which this is.
            storage.log_rejection(conn, None, "stage1", "x_handle_not_found", handle)
            print(f"  GONE @{handle:<18} no such user")
            rejected += 1
            continue

        body = as_document(handle, profile)
        doc_id, _is_new = storage.store_document(
            conn, sid, "social", f"https://x.com/{handle}#profile", body, None)
        storage.log_fetch(conn, sid, "ok", 1, f"{handle}: profile")

        name = profile.get("name") or handle
        bio = profile.get("description") or ""
        person_id = _person_by_name(conn, name) or _person_by_name(conn, expected)
        decision, tier, method = classify(profile, lab, person_id)

        # The live profile always wins over the candidate list. Disagreement is
        # data — it measures how stale a curated handle list goes — so it is
        # recorded in the evidence locator, not just printed.
        mismatch = name_key(name) != name_key(expected)
        if mismatch:
            mismatches.append((handle, expected, name))

        if decision == "reject":
            storage.log_rejection(
                conn, doc_id, "stage1", "x_handle_unverified",
                f"@{handle}: bio does not name {lab} and '{name}' is not in the "
                f"register. bio={bio[:120]!r}")
            print(f"  NO   @{handle:<18} {name[:24]:<24} bio does not corroborate {lab}")
            rejected += 1
            continue

        # A failure between the evidence row and the identity row must leave
        # nothing behind: orphaned evidence proves no claim, and check C10
        # flags it. Explicit cleanup rather than a SAVEPOINT, because
        # `storage.insert_evidence` commits internally and a COMMIT releases
        # every open savepoint — the rollback would be a no-op that looks like
        # protection.
        evidence_id = None
        try:
            evidence_id = storage.insert_evidence(
                conn, doc_id,
                json.dumps({"kind": "x_profile", "field": "description",
                            "handle": handle, "expected_name": expected,
                            "profile_name": name}),
                bio or name, "exact", 1.0)

            if person_id is None:
                # discovered_via='seed', not a new 'x_profile' enum value: the
                # column records the discovery MECHANISM, which here is the same
                # as every other seed — a curated list admitted only where a
                # fetched page corroborates it. The X-specific detail lives on
                # `identities.platform='x'` and the evidence row, so widening
                # the CHECK would duplicate what the evidence chain answers.
                cur = conn.execute(
                    "INSERT INTO people (canonical_name, seniority_tier,"
                    " discovered_via, first_seen_at) VALUES (?,?,?,?)",
                    (name, "ic", "seed", storage.now_utc()))
                person_id = cur.lastrowid
                # basis marks this as self-declared, so it is never mistaken
                # for a lab-page confirmation.
                conn.execute(
                    "INSERT INTO affiliations (person_id, lab_id, role, basis,"
                    " observed_at, evidence_id) VALUES (?,?,?,?,?,?)",
                    (person_id, lab_row["id"], None, "page_verbatim",
                     storage.now_utc(), evidence_id))

            conn.execute(
                "INSERT INTO identities (person_id, platform, handle,"
                " confidence_tier, resolution_method, evidence_id)"
                " VALUES (?,?,?,?,?,?)",
                (person_id, "x", handle, tier, method, evidence_id))
        except Exception:
            if evidence_id is not None:
                conn.execute("DELETE FROM identities WHERE evidence_id=?", (evidence_id,))
                conn.execute("DELETE FROM affiliations WHERE evidence_id=?", (evidence_id,))
                conn.execute("DELETE FROM evidence WHERE id=?", (evidence_id,))
                conn.commit()
            raise
        conn.commit()
        accepted += 1
        flag = f"  (expected {expected})" if mismatch else ""
        print(f"  ADD  @{handle:<18} {name[:24]:<24} {tier}/{method}{flag}")

    spend = client.users_read * X_USER_COST_USD
    print(f"\naccepted {accepted} · rejected {rejected} · errors {errors}"
          f" · {client.users_read} user reads = ${spend:.2f}")
    print("  rejected handles are in `rejections` with the bio that failed them.")
    if mismatches:
        print(f"\n  {len(mismatches)} handle(s) resolved to a different person than "
              f"the candidate list assumed — the profile won, as it should:")
        for h, exp, got in mismatches:
            print(f"    @{h:<18} listed as {exp!r}, is {got!r}")
    return {"accepted": accepted, "rejected": rejected, "errors": errors,
            "mismatches": mismatches, "spend_usd": spend}


def labs_named_in_bio(conn: sqlite3.Connection, bio: str) -> list[sqlite3.Row]:
    """Tracked labs whose bio spelling appears in the text, word-bounded.
    Pure lookup — unit-testable without a network, like `classify`."""
    named = []
    for r in conn.execute("SELECT id, name FROM labs"):
        if bio_names_lab(bio, r["name"]):
            named.append(r)
    return named


def reobserve_x_bios(conn: sqlite3.Connection, dry_run: bool = False,
                     cadence_days: int | None = None,
                     max_lookups: int | None = None) -> dict:
    """Re-fetch the bios of ALREADY-ADMITTED X identities and append what they
    declare today as dated affiliation observations.

    This is `observe()` for the one surface where people actually announce
    moves. A lab page drops a leaver silently — a MISS with no destination —
    but a bio rewritten from "@labA" to "@labB" is the arrival itself, and the
    appended observation is exactly what mobility synthesis pairs into a
    personnel event in the same pipeline run.

    Spending posture: one User: Read per due identity ($0.010). The cadence
    gate reads fetch_log, so an identity is re-checked at most once per
    X_BIO_REOBSERVE_DAYS regardless of how often the pipeline runs, and
    max_lookups caps a single run the way every other X spend is capped.
    Admission is not re-litigated here: who the handle belongs to was proven at
    seeding; this asks only where that person says they are now."""
    from fli.core.config import X_BIO_REOBSERVE_DAYS
    cadence = X_BIO_REOBSERVE_DAYS if cadence_days is None else cadence_days
    cap = X_MAX_USER_LOOKUPS if max_lookups is None else max_lookups

    rows = conn.execute(
        "SELECT i.handle, i.person_id, p.canonical_name FROM identities i"
        " JOIN people p ON p.id=i.person_id WHERE i.platform='x'"
        " ORDER BY i.id").fetchall()
    due = []
    for r in rows:
        recent = conn.execute(
            "SELECT 1 FROM fetch_log f JOIN sources s ON s.id=f.source_id"
            " WHERE s.url=? AND f.status='ok'"
            " AND f.attempted_at >= datetime('now', ?)",
            (f"https://x.com/{r['handle']}#profile", f"-{cadence} days")).fetchone()
        if not recent:
            due.append(r)
    due = due[:cap]

    print(f"X bio re-observation — {len(rows)} identity(ies), {len(due)} due"
          f" (cadence {cadence}d, cap {cap})")
    print(f"  worst case: ${len(due) * X_USER_COST_USD:.2f}"
          f" ({len(due)} x ${X_USER_COST_USD} User: Read)")
    if dry_run:
        print("DRY RUN — no requests made, nothing spent.")
        return {"dry_run": True, "due": len(due),
                "worst_case_usd": len(due) * X_USER_COST_USD}
    if not due:
        print("  nothing due — every bio was re-observed inside the cadence.")
        return {"observed": 0, "new_lab": 0, "no_lab": 0, "errors": 0,
                "spend_usd": 0.0}

    from fli.ingestion.x_api import XClient, bearer_token
    token = bearer_token()
    if not token:
        raise SystemExit("X_BEARER_TOKEN not set (put it in .env).")
    client = XClient(token)

    today = storage.now_utc()[:10]
    observed = new_lab = no_lab = errors = 0
    for r in due:
        handle = r["handle"]
        url = f"https://x.com/{handle}#profile"
        sid = storage.upsert_source(conn, "social", f"@{handle} profile", url,
                                    lab_id=None, channel="third_party",
                                    purpose="register")
        try:
            profile = client.user_profile(handle)
        except FetchError as e:
            storage.log_fetch(conn, sid, "error", 0, f"{handle}: {str(e)[:180]}")
            print(f"  ERR  @{handle:<18} {str(e)[:60]}")
            errors += 1
            continue
        if not profile:
            storage.log_fetch(conn, sid, "error", 0, f"{handle}: no such user")
            print(f"  GONE @{handle:<18} account no longer exists")
            errors += 1
            continue

        body = as_document(handle, profile)
        doc_id, _ = storage.store_document(conn, sid, "social", url, body, None)
        storage.log_fetch(conn, sid, "ok", 1, f"{handle}: bio re-observed")

        bio = profile.get("description") or ""
        named = labs_named_in_bio(conn, bio)
        if not named:
            # The bio-equivalent of observe()'s MISS: they self-declared a lab
            # once and no longer do. A destination may appear next cadence.
            no_lab += 1
            print(f"  none @{handle:<18} bio no longer names a tracked lab")
            continue
        for lab in named:
            if conn.execute(
                    "SELECT 1 FROM affiliations WHERE person_id=? AND lab_id=?"
                    " AND substr(observed_at,1,10)=?",
                    (r["person_id"], lab["id"], today)).fetchone():
                continue
            known = conn.execute(
                "SELECT 1 FROM affiliations WHERE person_id=? AND lab_id=?",
                (r["person_id"], lab["id"])).fetchone()
            ev = storage.insert_evidence(
                conn, doc_id,
                json.dumps({"kind": "x_profile", "field": "description",
                            "handle": handle, "reobservation": True,
                            "lab": lab["name"]}),
                bio, "exact", 1.0)
            conn.execute(
                "INSERT INTO affiliations (person_id, lab_id, role, basis,"
                " observed_at, evidence_id) VALUES (?,?,?,?,?,?)",
                (r["person_id"], lab["id"], None, "page_verbatim",
                 storage.now_utc(), ev))
            observed += 1
            if not known:
                new_lab += 1
                print(f"  NEW  @{handle:<18} bio now names {lab['name']}"
                      f" — mobility synthesis will pair this")
            else:
                print(f"  ok   @{handle:<18} still {lab['name']}")
    conn.commit()

    spend = client.users_read * X_USER_COST_USD
    print(f"\nre-observed {observed} affiliation(s) · {new_lab} at a NEW lab"
          f" · {no_lab} bio(s) with no tracked lab · {errors} error(s)"
          f" · {client.users_read} user reads = ${spend:.2f}")
    return {"observed": observed, "new_lab": new_lab, "no_lab": no_lab,
            "errors": errors, "spend_usd": spend}
