"""Resolve GitHub logins to registered people, with evidence."""
from __future__ import annotations

import json
import os
import sqlite3
import time

from fli import storage
from fli.core.http import FetchError, http_get
from fli.core.text import name_key
from fli.knowledge.register.approval import valid_candidate_name

API = "https://api.github.com"

MIN_CONTRIBUTIONS = 5

MAX_CONTRIBUTORS_PER_REPO = 30

UNAUTH_BUDGET = 55

_COMPANY_TERMS = {
    "OpenAI":          ["openai", "@openai", "open ai"],
    "Anthropic":       ["anthropic", "anthropics", "@anthropics", "@anthropic"],
    "Google DeepMind": ["deepmind", "google deepmind", "@google-deepmind",
                        "@deepmind", "google"],
    "Meta AI":         ["meta ai", "@meta", "@facebookresearch", "@meta-llama",
                        "fair", "facebook", "meta"],
    "Mistral":         ["mistral", "@mistralai", "mistral ai"],
    "DeepSeek":        ["deepseek", "@deepseek-ai", "deepseek-ai"],
    "Qwen":            ["qwen", "@qwenlm", "qwenlm", "alibaba", "@alibaba"],
    "xAI":             ["xai", "x.ai", "@xai-org", "@xai"],
}

_BOT_MARKERS = ("-bot", "bot-", "[bot]", "-ci", "-automation")


def is_bot(login: str, name: str = "") -> bool:
    low = f"{login} {name}".lower()
    return any(m in low for m in _BOT_MARKERS)


def token() -> str | None:
    from fli.ops.llm import load_dotenv
    load_dotenv()
    return os.environ.get("GITHUB_TOKEN")


def _headers() -> dict[str, str]:
    h = {"Accept": "application/vnd.github+json",
         "User-Agent": "frontier-lab-intelligence"}
    t = token()
    if t:
        h["Authorization"] = f"Bearer {t}"
    return h


REPOS_PER_ORG = 10

MIN_REPO_STARS = 100


def github_orgs() -> dict[str, list[str]]:
    """Lab -> GitHub org handles, from config/register_seeds.yml."""
    from fli.knowledge.register.seeding import load_seeds
    return load_seeds().get("github_orgs", {})


def org_members(org: str) -> list[str]:
    """Logins GitHub itself lists as public members of the org."""
    try:
        rows = _get(f"/orgs/{org}/public_members?per_page=100")
    except FetchError:
        return []
    return [r["login"] for r in rows if isinstance(rows, list)] if isinstance(rows, list) else []


def org_repos(org: str, limit: int = REPOS_PER_ORG) -> list[str]:
    """The org's most-starred repos, as owner/repo."""
    try:
        rows = _get(f"/orgs/{org}/repos?sort=stars&direction=desc&per_page={limit}")
    except FetchError:
        return []
    if not isinstance(rows, list):
        return []
    return [r["full_name"] for r in rows
            if not r.get("fork") and (r.get("stargazers_count") or 0) >= MIN_REPO_STARS]


def repo_slugs(conn) -> list[tuple[str, str]]:
    """(owner/repo, lab) for every tracked GitHub source."""
    out = []
    for r in conn.execute(
            "SELECT s.url, COALESCE(l.name,'') lab FROM sources s"
            " LEFT JOIN labs l ON l.id = s.lab_id"
            " WHERE s.source_type='github'"):
        parts = r["url"].split("github.com/", 1)[-1].split("/")
        if len(parts) >= 2 and r["lab"]:
            out.append((f"{parts[0]}/{parts[1]}", r["lab"]))
    return sorted(set(out))


def company_names_lab(company: str, bio: str, lab: str) -> bool:
    """Word-boundary match of a lab in the profile's company or bio."""
    from fli.core.policy import term_pattern
    hay = f"{company or ''} {bio or ''}"
    return any(term_pattern(t).search(hay) for t in _COMPANY_TERMS.get(lab, []))


def _person_by_name(conn, name: str) -> int | None:
    """Order-insensitive name lookup with a homonym guard: two tracked people
    folding to the same key means a name alone cannot identify this profile, so
    the lookup abstains instead of returning an arbitrary one."""
    if not name:
        return None
    key = name_key(name)
    matches = [r["id"] for r in conn.execute("SELECT id, canonical_name FROM people")
               if name_key(r["canonical_name"]) == key]
    return matches[0] if len(matches) == 1 else None


def lab_from_profile(profile: dict, labs: list[str]) -> str | None:
    """Which tracked lab this profile's own company field names, if any."""
    company, bio = profile.get("company") or "", profile.get("bio") or ""
    for lab in labs:
        if company_names_lab(company, bio, lab):
            return lab
    return None


def classify(profile: dict, lab: str, person_id: int | None,
             is_org_member: bool = False) -> tuple[str, str, str]:
    """(decision, tier, method) for one GitHub profile."""
    if is_org_member:
        return "accept", "verbatim", "self_link"
    if company_names_lab(profile.get("company") or "", profile.get("bio") or "", lab):
        return "accept", "verbatim", "self_link"
    if person_id is not None:
        return "accept", "name_match_only", "exact"
    return "reject", "", ""


def _reverifies(quote: str, raw: str) -> bool:
    """Is the quote still a verbatim substring of the stored document?"""
    from fli.core.text import contains_verbatim, html_to_text
    return (contains_verbatim(raw, quote)
            or contains_verbatim(html_to_text(raw), quote))


MEMBER_LINE = "public_member_of: "


def member_quote(org: str) -> str:
    """The exact bytes an org-membership evidence row quotes."""
    return f"{MEMBER_LINE}{org}"


def as_document(login: str, profile: dict, member_of: str | None = None) -> str:
    """Profile rendered so the quoted field appears VERBATIM in stored bytes.
    Profile rendered so the quoted field appears VERBATIM in stored bytes."""
    lines = [f"github:{login}",
             f"https://github.com/{login}",
             f"{profile.get('name') or login}",
             "",
             f"company: {profile.get('company') or ''}",
             f"bio: {profile.get('bio') or ''}"]
    if member_of:
        lines.append(member_quote(member_of))
    lines += ["", "--", f"id: {profile.get('id', '')}",
              f"public_repos: {profile.get('public_repos', '')}",
              f"followers: {profile.get('followers', '')}", ""]
    return "\n".join(lines)


def _get(path: str) -> list | dict:
    body, _ = http_get(f"{API}{path}", headers=_headers())
    return json.loads(body)


def seed_gh_identities(conn: sqlite3.Connection, dry_run: bool = False,
                       max_per_repo: int = MAX_CONTRIBUTORS_PER_REPO) -> dict:
    repos = repo_slugs(conn)
    existing = {r["handle"].lower() for r in conn.execute(
        "SELECT handle FROM identities WHERE platform='github'")}

    print(f"GitHub identity seeding — {len(repos)} tracked repo(s)")
    for slug, lab in repos:
        print(f"    {slug:<44}{lab}")
    budget = "5,000/hr (token)" if token() else f"{UNAUTH_BUDGET}/hr (NO GITHUB_TOKEN)"
    print(f"  rate budget: {budget}   cost: $0 — the REST API is free")
    if dry_run:
        print("\nDRY RUN — no requests made.")
        return {"dry_run": True, "repos": len(repos)}

    if not token():
        print("  NOTE: no GITHUB_TOKEN; capping requests to stay under the "
              "unauthenticated limit. Add one to .env for full coverage.")

    seen: dict[str, str] = {}
    members: dict[str, str] = {}
    calls = 0

    orgs = github_orgs()
    if orgs and token():
        for lab, handles in orgs.items():
            for org in handles:
                pub = org_members(org)
                calls += 1
                for login in pub:
                    members.setdefault(login, org)
                    seen.setdefault(login, lab)
                found = org_repos(org)
                calls += 1
                for full in found:
                    if not any(full == s for s, _ in repos):
                        repos.append((full, lab))
                print(f"  org {org:<22}{len(pub):>3} public member(s), "
                      f"{len(found):>2} repo(s) >= {MIN_REPO_STARS} stars")
    elif orgs:
        print("  NOTE: org expansion needs GITHUB_TOKEN; skipping it.")

    for slug, lab in repos:
        if not token() and calls >= UNAUTH_BUDGET // 3:
            print("  [cap] unauthenticated request budget reached; stopping "
                  "contributor discovery.")
            break
        try:
            contribs = _get(f"/repos/{slug}/contributors?per_page={max_per_repo}")
            calls += 1
        except FetchError as e:
            print(f"  ERR  {slug}: {str(e)[:70]}")
            continue
        if not isinstance(contribs, list):
            continue
        kept = 0
        for c in contribs:
            if c.get("type") != "User" or is_bot(c.get("login", "")):
                continue
            if (c.get("contributions") or 0) < MIN_CONTRIBUTIONS:
                continue
            login = c["login"]
            if login.lower() in existing or login in seen:
                continue
            seen[login] = lab
            kept += 1
        print(f"  {slug:<44}{kept:>3} candidate(s) above "
              f"{MIN_CONTRIBUTIONS} commits")

    all_labs = [r["name"] for r in conn.execute(
        "SELECT name FROM labs ORDER BY id")]
    accepted = rejected = errors = 0
    for login, lab in seen.items():
        if not token() and calls >= UNAUTH_BUDGET:
            print("  [cap] request budget reached; stopping.")
            break
        lab_row = conn.execute("SELECT id FROM labs WHERE name=?", (lab,)).fetchone()
        if not lab_row:
            continue
        sid = storage.upsert_source(conn, "github", f"github:{login} profile",
                                    f"https://github.com/{login}#profile",
                                    lab_id=None, channel="third_party",
                                    purpose="register")
        try:
            profile = _get(f"/users/{login}")
            calls += 1
            time.sleep(0.1)
        except FetchError as e:
            storage.log_fetch(conn, sid, "error", 0, f"{login}: {str(e)[:180]}")
            print(f"  ERR  {login:<22}{str(e)[:50]}")
            errors += 1
            continue

        body = as_document(login, profile, member_of=members.get(login))
        doc_id, _ = storage.store_document(
            conn, sid, "github", f"https://github.com/{login}#profile", body, None)
        storage.log_fetch(conn, sid, "ok", 1, f"{login}: profile")

        name = profile.get("name") or ""
        if is_bot(login, name):
            storage.log_rejection(conn, doc_id, "stage1", "gh_login_is_bot",
                                  f"{login}: automation account, not a person")
            print(f"  BOT  {login:<22}skipped")
            rejected += 1
            continue
        if not valid_candidate_name(name):
            storage.log_rejection(
                conn, doc_id, "stage1", "gh_name_not_a_person",
                f"{login}: profile name {name!r} is not a person's name"
                f"{' (public org member)' if login in members else ''}")
            print(f"  ANON {login:<22}{'no usable name on the profile':<26}"
                  f"{'org member' if login in members else ''}")
            rejected += 1
            continue
        claimed = lab_from_profile(profile, all_labs)
        attributed = claimed or lab
        person_id = _person_by_name(conn, name)
        decision, tier, method = classify(profile, attributed, person_id,
                                          is_org_member=login in members)
        if claimed and claimed != lab:
            print(f"       {login}: found via {lab}, company says {claimed}")
        lab_row = conn.execute("SELECT id FROM labs WHERE name=?",
                               (attributed,)).fetchone() or lab_row
        if decision == "reject":
            storage.log_rejection(
                conn, doc_id, "stage1", "gh_login_unverified",
                f"{login}: company={profile.get('company')!r} does not name {lab}"
                f" and '{name}' is not in the register")
            print(f"  NO   {login:<22}{(name or '-')[:24]:<26}"
                  f"company={(profile.get('company') or '-')[:18]}")
            rejected += 1
            continue

        evidence_id = None
        try:
            quoted = (member_quote(members[login])
                      if login in members and not profile.get("company")
                      else profile.get("company") or name or login)
            evidence_id = storage.insert_evidence(
                conn, doc_id,
                json.dumps({"kind": "github_profile",
                            "field": "org_membership" if login in members
                                     else "company",
                            "login": login, "profile_name": name}),
                quoted, "exact", 1.0)
            if person_id is None:
                cur = conn.execute(
                    "INSERT INTO people (canonical_name, seniority_tier,"
                    " discovered_via, first_seen_at) VALUES (?,?,?,?)",
                    (name or login, "ic", "seed", storage.now_utc()))
                person_id = cur.lastrowid
                conn.execute(
                    "INSERT INTO affiliations (person_id, lab_id, role, basis,"
                    " observed_at, evidence_id) VALUES (?,?,?,?,?,?)",
                    (person_id, lab_row["id"], None, "page_verbatim",
                     storage.now_utc(), evidence_id))
            conn.execute(
                "INSERT INTO identities (person_id, platform, handle,"
                " confidence_tier, resolution_method, evidence_id)"
                " VALUES (?,?,?,?,?,?)",
                (person_id, "github", login, tier, method, evidence_id))
        except Exception:
            if evidence_id is not None:
                conn.execute("DELETE FROM identities WHERE evidence_id=?", (evidence_id,))
                conn.execute("DELETE FROM affiliations WHERE evidence_id=?", (evidence_id,))
                conn.execute("DELETE FROM evidence WHERE id=?", (evidence_id,))
                conn.commit()
            raise
        conn.commit()
        accepted += 1
        print(f"  ADD  {login:<22}{(name or login)[:24]:<26}{tier}/{method}")

    print(f"\naccepted {accepted} · rejected {rejected} · errors {errors}"
          f" · {calls} API calls · $0")
    print("  rejected logins are in `rejections` with the company field that "
          "failed them.")
    return {"accepted": accepted, "rejected": rejected, "errors": errors,
            "calls": calls}


def retract_unverifiable(conn: sqlite3.Connection, dry_run: bool = False) -> dict:
    """Remove GitHub register rows whose evidence no longer re-verifies."""
    bad = [r["id"] for r in conn.execute(
        "SELECT ev.id, ev.verbatim_content q, d.raw_content raw"
        " FROM evidence ev JOIN raw_documents d ON d.id = ev.document_id"
        " WHERE d.source_type = 'github'"
        "   AND json_extract(ev.locator,'$.kind') = 'github_profile'")
        if not _reverifies(r["q"], r["raw"])]

    people = {r["person_id"] for r in conn.execute(
        "SELECT person_id FROM identities WHERE evidence_id IN"
        f" ({','.join('?' * len(bad))})", bad)} if bad else set()

    print(f"retract — {len(bad)} github_profile evidence row(s) fail "
          f"re-verification, touching {len(people)} person row(s)")
    if not bad:
        print("  nothing to retract.")
        return {"evidence": 0, "identities": 0, "affiliations": 0, "people": 0}

    q = ",".join("?" * len(bad))
    counts = {t: conn.execute(
        f"SELECT count(*) c FROM {t} WHERE evidence_id IN ({q})", bad
    ).fetchone()["c"] for t in ("identities", "affiliations")}

    orphans = [p for p in people if not any((
        conn.execute("SELECT 1 FROM identities WHERE person_id=? AND"
                     f" evidence_id NOT IN ({q})", (p, *bad)).fetchone(),
        conn.execute("SELECT 1 FROM insights WHERE attributed_person_id=?",
                     (p,)).fetchone(),
        conn.execute("SELECT 1 FROM event_entities WHERE person_id=?",
                     (p,)).fetchone(),
        conn.execute("SELECT 1 FROM person_candidates c JOIN people pe"
                     " ON pe.canonical_name = c.name WHERE pe.id = ?",
                     (p,)).fetchone()))]

    print(f"  identities {counts['identities']}   affiliations "
          f"{counts['affiliations']}   people {len(orphans)} of {len(people)}"
          f" (the rest are referenced elsewhere and stay)")
    if dry_run:
        print("  DRY RUN — nothing deleted.")
        return {"evidence": len(bad), **counts, "people": len(orphans),
                "dry_run": True}

    conn.execute(f"DELETE FROM identities WHERE evidence_id IN ({q})", bad)
    conn.execute(f"DELETE FROM affiliations WHERE evidence_id IN ({q})", bad)
    conn.execute(f"DELETE FROM evidence WHERE id IN ({q})", bad)
    if orphans:
        po = ",".join("?" * len(orphans))
        conn.execute(f"DELETE FROM affiliations WHERE person_id IN ({po})",
                     orphans)
        conn.execute(f"DELETE FROM people WHERE id IN ({po})", orphans)
    cur = conn.execute(
        "DELETE FROM sources WHERE purpose='register' AND source_type='github'"
        " AND id NOT IN (SELECT source_id FROM fetch_log)"
        " AND id NOT IN (SELECT source_id FROM raw_documents)")
    conn.commit()
    print(f"  retracted. unfetched source rows removed: {cur.rowcount}")
    print("  re-run `register gh_identities` to re-admit these people with "
          "evidence that re-verifies.")
    return {"evidence": len(bad), **counts, "people": len(orphans),
            "sources": cur.rowcount}


def prune_unnameable_github_people(conn: sqlite3.Connection,
                                   dry_run: bool = False) -> dict:
    """Remove GitHub-only people the current admission gate would now reject.
    Remove GitHub-only people the current admission gate would now reject."""
    targets = []
    for r in conn.execute(
            "SELECT p.id, p.canonical_name FROM people p"
            " WHERE p.id IN (SELECT person_id FROM identities WHERE platform='github')"
            "    OR p.id NOT IN (SELECT person_id FROM identities)"):
        plats = {x["platform"] for x in conn.execute(
            "SELECT DISTINCT platform FROM identities WHERE person_id=?",
            (r["id"],))}
        if plats - {"github"}:
            continue
        if valid_candidate_name(r["canonical_name"]) and plats:
            continue
        depended = any((
            conn.execute("SELECT 1 FROM insights WHERE attributed_person_id=?",
                         (r["id"],)).fetchone(),
            conn.execute("SELECT 1 FROM event_entities WHERE person_id=?",
                         (r["id"],)).fetchone(),
            conn.execute("SELECT 1 FROM person_candidates c JOIN people pe"
                         " ON pe.canonical_name=c.name"
                         " WHERE pe.id=? AND c.status='approved'",
                         (r["id"],)).fetchone()))
        if not depended:
            targets.append((r["id"], r["canonical_name"]))

    print(f"prune — {len(targets)} github-only person row(s) the name gate now "
          f"rejects")
    for pid, name in targets[:12]:
        print(f"    {pid:>4}  {name!r}")
    if len(targets) > 12:
        print(f"    ... and {len(targets) - 12} more")
    if dry_run:
        print("  DRY RUN — nothing deleted.")
        return {"people": len(targets), "dry_run": True}

    ids = [t[0] for t in targets]
    if ids:
        q = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM identities WHERE person_id IN ({q})", ids)
        conn.execute(f"DELETE FROM affiliations WHERE person_id IN ({q})", ids)
        conn.execute(f"DELETE FROM people WHERE id IN ({q})", ids)

    cur = conn.execute(
        "DELETE FROM evidence WHERE"
        " json_extract(locator,'$.kind') = 'github_profile'"
        " AND NOT EXISTS (SELECT 1 FROM identities i WHERE i.evidence_id = evidence.id)"
        " AND NOT EXISTS (SELECT 1 FROM affiliations a WHERE a.evidence_id = evidence.id)"
        " AND NOT EXISTS (SELECT 1 FROM insights n WHERE n.evidence_id = evidence.id)"
        " AND NOT EXISTS (SELECT 1 FROM person_candidates c WHERE c.evidence_id = evidence.id)"
        " AND NOT EXISTS (SELECT 1 FROM event_entities ee WHERE ee.evidence_id = evidence.id)")
    stranded = cur.rowcount

    debris = [r["id"] for r in conn.execute(
        "SELECT s.id FROM sources s"
        " WHERE s.purpose='register' AND s.source_type='github'"
        "   AND NOT EXISTS (SELECT 1 FROM evidence e"
        "                   JOIN raw_documents d ON d.id=e.document_id"
        "                   WHERE d.source_id=s.id)"
        "   AND NOT EXISTS (SELECT 1 FROM rejections r"
        "                   JOIN raw_documents d ON d.id=r.document_id"
        "                   WHERE d.source_id=s.id)")]
    if debris:
        dq = ",".join("?" * len(debris))
        conn.execute(f"DELETE FROM raw_documents WHERE source_id IN ({dq})", debris)
        conn.execute(f"DELETE FROM fetch_log WHERE source_id IN ({dq})", debris)
        conn.execute(f"DELETE FROM sources WHERE id IN ({dq})", debris)
    conn.commit()
    print(f"  pruned {len(ids)} person row(s). Their pending queue rows survive "
          f"— a real name re-admits them.")
    print(f"  stranded github_profile evidence removed: {stranded}")
    print(f"  orphan register sources removed: {len(debris)}")
    return {"people": len(ids), "sources": len(debris), "evidence": stranded}


OBSERVE_CADENCE_DAYS = 7


def observe_gh_profiles(conn: sqlite3.Connection, dry_run: bool = False) -> dict:
    """Re-observe the CURRENT employer of every person with a GitHub identity.
    Re-observe the CURRENT employer of every person with a GitHub identity."""
    rows = conn.execute(
        "SELECT i.person_id, i.handle login, p.canonical_name name"
        " FROM identities i JOIN people p ON p.id = i.person_id"
        " WHERE i.platform = 'github' GROUP BY i.person_id, i.handle").fetchall()
    all_labs = [r["name"] for r in conn.execute("SELECT name FROM labs ORDER BY id")]
    budget = "5,000/hr (token)" if token() else f"{UNAUTH_BUDGET}/hr (NO GITHUB_TOKEN)"
    print(f"GitHub profile re-observation — {len(rows)} identity(ies),"
          f" cadence {OBSERVE_CADENCE_DAYS}d, rate budget {budget}, $0")
    if dry_run:
        print("DRY RUN — no requests made.")
        return {"dry_run": True, "profiles": len(rows)}

    observed = skipped_cadence = no_lab = errors = calls = 0
    for r in rows:
        if not token() and calls >= UNAUTH_BUDGET:
            print("  [cap] unauthenticated request budget reached; stopping.")
            break
        login = r["login"]
        url = f"https://github.com/{login}#profile"
        sid = storage.upsert_source(conn, "github", f"github:{login} profile",
                                    url, lab_id=None, channel="third_party",
                                    purpose="register")
        recent = conn.execute(
            "SELECT 1 FROM fetch_log WHERE source_id=? AND status='ok'"
            " AND attempted_at > datetime('now', ?)",
            (sid, f"-{OBSERVE_CADENCE_DAYS} days")).fetchone()
        if recent:
            skipped_cadence += 1
            continue
        try:
            profile = _get(f"/users/{login}")
            calls += 1
            time.sleep(0.1)
        except FetchError as e:
            storage.log_fetch(conn, sid, "error", 0, f"{login}: {str(e)[:180]}")
            errors += 1
            continue
        body = as_document(login, profile)
        doc_id, _ = storage.store_document(conn, sid, "github", url, body, None)
        storage.log_fetch(conn, sid, "ok", 1, f"{login}: profile re-observed")

        claimed = lab_from_profile(profile, all_labs)
        if not claimed or not profile.get("company"):
            no_lab += 1
            continue
        lab_row = conn.execute("SELECT id FROM labs WHERE name=?",
                               (claimed,)).fetchone()
        if not lab_row:
            no_lab += 1
            continue
        ev = storage.insert_evidence(
            conn, doc_id,
            json.dumps({"kind": "github_profile", "field": "company",
                        "login": login, "profile_name": r["name"],
                        "observation": "reobserve"}),
            profile["company"], "exact", 1.0)
        conn.execute(
            "INSERT INTO affiliations (person_id, lab_id, role, basis,"
            " observed_at, evidence_id) VALUES (?,?,?,?,?,?)",
            (r["person_id"], lab_row["id"], None, "page_verbatim",
             storage.now_utc(), ev))
        conn.commit()
        observed += 1
        print(f"  OBS  {login:<22}{r['name'][:24]:<26}{claimed}")

    print(f"\nobserved {observed} · no tracked lab {no_lab} ·"
          f" within cadence {skipped_cadence} · errors {errors}"
          f" · {calls} API calls · $0")
    return {"observed": observed, "no_lab": no_lab,
            "skipped_cadence": skipped_cadence, "errors": errors,
            "calls": calls}
