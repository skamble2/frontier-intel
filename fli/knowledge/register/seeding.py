"""Seed the register: the tracked labs and their founding people.

Seeding is gated on a VERBATIM name match in a fetched lab page - a
person enters the register only with evidence behind them."""
import json
import sqlite3
import urllib.parse

from fli import storage
from fli.core.http import FetchError, http_get
from fli.core.text import contains_verbatim, html_to_text

LABS = [
    # name, is_public_company, parent_ticker
    ("OpenAI",          0, None),
    ("Anthropic",       0, None),
    ("Google DeepMind", 1, "GOOGL"),
    ("Meta AI",         1, "META"),
    ("Mistral",         0, None),
    ("DeepSeek",        0, None),
    ("Qwen",            1, "BABA"),
    ("xAI",             0, None),
]


LAB_PAGES = {
    "OpenAI":          [("https://en.wikipedia.org/wiki/OpenAI", "third_party")],
    "Anthropic":       [("https://www.anthropic.com/company", "official"),
                        ("https://en.wikipedia.org/wiki/Anthropic", "third_party")],
    "Google DeepMind": [("https://deepmind.google/about/", "official"),
                        ("https://en.wikipedia.org/wiki/Google_DeepMind", "third_party")],
    # ai.meta.com/research removed 2026-07-23: refused urllib on 6/6 attempts
    # (HTTP 400; browser-stack only). Coverage via Wikipedia + feeds.
    "Meta AI":         [("https://en.wikipedia.org/wiki/Meta_AI", "third_party")],
    "Mistral":         [("https://mistral.ai/company", "official"),
                        ("https://en.wikipedia.org/wiki/Mistral_AI", "third_party")],
    "DeepSeek":        [("https://www.deepseek.com/", "official"),
                        ("https://en.wikipedia.org/wiki/DeepSeek", "third_party")],
    "Qwen":            [("https://qwenlm.github.io/", "official"),
                        ("https://en.wikipedia.org/wiki/Qwen", "third_party")],
    # x.ai serves its company page client-side, the same wall measured on the
    # OpenAI blog and Mistral newsroom. Both URLs are listed anyway: whichever
    # fails is recorded in fetch_log, so the boundary stays visible in the data
    # instead of being asserted here.
    "xAI":             [("https://x.ai/about", "official"),
                        ("https://en.wikipedia.org/wiki/XAI_(company)", "third_party")],
}


SEED_PEOPLE = [
    ("OpenAI",          "Sam Altman",        "CEO",             "founder"),
    ("OpenAI",          "Greg Brockman",     "President",       "founder"),
    ("OpenAI",          "Jakub Pachocki",    "Chief Scientist", "research_lead"),
    ("OpenAI",          "Mark Chen",         "Chief Research Officer", "research_lead"),
    ("Anthropic",       "Dario Amodei",      "CEO",             "founder"),
    ("Anthropic",       "Daniela Amodei",    "President",       "founder"),
    # research-role founders are tiered research_lead so they anchor co-author
    # expansion: a hands-on Chief Scientist / CTO co-authors real
    # research, unlike a CEO co-signing an institutional paper.
    ("Anthropic",       "Jared Kaplan",      "Chief Science Officer", "research_lead"),
    ("Anthropic",       "Chris Olah",        None,              "research_lead"),
    ("Google DeepMind", "Demis Hassabis",    "CEO",             "founder"),
    ("Google DeepMind", "Koray Kavukcuoglu", "CTO",             "research_lead"),
    ("Meta AI",         "Yann LeCun",        "Chief AI Scientist", "research_lead"),
    ("Meta AI",         "Alexandr Wang",     None,              "research_lead"),
    ("Mistral",         "Arthur Mensch",     "CEO",             "founder"),
    ("Mistral",         "Guillaume Lample",  "Chief Scientist", "research_lead"),
    ("Mistral",         "Timothée Lacroix",  "CTO",             "research_lead"),
    ("DeepSeek",        "Liang Wenfeng",     "CEO",             "founder"),
    ("Qwen",            "Junyang Lin",       None,              "research_lead"),
    # xAI seeds are CANDIDATES, not assertions. Several of the founding
    # research team have since left, and a name here survives only if it
    # appears verbatim on a fetched page — the same gate every other seed
    # passes. Names that fail land in `rejections` as seed_name_not_on_page,
    # so the miss rate is measurable rather than invisible.
    #
    # A departed founder is not a mistake to prune: affiliations are
    # append-only with an `observed_at`, so one person observed at two labs in
    # a window IS the mobility event this system exists to catch.
    ("xAI",             "Elon Musk",         "CEO",             "founder"),
    ("xAI",             "Greg Yang",         None,              "research_lead"),
    ("xAI",             "Jimmy Ba",          None,              "research_lead"),
    ("xAI",             "Yuhuai Wu",         None,              "research_lead"),
    ("xAI",             "Christian Szegedy", None,              "research_lead"),
    ("xAI",             "Igor Babuschkin",   None,              "research_lead"),
]


PERSON_PAGES = {
    "Koray Kavukcuoglu": [("https://blog.google/authors/koray-kavukcuoglu/", "official")],
    "Junyang Lin":       [("https://en.wikipedia.org/wiki/Junyang_Lin", "third_party")],
}


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
