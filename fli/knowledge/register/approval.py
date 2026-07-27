"""Candidate approval.

Three things, in precedence order: the human override file (always
wins), the per-lab slate that keeps the register balanced,
and the deterministic auto-approve rule. Approval is asynchronous - it
never blocks a pipeline run."""
import json
import sqlite3

from fli import storage
from fli.core.policy import load_policy
from fli.core.paths import OVERRIDES_PATH
from fli.core.text import contains_verbatim, html_to_text, name_key
from fli.knowledge.register.seeding import LABS

# load_policy() is called at use site, never at import, so a policy edit takes
# effect on the next run and tests can point at a different file.


def auto_approve_rule() -> str:
    """Human-readable rule, printed every run so a person can always see which
    policy version admitted whom. The rule's shape is a code concern; only
    `slate_k` is a business choice, so only that comes from the policy."""
    p = load_policy()
    return (f"policy v{p.version}: corroborated + valid_name "
            f"+ top-{p.slate_k}/lab + not vetoed")


def valid_candidate_name(name: str) -> bool:
    """Reject malformed author strings (leading spaces, single tokens,
    lab names) that slip through mega-author paper metadata."""
    if name != name.strip() or len(name.split()) < 2:
        return False
    if not all(any(ch.isalpha() for ch in tok) for tok in name.split()):
        return False
    return name.lower() not in {l.lower() for l, _, _ in LABS}


def _load_raw_overrides() -> dict[str, list[str]]:
    """Parse config/register_overrides.yml — a minimal, hand-written subset
    (two lists, `approve:` / `reject:`, of canonical names under `- ` items).
    Stdlib-only by design; the file is small and human-owned."""
    data: dict[str, list[str]] = {"approve": [], "reject": []}
    if not OVERRIDES_PATH.exists():
        return data
    section = None
    for line in OVERRIDES_PATH.read_text().splitlines():
        s = line.strip()
        if s in ("approve:", "reject:"):
            section = s[:-1]
        elif s.startswith("- ") and section:
            data[section].append(s[2:].strip())
    return data


def _save_raw_overrides(data: dict[str, list[str]]) -> None:
    OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Register overrides — canonical names, one per '- ' line. These win",
             "# over the auto-approve rule and survive DB rebuilds. Edit freely.", ""]
    for section in ("approve", "reject"):
        lines.append(f"{section}:")
        lines += [f"  - {n}" for n in data[section]]
        lines.append("")
    OVERRIDES_PATH.write_text("\n".join(lines))


def load_overrides() -> dict[str, set[str]]:
    """Name-keyed sets for matching (order-insensitive, same key as the register)."""
    raw = _load_raw_overrides()
    return {k: {name_key(n) for n in v} for k, v in raw.items()}


def _candidate_lab_ids(conn, row) -> set[int]:
    """Labs a candidate is attributable to: the persisted seed labs plus a
    collective-author lab_hint if present (the stronger, structural signal)."""
    labs = set(json.loads(row["seed_lab_ids"]))
    if row["lab_hint"]:
        r = conn.execute("SELECT id FROM labs WHERE name=?", (row["lab_hint"],)).fetchone()
        if r:
            labs.add(r["id"])
    return labs


def _slate(conn, pending: list) -> set[int]:
    """Candidate ids that make the top-K-by-paper_count slate of ANY lab they are
    attributable to. Corroboration is already guaranteed — every queue row
    comes from a corroborated paper — so paper_count is only the ranking here."""
    by_lab: dict[int, list] = {}
    for r in pending:
        if not valid_candidate_name(r["name"]):
            continue
        for lab_id in _candidate_lab_ids(conn, r):
            by_lab.setdefault(lab_id, []).append((r["paper_count"], r["id"]))
    slate: set[int] = set()
    k = load_policy().slate_k
    for lst in by_lab.values():
        lst.sort(key=lambda x: (-x[0], x[1]))
        slate.update(cid for _, cid in lst[:k])
    return slate


def show_queue(conn: sqlite3.Connection) -> None:
    """Per-lab review slates: top-K per lab, so no single lab's mega-paper
    cluster buries the others."""
    pending = [dict(r) for r in conn.execute(
        "SELECT * FROM person_candidates WHERE status='pending'")]
    slate = _slate(conn, pending)
    lab_name = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM labs")}
    total = len(pending)
    print(f"reviewable slates ({len(slate)} of {total} pending; top-{load_policy().slate_k}"
          f" per lab by paper_count, valid name):")
    for r in pending:
        if r["id"] not in slate:
            continue
        labs = ", ".join(lab_name.get(i, "?") for i in _candidate_lab_ids(conn, r)) or "-"
        print(f"  [{r['id']:>3}] {r['name']:<28} papers={r['paper_count']}"
              f" labs={labs}")


def _seed_labs_of(conn, candidate_row) -> set[int]:
    """Distinct labs of the seeds this candidate co-authored with."""
    labs = set()
    for seed_id in json.loads(candidate_row["seed_person_ids"]):
        for r in conn.execute(
                "SELECT DISTINCT lab_id FROM affiliations WHERE person_id=?"
                " AND lab_id IS NOT NULL AND basis='page_verbatim'", (seed_id,)):
            labs.add(r["lab_id"])
    return labs


def _affiliation_for(conn, person_id: int, candidate_row,
                     page_cache: dict) -> str:
    """Lab link for an approved person, best available basis:
    1. name verbatim on a stored lab page  -> basis 'page_verbatim'
    2. single corroborating seed's lab     -> basis 'coauthor_inference'
    3. neither -> affiliation row with lab_id NULL (absence recorded)."""
    name = candidate_row["name"]
    hits = []
    for d in conn.execute(
            "SELECT d.id, d.raw_content, s.lab_id FROM raw_documents d"
            " JOIN sources s ON s.id = d.source_id"
            " WHERE s.lab_id IS NOT NULL AND s.purpose='register'"):
        if d["id"] not in page_cache:
            page_cache[d["id"]] = html_to_text(d["raw_content"])
        if contains_verbatim(page_cache[d["id"]], name):
            hits.append((d["id"], d["lab_id"]))
    seen_labs = set()
    for doc_id, lab_id in hits:
        if lab_id in seen_labs:
            continue
        seen_labs.add(lab_id)
        ev = storage.insert_evidence(
            conn, doc_id, json.dumps({"kind": "page_text", "match": name}),
            name, "exact", 1.0)
        conn.execute(
            "INSERT INTO affiliations (person_id, lab_id, role, basis,"
            " observed_at, evidence_id) VALUES (?,?,?,?,?,?)",
            (person_id, lab_id, None, "page_verbatim", storage.now_utc(), ev))
    if seen_labs:
        labs = [conn.execute("SELECT name FROM labs WHERE id=?",
                             (i,)).fetchone()["name"] for i in seen_labs]
        return "affiliated (page): " + ", ".join(labs)

    inferred = _seed_labs_of(conn, candidate_row)
    if len(inferred) == 1:
        lab_id = inferred.pop()
        conn.execute(
            "INSERT INTO affiliations (person_id, lab_id, role, basis,"
            " observed_at, evidence_id) VALUES (?,?,?,?,?,?)",
            (person_id, lab_id, None, "coauthor_inference", storage.now_utc(),
             candidate_row["evidence_id"]))
        lab = conn.execute("SELECT name FROM labs WHERE id=?",
                           (lab_id,)).fetchone()["name"]
        return f"affiliated (inferred from co-authorship): {lab}"

    conn.execute(
        "INSERT INTO affiliations (person_id, lab_id, role, observed_at,"
        " evidence_id) VALUES (?,?,?,?,?)",
        (person_id, None, None, storage.now_utc(), candidate_row["evidence_id"]))
    return ("ambiguous seed labs (absence recorded)" if inferred
            else "no lab evidence (absence recorded)")


def _promote(conn, c, discovered_via: str, page_cache: dict) -> str:
    """Create person + identity + best-basis affiliation for an approved
    candidate. Identity tier follows the method C4 expects: manual ->
    manual_approved, auto_approved -> corroborated (every candidate is)."""
    conn.execute("UPDATE person_candidates SET status='approved', reviewed_at=? WHERE id=?",
                 (storage.now_utc(), c["id"]))
    cur = conn.execute(
        "INSERT INTO people (canonical_name, seniority_tier, discovered_via,"
        " first_seen_at) VALUES (?,?,?,?)",
        (c["name"], None, discovered_via, storage.now_utc()))
    person_id = cur.lastrowid
    tier, method = (("manual_approved", "manual") if discovered_via == "manual"
                    else ("corroborated", "coauthor_overlap"))
    conn.execute(
        "INSERT OR IGNORE INTO identities (person_id, platform, handle,"
        " confidence_tier, resolution_method, evidence_id) VALUES (?,?,?,?,?,?)",
        (person_id, "arxiv", c["name"], tier, method, c["evidence_id"]))
    return _affiliation_for(conn, person_id, c, page_cache)


def review(conn: sqlite3.Connection, ids: list[int], decision: str) -> None:
    """Ad-hoc human decision. Writes the name into register_overrides.yml so it
    survives DB rebuilds (fixing the old wart where re-running expand lost
    approvals), then applies it immediately. A manual approve is a human override
    and bypasses the slate/threshold; name hygiene (C9) still holds."""
    page_cache: dict = {}
    raw = _load_raw_overrides()
    for cid in ids:
        c = conn.execute("SELECT * FROM person_candidates WHERE id=?", (cid,)).fetchone()
        if not c:
            print(f"  [{cid}] not found")
            continue
        if decision == "approved":
            if not valid_candidate_name(c["name"]):
                print(f"  [{cid}] {c['name']!r} REFUSED: fails name-hygiene gate"
                      f" (reject it instead)")
                continue
            if c["name"] not in raw["approve"]:
                raw["approve"].append(c["name"])
            note = ("  " + _promote(conn, c, "manual", page_cache)
                    if c["status"] == "pending" else "  (already approved)")
            print(f"  [{cid}] {c['name']} -> approved{note}")
        else:
            if c["name"] not in raw["reject"]:
                raw["reject"].append(c["name"])
            conn.execute("UPDATE person_candidates SET status='rejected', reviewed_at=? WHERE id=?",
                         (storage.now_utc(), cid))
            print(f"  [{cid}] {c['name']} -> rejected")
    _save_raw_overrides(raw)
    conn.commit()


def auto_approve(conn: sqlite3.Connection) -> dict:
    """Deterministic promotion, re-applied every run. Overrides always
    win; otherwise a candidate is promoted iff it is valid and in the top-K slate
    of some lab it's attributable to. Corroboration is guaranteed by construction
    (every queue row comes from a corroborated paper). Idempotent: already-
    approved candidates aren't pending, so they're never re-promoted."""
    overrides = load_overrides()
    all_cands = [dict(r) for r in conn.execute("SELECT * FROM person_candidates")]
    slate = _slate(conn, all_cands)  # over ALL candidates -> caps at K per lab
    page_cache: dict = {}
    approved = vetoed = 0
    for c in all_cands:
        if c["status"] != "pending":
            continue
        key = name_key(c["name"])
        if key in overrides["reject"]:
            conn.execute("UPDATE person_candidates SET status='rejected', reviewed_at=? WHERE id=?",
                         (storage.now_utc(), c["id"]))
            vetoed += 1
            continue
        if not valid_candidate_name(c["name"]):      # C9 holds even for overrides
            continue
        if key not in overrides["approve"] and c["id"] not in slate:
            continue
        _promote(conn, c, "auto_approved", page_cache)
        approved += 1
    conn.commit()
    print(f"auto-approve [{auto_approve_rule()}]: {approved} approved, {vetoed} vetoed")
    return {"approved": approved, "vetoed": vetoed}
