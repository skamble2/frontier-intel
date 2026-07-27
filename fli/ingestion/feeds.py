"""Feed ingestion: RSS/Atom feeds, GitHub Atom, sitemap-driven pages, arXiv.

Each feed entry (or fetched page, or arXiv paper) becomes one immutable
document, deduped by content hash on arrival. Every fetch attempt is logged.

Run:  python -m fli.cli ingest [--db PATH]
"""
from __future__ import annotations

import sqlite3
import time
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from fli import storage
from fli.core.text import html_to_text
from fli.core.http import FetchError, http_get
from fli.core.config import (ARXIV_DELAY_S, BLOG_BODY_MIN,
                            MAX_ENTRIES_PER_FEED, MAX_SITEMAP_PAGES)

ATOM = "{http://www.w3.org/2005/Atom}"
SM = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}"

# (lab, source_type, kind, url) — all URLs verified fetchable 2026-07-23.
# Known gaps, by measurement: Anthropic/Mistral/Qwen/Meta serve no blog RSS
# at standard paths (sitemap or substitute used); Meta AI's own blog has only
# a gzipped sitemap and is substituted by the Engineering feed.
# JS-rendering wall (measured 2026-07-24): OpenAI blog and Mistral newsroom
# render article bodies client-side, so http_get returns only a shell — the
# body-fetch (thin blogs) and html_to_text (newsroom) recover just the
# headline+teaser, and the classifier then discards the thin ones (e.g. the
# GPT-5.6 launch). Documented coverage trade-off, NOT an _hydrated_body bug;
# closing it would need headless-browser rendering (deliberately out of scope).
FEEDS = [
    ("OpenAI",          "blog",   "feed",    "https://openai.com/news/rss.xml"),
    ("Google DeepMind", "blog",   "feed",    "https://deepmind.google/blog/rss.xml"),
    ("Google DeepMind", "blog",   "feed",    "https://blog.google/technology/google-deepmind/rss/"),
    ("Qwen",            "blog",   "feed",    "https://qwenlm.github.io/blog/index.xml"),
    ("Meta AI",         "blog",   "feed",    "https://engineering.fb.com/feed/"),
    ("Anthropic",       "newsroom", "sitemap", "https://www.anthropic.com/sitemap.xml"),
    ("Mistral",         "newsroom", "sitemap", "https://mistral.ai/sitemap-index.xml"),
    ("DeepSeek",        "github", "feed",    "https://github.com/deepseek-ai/DeepSeek-V3/releases.atom"),
    # Qwen3 publishes no releases (0 entries, 4 empty fetches); Qwen-Agent does
    ("Qwen",            "github", "feed",    "https://github.com/QwenLM/Qwen-Agent/releases.atom"),
    ("Mistral",         "github", "feed",    "https://github.com/mistralai/mistral-inference/releases.atom"),
    ("OpenAI",          "github", "feed",    "https://github.com/openai/openai-python/releases.atom"),
    ("Anthropic",       "github", "feed",    "https://github.com/anthropics/anthropic-sdk-python/releases.atom"),
    # releases, not commits: the commit feed was 70% git chatter (measured)
    ("Meta AI",         "github", "feed",    "https://github.com/meta-llama/llama-models/releases.atom"),
]

# sitemap URL -> page-path prefix worth ingesting
SITEMAP_PREFIX = {
    "https://www.anthropic.com/sitemap.xml": "https://www.anthropic.com/news/",
    "https://mistral.ai/sitemap-index.xml": "https://mistral.ai/news",
}

# Two paper streams. abs: (mention) queries are the recall net — 99% of their
# results are third parties writing ABOUT the labs (measured 126/127), so
# stage 1 gates arXiv docs on authorship: a tracked person or a collective
# author string must appear in the author list. au: (authorship) queries
# anchor the stream on the labs' own output; only collectives that actually
# return papers are queried (measured 2026-07-23 — Anthropic, Llama Team and
# Meta AI return 0: those labs publish under individual names, which the
# authorship gate matches via the register).
ARXIV_QUERY_LABS = ["OpenAI", "Anthropic", "DeepMind", "Meta AI",
                    "Mistral", "DeepSeek", "Qwen"]

ARXIV_AUTHOR_QUERIES = [
    ("DeepSeek",        'DeepSeek-AI'),
    ("Qwen",            'Qwen Team'),
    ("Google DeepMind", 'Gemini Team'),
    ("OpenAI",          'OpenAI'),
    ("Mistral",         'Mistral AI'),
]


def _norm_date(s: str | None) -> str | None:
    if not s:
        return None
    try:
        return parsedate_to_datetime(s).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return s  # already ISO (Atom) or unparseable; keep as-is


def parse_feed(xml_text: str) -> list[dict]:
    """Parse RSS 2.0 or Atom into [{title, link, published, content}]."""
    root = ET.fromstring(xml_text)
    out = []
    if root.tag.endswith("rss"):
        for it in root.iter("item"):
            out.append({
                "title": (it.findtext("title") or "").strip(),
                "link": (it.findtext("link") or "").strip(),
                "published": _norm_date(it.findtext("pubDate")),
                "content": (it.findtext(f"{CONTENT_NS}encoded")
                            or it.findtext("description") or "").strip(),
            })
    else:
        for e in root.iter(f"{ATOM}entry"):
            link_el = e.find(f"{ATOM}link")
            out.append({
                "title": (e.findtext(f"{ATOM}title") or "").strip(),
                "link": link_el.get("href", "") if link_el is not None else "",
                "published": e.findtext(f"{ATOM}published") or e.findtext(f"{ATOM}updated"),
                "content": (e.findtext(f"{ATOM}content")
                            or e.findtext(f"{ATOM}summary") or "").strip(),
            })
    return out[:MAX_ENTRIES_PER_FEED]


def _entry_doc(entry: dict) -> str:
    """Deterministic serialization of one feed entry; the stored document."""
    return (f"{entry['title']}\n{entry['link']}\n{entry['published'] or ''}"
            f"\n\n{entry['content']}")


def _hydrated_body(entry: dict, source_type: str) -> str:
    """Body to store for a feed entry. Blog feeds often carry only a teaser;
    when the entry body is thin, fetch the linked article and use its visible
    text if richer. GitHub release notes ARE the content and are never
    re-fetched; a failed fetch falls back to the teaser. JS-rendered sites
    (OpenAI) return a shell whose text isn't richer than the teaser, so the
    teaser is kept by design — see the JS-rendering-wall note on FEEDS."""
    body = entry["content"]
    if source_type != "blog" or len(body) >= BLOG_BODY_MIN or not entry["link"]:
        return body
    try:
        html, _ = http_get(entry["link"])
    except FetchError:
        return body
    fetched = html_to_text(html)
    return fetched if len(fetched) > len(body) else body


def _lab_id(conn, lab_name: str) -> int | None:
    row = conn.execute("SELECT id FROM labs WHERE name=?", (lab_name,)).fetchone()
    return row["id"] if row else None


def ingest_feed(conn, lab: str, source_type: str, url: str) -> tuple[int, int]:
    """One RSS/Atom feed. Returns (entries_seen, new_docs)."""
    source_id = storage.upsert_source(
        conn, source_type, f"{lab} {source_type}: {urllib.parse.urlparse(url).netloc}",
        url, lab_id=_lab_id(conn, lab), channel="official")
    try:
        xml_text, decode_note = http_get(url)
        entries = parse_feed(xml_text)
    except (FetchError, ET.ParseError) as e:
        storage.log_fetch(conn, source_id, "error", detail=f"{url}: {e}")
        return 0, 0
    new = 0
    for entry in entries:
        entry["content"] = _hydrated_body(entry, source_type)
        _, is_new = storage.store_document(
            conn, source_id, source_type, entry["link"] or url,
            _entry_doc(entry), entry["published"])
        new += is_new
    status = "ok" if entries else "empty"
    storage.log_fetch(conn, source_id, status, items_found=new,
                      detail=f"{url};{decode_note};{len(entries)} entries, {new} new")
    return len(entries), new


def _sitemap_pages(xml_text: str, prefix: str) -> list[tuple[str, str | None]]:
    """(url, lastmod) pairs under prefix, newest first. Follows one level of
    sitemap-index nesting."""
    root = ET.fromstring(xml_text)
    if root.tag == f"{SM}sitemapindex":
        pages = []
        for child in root.iter(f"{SM}sitemap"):
            loc = child.findtext(f"{SM}loc")
            if not loc:
                continue
            try:
                child_xml, _ = http_get(loc.strip())
                pages += _sitemap_pages(child_xml, prefix)
            except (FetchError, ET.ParseError):
                continue
        return sorted(pages, key=lambda p: p[1] or "", reverse=True)
    pages = []
    for u in root.iter(f"{SM}url"):
        loc = (u.findtext(f"{SM}loc") or "").strip()
        if loc.startswith(prefix):
            pages.append((loc, u.findtext(f"{SM}lastmod")))
    return sorted(pages, key=lambda p: p[1] or "", reverse=True)


def ingest_sitemap(conn, lab: str, url: str) -> tuple[int, int]:
    """Sitemap-driven: newest pages under the configured prefix are fetched
    and stored as documents. Returns (pages_tried, new_docs)."""
    prefix = SITEMAP_PREFIX[url]
    source_id = storage.upsert_source(
        conn, "newsroom", f"{lab} newsroom: {urllib.parse.urlparse(url).netloc}",
        url, lab_id=_lab_id(conn, lab), channel="official")
    try:
        xml_text, _ = http_get(url)
        pages = _sitemap_pages(xml_text, prefix)[:MAX_SITEMAP_PAGES]
    except (FetchError, ET.ParseError) as e:
        storage.log_fetch(conn, source_id, "error", detail=f"{url}: {e}")
        return 0, 0
    if not pages:
        storage.log_fetch(conn, source_id, "empty", items_found=0,
                          detail=f"no pages under {prefix}")
        return 0, 0
    new = 0
    for page_url, lastmod in pages:
        try:
            html, _ = http_get(page_url)
        except FetchError as e:
            storage.log_fetch(conn, source_id, "error", detail=f"{page_url}: {e}")
            continue
        _, is_new = storage.store_page(conn, source_id, "newsroom",
                                       page_url, html, lastmod)
        new += is_new
    storage.log_fetch(conn, source_id, "ok", items_found=new,
                      detail=f"{len(pages)} pages under {prefix}, {new} new")
    return len(pages), new


def ingest_arxiv(conn, lab: str, field: str = "abs",
                 term: str | None = None) -> tuple[int, int]:
    """Recent arXiv papers: field='abs' (mentions the lab) or field='au'
    (authored by a collective). One document per paper entry.
    Returns (entries_seen, new_docs)."""
    q = urllib.parse.urlencode({
        "search_query": f'{field}:"{term or lab}"',
        "sortBy": "submittedDate", "sortOrder": "descending",
        "max_results": 20})
    url = f"http://export.arxiv.org/api/query?{q}".replace("http://", "https://")
    # au: queries belong to a lab (authorship); abs: mention queries do not
    source_id = storage.upsert_source(
        conn, "arxiv", f'arXiv {field}:"{term or lab}"', url, channel="official",
        lab_id=_lab_id(conn, lab) if field == "au" else None)
    try:
        xml_text, _ = http_get(url)
        root = ET.fromstring(xml_text)
    except (FetchError, ET.ParseError) as e:
        storage.log_fetch(conn, source_id, "error", detail=str(e))
        return 0, 0
    entries = root.findall(f"{ATOM}entry")
    if not entries:
        storage.log_fetch(conn, source_id, "empty", items_found=0, detail=url)
        return 0, 0
    new = 0
    for e in entries:
        paper_id = (e.findtext(f"{ATOM}id") or "").strip()
        doc = "\n".join([
            (e.findtext(f"{ATOM}title") or "").strip(),
            paper_id,
            (e.findtext(f"{ATOM}published") or "").strip(),
            "authors: " + "; ".join(a.findtext(f"{ATOM}name", "")
                                    for a in e.findall(f"{ATOM}author")),
            "",
            " ".join((e.findtext(f"{ATOM}summary") or "").split()),
        ])
        _, is_new = storage.store_document(
            conn, source_id, "arxiv", paper_id or url, doc,
            e.findtext(f"{ATOM}published"))
        new += is_new
    storage.log_fetch(conn, source_id, "ok", items_found=new,
                      detail=f"{len(entries)} papers, {new} new")
    return len(entries), new


def ingest_all(conn: sqlite3.Connection) -> dict:
    """Run every configured source. Returns counters for the run summary."""
    seen = new = 0
    for lab, source_type, kind, url in FEEDS:
        if kind == "feed":
            s, n = ingest_feed(conn, lab, source_type, url)
        else:
            s, n = ingest_sitemap(conn, lab, url)
        print(f"  {lab:<16} {source_type:<8} seen={s:<3} new={n:<3} {url}")
        seen, new = seen + s, new + n
    arxiv_jobs = ([(lab, "abs", None) for lab in ARXIV_QUERY_LABS]
                  + [(lab, "au", term) for lab, term in ARXIV_AUTHOR_QUERIES])
    for i, (lab, field, term) in enumerate(arxiv_jobs):
        if i:
            time.sleep(ARXIV_DELAY_S)
        s, n = ingest_arxiv(conn, lab, field, term)
        print(f"  {lab:<16} arxiv/{field:<3} seen={s:<3} new={n:<3}")
        seen, new = seen + s, new + n
    return {"items_seen": seen, "docs_new": new,
            "hash_dups": seen - new}


def main() -> None:
    import argparse
    from pathlib import Path
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(storage.DEFAULT_DB))
    args = ap.parse_args()
    conn = storage.connect(Path(args.db))
    storage.init_db(conn)
    stats = ingest_all(conn)
    print(f"\nitems seen: {stats['items_seen']}  new docs: {stats['docs_new']}"
          f"  hash-dups: {stats['hash_dups']}")


if __name__ == "__main__":
    main()
