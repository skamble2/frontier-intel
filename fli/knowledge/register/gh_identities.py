"""Resolve GitHub logins to registered people, with evidence.

THE THIRD PLATFORM. The register already links an arXiv author string to an X
handle; this closes the loop — knowing that a GitHub account is the same
person as both.

WHY THIS COULD NOT BE DONE FROM WHAT WE ALREADY STORED. Ingestion reads
`releases.atom`, which carries a tag, a URL and a changelog and NO author
field. There was nothing in the stored bytes to correlate a person against, so
for a long time the honest answer was "not implemented". The fix is not more
parsing — it is a different endpoint:

    /repos/{owner}/{repo}/contributors   who actually commits to a lab's repo
    /users/{login}                       their name, company and bio

Contributors are a better population than release notes ever were: these are
people the lab's own repository says wrote its code.

DISCOVERY RUNS IN TWO PASSES. The configured feeds are all SDK/inference
repos, and they produced engineers with zero overlap against the arXiv
population — the people who ship a client library are not the people who write
the papers. So the org is mined too:

    /orgs/{org}/public_members  membership GitHub itself asserts
    /orgs/{org}/repos           the lab's RESEARCH repos, where paper authors
                                actually commit

THE ADMISSION RULE, ordered by how much inference each signal needs:

    public member of the org  -> verbatim        (self_link)
    `company` names the lab   -> verbatim        (self_link)
    name is in the register   -> name_match_only (exact)
    none of the above         -> REJECTED, logged with the profile

Most contributors will fail. GitHub's `company` field is optional and often
blank, and many contributors to a lab's SDK are outside users. The rejections
are the measurement, not a failure of the method.

COST: nothing. The GitHub REST API is free — 60 requests/hour unauthenticated,
5,000 with any personal access token. Set GITHUB_TOKEN in .env to get the
higher limit; without it the run still works but caps itself.

Run:  python3 -m fli.cli register gh_identities --dry-run
      python3 -m fli.cli register gh_identities
"""
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

# Contributors ranked below this many commits are noise: a one-commit typo fix
# does not make someone a lab researcher. Measured against the tracked repos,
# where the tail is dominated by single-commit outside contributors.
MIN_CONTRIBUTIONS = 5

# Per repo. Contributor lists are long and sorted by commit count, so the head
# is where the lab's own people are.
MAX_CONTRIBUTORS_PER_REPO = 30

# Unauthenticated GitHub allows 60 requests/hour. Refuse to start a run that
# would obviously exceed the limit rather than fail halfway with a 403.
UNAUTH_BUDGET = 55

# A GitHub `company` field is usually the ORG HANDLE, not the company name, and
# the handle is often not the lab's name: Anthropic's org is `anthropics`,
# plural. `\banthropic\b` cannot match "anthropics" — the boundary fails
# between 'c' and 's' — so three people whose company field literally named
# their employer were rejected. Org handles are listed explicitly.
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

# Accounts that are automation, not people. GitHub's `type` field catches Apps
# but not user accounts operated as bots, which is what `stainless-bot` is.
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


# How many of an org's repos to mine, ranked by stars. The tail is forks,
# templates and abandoned experiments; the head is where the lab's own people
# are. Ten per org keeps the whole run inside a few hundred requests.
REPOS_PER_ORG = 10

# Repos below this many stars are not the lab speaking — they are a personal
# scratch repo that happens to live under the org.
MIN_REPO_STARS = 100


def github_orgs() -> dict[str, list[str]]:
    """Lab -> GitHub org handles, from config/register_seeds.yml."""
    from fli.knowledge.register.seeding import load_seeds
    return load_seeds().get("github_orgs", {})


def org_members(org: str) -> list[str]:
    """Logins GitHub itself lists as public members of the org.

    The strongest signal available anywhere in the register: no bio parsing, no
    company string, no name matching — the platform asserting membership. Most
    people keep membership private, so this under-counts badly, but it never
    guesses.
    """
    try:
        rows = _get(f"/orgs/{org}/public_members?per_page=100")
    except FetchError:
        return []
    return [r["login"] for r in rows if isinstance(rows, list)] if isinstance(rows, list) else []


def org_repos(org: str, limit: int = REPOS_PER_ORG) -> list[str]:
    """The org's most-starred repos, as owner/repo.

    Discovered rather than configured: a lab adds repos faster than a config
    file gets updated, and the point of mining an org is to find the research
    code we are not already tracking.
    """
    try:
        rows = _get(f"/orgs/{org}/repos?sort=stars&direction=desc&per_page={limit}")
    except FetchError:
        return []
    if not isinstance(rows, list):
        return []
    return [r["full_name"] for r in rows
            if not r.get("fork") and (r.get("stargazers_count") or 0) >= MIN_REPO_STARS]


def repo_slugs(conn) -> list[tuple[str, str]]:
    """(owner/repo, lab) for every tracked GitHub source.

    Parsed from the stored feed URL rather than configured separately, so the
    repos we mine for people are exactly the repos we already ingest.
    """
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
    """Word-boundary match of a lab in the profile's company or bio.

    Same matcher as the policy lexicon, for the same reason: substring matching
    would read "Metaphysics" as Meta.
    """
    from fli.core.policy import term_pattern
    hay = f"{company or ''} {bio or ''}"
    return any(term_pattern(t).search(hay) for t in _COMPANY_TERMS.get(lab, []))


def _person_by_name(conn, name: str) -> int | None:
    if not name:
        return None
    key = name_key(name)
    for r in conn.execute("SELECT id, canonical_name FROM people"):
        if name_key(r["canonical_name"]) == key:
            return r["id"]
    return None


def lab_from_profile(profile: dict, labs: list[str]) -> str | None:
    """Which tracked lab this profile's own company field names, if any.

    Checked against EVERY tracked lab, not just the repo we found them through.
    A person is found via whatever repo they commit to, which is not the same
    as who employs them: `dltn` says `@anthropics` and was discovered on
    meta-llama, `logankilpatrick` says `Google Deepmind` and was discovered on
    openai-python. Testing only the repo's lab threw both away — and worse,
    would have accepted them under the wrong employer if the wording had been
    looser.

    Ties are impossible in practice and resolved by list order if they occur.
    """
    company, bio = profile.get("company") or "", profile.get("bio") or ""
    for lab in labs:
        if company_names_lab(company, bio, lab):
            return lab
    return None


def classify(profile: dict, lab: str, person_id: int | None,
             is_org_member: bool = False) -> tuple[str, str, str]:
    """(decision, tier, method) for one GitHub profile.

    Ordered by how much inference each signal requires:

      org membership  GitHub asserting the person is IN the organisation. No
                      parsing, no matching — the platform's own answer.
      company field   self-declared, and self-declaration is evidence.
      name match      weakest: agreeing with a name already in the register.

    Pure, so the rule that admits a row is testable without a network.
    """
    if is_org_member:
        return "accept", "verbatim", "self_link"
    if company_names_lab(profile.get("company") or "", profile.get("bio") or "", lab):
        return "accept", "verbatim", "self_link"
    if person_id is not None:
        return "accept", "name_match_only", "exact"
    return "reject", "", ""


def _reverifies(quote: str, raw: str) -> bool:
    """Is the quote still a verbatim substring of the stored document?

    The same substring test C2 applies, computed here from the layer-0 text
    primitives rather than by importing the validation battery — a knowledge
    module must not depend on a validation module (the layering test enforces
    it). A github profile is stored as plain text, so the raw form is enough;
    the html fallback is kept for parity with how C2 reads a page.
    """
    from fli.core.text import contains_verbatim, html_to_text
    return (contains_verbatim(raw, quote)
            or contains_verbatim(html_to_text(raw), quote))


MEMBER_LINE = "public_member_of: "


def member_quote(org: str) -> str:
    """The exact bytes an org-membership evidence row quotes."""
    return f"{MEMBER_LINE}{org}"


def as_document(login: str, profile: dict, member_of: str | None = None) -> str:
    """Profile rendered so the quoted field appears VERBATIM in stored bytes.

    Learned from the X profile bug: `json.dumps` escapes newlines, so a quote
    taken from the raw field is not a substring of the stored document and the
    verification check fails on every row.

    ORG MEMBERSHIP IS WRITTEN INTO THE DOCUMENT, and that is the point of
    `member_of`. The first version of this quoted the sentence "public member
    of the organisation" — a description of what the API returned, not bytes
    the API returned. It read as evidence and re-verified against nothing: 53
    rows failed C2 and, through their affiliations, C5 as well. A field that
    was fetched must be written down before it can be cited.
    """
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

    seen: dict[str, str] = {}            # login -> lab (first source wins)
    # login -> the org handle GitHub listed them under. Not a set: the org is
    # the evidence, so it has to survive as far as the quote.
    members: dict[str, str] = {}
    calls = 0

    # PASS 1 — org membership and research-repo discovery.
    #
    # The 7 configured feeds are all SDK/inference repos, and they produced
    # engineers with ZERO overlap against the arXiv population: the people who
    # ship a client library are not the people who write the papers. Mining the
    # org finds the research repos where those two populations might actually
    # meet.
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
            time.sleep(0.1)                              # politeness, not a retry
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
        # A LOGIN IS NOT A NAME. Org membership is the strongest signal here,
        # and it was strong enough to admit profiles whose `name` field is
        # blank — so the register gained "people" called `dcarr622` and
        # `liann-oai`, 26 of which failed C9. GitHub asserting that an account
        # belongs to the org does not tell us WHO it belongs to, and a register
        # of people cannot hold a row it cannot name. The membership is still
        # recorded as a rejection, so the count remains visible.
        if not valid_candidate_name(name):
            storage.log_rejection(
                conn, doc_id, "stage1", "gh_name_not_a_person",
                f"{login}: profile name {name!r} is not a person's name"
                f"{' (public org member)' if login in members else ''}")
            print(f"  ANON {login:<22}{'no usable name on the profile':<26}"
                  f"{'org member' if login in members else ''}")
            rejected += 1
            continue
        # The lab this profile CLAIMS, which beats the repo we found them on.
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
    """Remove GitHub register rows whose evidence no longer re-verifies.

    THE SYSTEM MUST BE ABLE TO TAKE SOMETHING BACK. The evidence-first rule is
    only worth anything if a row that stops being evidence stops being in the
    register — otherwise "every claim re-verifies" degrades into "every claim
    re-verified once, when it was written".

    What this exists to clean up: an earlier org-membership path quoted the
    sentence "public member of the organisation", which describes what the API
    said rather than repeating what it returned. 53 identities cited it, their
    affiliations inherited the failure, and both C2 and C5 went red. The fix is
    upstream (`as_document` now writes the membership into the document, and
    `member_quote` cites those exact bytes); this retracts what the broken run
    left behind so a re-run can admit the same people properly.

    Deliberately narrow. It only touches `github_profile` evidence, and it only
    deletes a person when nothing else in the database depends on them: an
    event attribution, a second identity, or an APPROVED queue row all keep the
    person. A *pending* candidate does not — that row keys on the name string,
    so it survives the person being removed and will be considered again on its
    own merits.
    """
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
        # The queue keys on the name string, not a person id, so a person who
        # also sits in the approval queue must survive the retraction.
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
    # A source row created for a profile that was never fetched is bookkeeping
    # debris and fails C7 forever; it has no documents, so nothing cites it.
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

    A REGISTER OF PEOPLE CANNOT HOLD A ROW IT CANNOT NAME. An earlier run
    admitted a profile on its org membership or its `company` field without
    checking that the profile carried a person's name, so the register gained
    entries called `liann-oai`, `hallacy` and `pomelo` — a login is not a
    name. `seed_gh_identities` now rejects these outright (`gh_name_not_a_person`),
    and this applies the same rule to what the old path left behind.

    The evidence for these rows still re-verifies — the company field really
    does name the lab — so `retract_unverifiable` correctly leaves them alone.
    The problem is not the evidence, it is that we do not know WHO the account
    belongs to, and C9 exists to keep exactly that out of the register.

    Same orphan-safety as the retract: a person is only removed when nothing
    depends on them. Their pending queue row keys on the name string and
    survives, so anyone later matched to a real name is admitted again.
    """
    targets = []
    for r in conn.execute(
            "SELECT p.id, p.canonical_name FROM people p"
            " WHERE p.id IN (SELECT person_id FROM identities WHERE platform='github')"
            "    OR p.id NOT IN (SELECT person_id FROM identities)"):
        plats = {x["platform"] for x in conn.execute(
            "SELECT DISTINCT platform FROM identities WHERE person_id=?",
            (r["id"],))}
        if plats - {"github"}:
            continue                     # has a non-github identity; keep it
        # unnameable = a login masquerading as a name, or no identity left
        if valid_candidate_name(r["canonical_name"]) and plats:
            continue
        # nothing downstream may depend on the person
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

    # Deleting the identity strands its evidence row: nothing references it,
    # and C10 flags every stranded row forever. Sweep github_profile evidence
    # that no identity, affiliation, insight, candidate or entity cites — this
    # also collects strays a previous prune left behind.
    cur = conn.execute(
        "DELETE FROM evidence WHERE"
        " json_extract(locator,'$.kind') = 'github_profile'"
        " AND NOT EXISTS (SELECT 1 FROM identities i WHERE i.evidence_id = evidence.id)"
        " AND NOT EXISTS (SELECT 1 FROM affiliations a WHERE a.evidence_id = evidence.id)"
        " AND NOT EXISTS (SELECT 1 FROM insights n WHERE n.evidence_id = evidence.id)"
        " AND NOT EXISTS (SELECT 1 FROM person_candidates c WHERE c.evidence_id = evidence.id)"
        " AND NOT EXISTS (SELECT 1 FROM event_entities ee WHERE ee.evidence_id = evidence.id)")
    stranded = cur.rowcount

    # Runs even when there were no people to prune: source debris outlives the
    # person it belonged to, so this sweep is independent cleanup.
    #
    # A pruned profile leaves its source + document behind. Once no evidence or
    # rejection cites that document, the source is orphan debris — and a source
    # with a document but no fetch_log row (an artifact of an earlier code path)
    # fails C7 forever. Remove only those that nothing references.
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


# Re-fetching a profile that was observed hours ago buys nothing: employer
# changes are weekly-scale events. Seven days matches the X bio cadence.
OBSERVE_CADENCE_DAYS = 7


def observe_gh_profiles(conn: sqlite3.Connection, dry_run: bool = False) -> dict:
    """Re-observe the CURRENT employer of every person with a GitHub identity.

    `seed_gh_identities` skips logins that are already in the register, so a
    person admitted once was never looked at again — their GitHub identity was
    plumbing with no downstream signal. This is the GitHub side of `observe()`
    and `reobserve_x_bios`: re-fetch each known profile, and when its `company`
    field names a tracked lab, append a dated `page_verbatim` affiliation
    observation. Mobility synthesis pairs those observations into personnel
    events — a company field that flips from one lab to another becomes a move
    in the same pipeline run that saw it.

    A profile that names NO tracked lab appends nothing: absence of evidence is
    not an observation, and mobility only fires on presence at the new lab.

    Cadence-gated in fetch_log (like X bios), so a daily pipeline re-fetches
    each profile at most every {OBSERVE_CADENCE_DAYS} days. Free: the REST API
    costs $0 and one request per profile.
    """
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
            time.sleep(0.1)                          # politeness, not a retry
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
