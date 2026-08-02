"""Feed ingestion: RSS/Atom feeds, GitHub Atom, sitemap-driven pages, arXiv."""
from __future__ import annotations

import sqlite3
import time
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from fli import storage
from fli.core.text import html_to_text, page_published
from fli.core.http import FetchError, http_get, http_get_rendered
from fli.core.config import (ARXIV_DELAY_S, BLOG_BODY_MIN, JS_WALLED_DOMAINS,
                            MAX_ENTRIES_PER_FEED, MAX_SITEMAP_PAGES)

ATOM = "{http://www.w3.org/2005/Atom}"
SM = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}"

FEEDS = [
    ("OpenAI",          "blog",   "feed",    "https://openai.com/news/rss.xml"),
    ("Google DeepMind", "blog",   "feed",    "https://deepmind.google/blog/rss.xml"),
    ("Google DeepMind", "blog",   "feed",    "https://blog.google/technology/google-deepmind/rss/"),
    ("Qwen",            "blog",   "feed",    "https://qwenlm.github.io/blog/index.xml"),
    ("Meta AI",         "blog",   "feed",    "https://engineering.fb.com/feed/"),
    ("Anthropic",       "newsroom", "sitemap", "https://www.anthropic.com/sitemap.xml"),
    ("Mistral",         "newsroom", "sitemap", "https://mistral.ai/sitemap-index.xml"),
    ("DeepSeek",        "github", "feed",    "https://github.com/deepseek-ai/DeepSeek-V3/releases.atom"),
    ("Qwen",            "github", "feed",    "https://github.com/QwenLM/Qwen-Agent/releases.atom"),
    ("Mistral",         "github", "feed",    "https://github.com/mistralai/mistral-inference/releases.atom"),
    ("OpenAI",          "github", "feed",    "https://github.com/openai/openai-python/releases.atom"),
    ("Anthropic",       "github", "feed",    "https://github.com/anthropics/anthropic-sdk-python/releases.atom"),
    ("Meta AI",         "github", "feed",    "https://github.com/meta-llama/llama-models/releases.atom"),
    ("xAI",             "github", "feed",    "https://github.com/xai-org/grok-1/releases.atom"),
    ("xAI",             "blog",   "feed",    "https://x.ai/news/rss.xml"),
]

SITEMAP_PREFIX = {
    "https://www.anthropic.com/sitemap.xml": "https://www.anthropic.com/news/",
    "https://mistral.ai/sitemap-index.xml": "https://mistral.ai/news",
}

ARXIV_QUERY_LABS = ["OpenAI", "Anthropic", "DeepMind", "Meta AI",
                    "Mistral", "DeepSeek", "Qwen"]
# xAI is deliberately absent from both arXiv lists, and the hole is measured,
# not assumed: x.ai serves 403 to the direct fetch and the rendering proxy,
# au:"xAI" returned 0 entries on every fetch (the lab has no collective arXiv
# author name, unlike DeepSeek-AI / Qwen Team / Gemini Team), and a trial
# abs:"xAI" mention query pulled 20 documents of which 20 were eXplainable-AI
# acronym collisions — every one rejected not_lab_authored by the filter.
# The lab's remaining coverage is its org X account, GitHub releases and
# newsroom mentions; a query that is 100% noise does not earn its fetch cost.

# au: queries match author NAMES, so an org term only works when the lab
# publishes under a collective author (DeepSeek-AI, Qwen Team, Gemini Team —
# each of these has yielded documents).
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
        return s


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


def _js_walled(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return any(host == d or host.endswith("." + d) for d in JS_WALLED_DOMAINS)


def _hydrated_body(entry: dict, source_type: str) -> str:
    """Body to store for a feed entry. """
    body = entry["content"]
    if source_type != "blog" or len(body) >= BLOG_BODY_MIN or not entry["link"]:
        return body
    best = body
    try:
        html, _ = http_get(entry["link"])
        fetched = html_to_text(html)
        if len(fetched) > len(best):
            best = fetched
    except FetchError:
        pass
    if len(best) < BLOG_BODY_MIN and _js_walled(entry["link"]):
        try:
            rendered = http_get_rendered(entry["link"])
            if len(rendered) > len(best):
                best = rendered
        except FetchError:
            pass
    return best


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
    """(url, lastmod) pairs under prefix, newest first. """
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
    """Sitemap-driven: newest pages under the configured prefix are fetched and
    stored as documents."""
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
        published = page_published(html) or lastmod
        _, is_new = storage.store_page(conn, source_id, "newsroom",
                                       page_url, html, published)
        new += is_new
    storage.log_fetch(conn, source_id, "ok", items_found=new,
                      detail=f"{len(pages)} pages under {prefix}, {new} new")
    return len(pages), new


def ingest_arxiv(conn, lab: str, field: str = "abs",
                 term: str | None = None) -> tuple[int, int]:
    """Recent arXiv papers: field='abs' (mentions the lab) or field='au' (authored
    by a collective)."""
    q = urllib.parse.urlencode({
        "search_query": f'{field}:"{term or lab}"',
        "sortBy": "submittedDate", "sortOrder": "descending",
        "max_results": 20})
    url = f"http://export.arxiv.org/api/query?{q}".replace("http://", "https://")
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
