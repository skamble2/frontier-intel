"""Seed the register: the tracked labs and their founding people.

Seeding is gated on a VERBATIM name match in a fetched lab page - a
person enters the register only with evidence behind them.

The seed data itself (labs, pages, people, X-handle candidates) lives in
config/register_seeds.yml alongside register_overrides.yml: the tracked
universe is a judgement call, not code, so editing it must not be a code
change. This module keeps the same LABS / LAB_PAGES / SEED_PEOPLE /
PERSON_PAGES shapes it always exposed."""
import json
import sqlite3
import urllib.parse
from functools import lru_cache

import yaml

from fli import storage
from fli.core.http import FetchError, http_get
from fli.core.paths import SEEDS_PATH
from fli.core.text import contains_verbatim, html_to_text


@lru_cache(maxsize=1)
def load_seeds() -> dict:
    """Parse config/register_seeds.yml once. A missing or malformed file is a
    hard error - there is deliberately no hardcoded fallback list."""
    if not SEEDS_PATH.exists():
        raise FileNotFoundError(
            f"seed file not found: {SEEDS_PATH}\nIt holds the tracked labs, "
            f"seed people and X-handle candidates, and is required.")
    raw = yaml.safe_load(SEEDS_PATH.read_text(encoding="utf-8"))
    for key in ("labs", "seed_people", "person_pages", "x_candidates",
                "lab_bio_terms"):
        if key not in raw:
            raise ValueError(f"{SEEDS_PATH}: missing key `{key}`")
    return raw


_seeds = load_seeds()

# name, is_public_company, parent_ticker
LABS = [(l["name"], l["is_public_company"], l["parent_ticker"])
        for l in _seeds["labs"]]

LAB_PAGES = {l["name"]: [(p["url"], p["channel"]) for p in l["pages"]]
             for l in _seeds["labs"]}

SEED_PEOPLE = [(p["lab"], p["name"], p["role"], p["tier"])
               for p in _seeds["seed_people"]]

PERSON_PAGES = {name: [(p["url"], p["channel"]) for p in pages]
                for name, pages in _seeds["person_pages"].items()}


def seed_labs(conn: sqlite3.Connection) -> None:
    for name, is_public, ticker in LABS:
        if not conn.execute("SELECT 1 FROM labs WHERE name=?", (name,)).fetchone():
            conn.execute("INSERT INTO labs (name, is_public_company, parent_ticker)"
                         " VALUES (?,?,?)", (name, is_public, ticker))
    conn.commit()


def _fetch_pages(conn, page_list, source_label: str,
                 lab_id: int | None = None) -> list[tuple[int, str, str]]:
    """Fetch and store a [(url, channel)] list. Every attempt gets a
    fetch_log row, including the decode note. Returns [(doc_id, url, text)]."""
    out = []
    for url, channel in page_list:
        source_id = storage.upsert_source(
            conn, "blog" if "wikipedia" not in url else "newsroom",
            f"{source_label}: {urllib.parse.urlparse(url).netloc}", url,
            lab_id=lab_id, channel=channel, purpose="register")
        try:
            html, decode_note = http_get(url)
        except FetchError as e:
            storage.log_fetch(conn, source_id, "error", detail=f"{url}: {e}")
            continue
        doc_id, is_new = storage.store_page(conn, source_id, "blog", url, html, None)
        storage.log_fetch(conn, source_id, "ok", items_found=1 if is_new else 0,
                          detail=f"{url};{decode_note}" + ("" if is_new else ";hash-dup"))
        out.append((doc_id, url, html_to_text(html)))
    return out


def seed_people(conn: sqlite3.Connection) -> None:
    """Register each seed whose name verifies verbatim on a stored page.
    Creates person + affiliation + identity, all pointing at the evidence."""
    seed_labs(conn)
    pages: dict[str, list[tuple[int, str, str]]] = {}

    for lab_name, person_name, role_hint, tier in SEED_PEOPLE:
        lab = conn.execute("SELECT id FROM labs WHERE name=?", (lab_name,)).fetchone()
        if lab_name not in pages:
            pages[lab_name] = _fetch_pages(conn, LAB_PAGES[lab_name],
                                           f"{lab_name} page", lab_id=lab["id"])

        # first page containing the name wins; person-level fallback pages
        # are fetched only when every lab page misses
        candidates = list(pages[lab_name])
        hit = next(((d, u, t) for d, u, t in candidates
                    if contains_verbatim(t, person_name)), None)
        if hit is None and person_name in PERSON_PAGES:
            extra = _fetch_pages(conn, PERSON_PAGES[person_name],
                                 f"{person_name} page", lab_id=lab["id"])
            candidates += extra
            hit = next(((d, u, t) for d, u, t in extra
                        if contains_verbatim(t, person_name)), None)
        if not candidates:
            storage.log_rejection(conn, None, "stage1", "seed_page_unfetchable",
                                  f"{lab_name}: no fetchable page for '{person_name}'")
            print(f"  SKIP {person_name} ({lab_name}): no fetchable page")
            continue
        if hit is None:
            urls = ", ".join(u for _, u, _ in candidates)
            storage.log_rejection(conn, candidates[0][0], "stage1",
                                  "seed_name_not_on_page",
                                  f"'{person_name}' not verbatim on any of: {urls}")
            print(f"  SKIP {person_name} ({lab_name}): name not verbatim on {urls}")
            continue
        doc_id, url, text = hit

        if conn.execute("SELECT 1 FROM people WHERE canonical_name=?",
                        (person_name,)).fetchone():
            print(f"  ok   {person_name} ({lab_name}): already registered")
            continue

        role = role_hint if (role_hint and contains_verbatim(text, role_hint)) else None
        evidence_id = storage.insert_evidence(
            conn, doc_id,
            json.dumps({"kind": "page_text", "match": person_name}),
            person_name, "exact", 1.0)
        cur = conn.execute(
            "INSERT INTO people (canonical_name, seniority_tier, discovered_via,"
            " first_seen_at) VALUES (?,?,?,?)",
            (person_name, tier, "seed", storage.now_utc()))
        person_id = cur.lastrowid
        conn.execute(
            "INSERT INTO affiliations (person_id, lab_id, role, observed_at, evidence_id)"
            " VALUES (?,?,?,?,?)",
            (person_id, lab["id"], role, storage.now_utc(), evidence_id))
        conn.execute(
            "INSERT OR IGNORE INTO identities (person_id, platform, handle, confidence_tier,"
            " resolution_method, evidence_id) VALUES (?,?,?,?,?,?)",
            (person_id, "lab_page", f"{person_name} @ {url}", "verbatim", "exact",
             evidence_id))
        conn.commit()
        print(f"  ADD  {person_name} ({lab_name})"
              f" role={role or 'NULL(unverified)'} via {url}")
