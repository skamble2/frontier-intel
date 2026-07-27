"""Daily pipeline: ingest all feeds -> stage-1 filter -> stage-2 extraction
(skipped without an API key; everything else stays deterministic and green)
-> re-observe affiliations -> validation battery. Exit code is the battery's
verdict.

Run:  python -m fli.cli pipeline [--db PATH] [--max-extract N]
Schedule (hourly example):  crontab -e ->
  0 * * * * cd <repo> && ./.venv/bin/python -m fli.cli pipeline >> pipeline.log 2>&1
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fli.validation import checks
from fli.knowledge import expansion as expand
from fli.knowledge import extraction
from fli.ingestion import feeds
from fli.knowledge import filtering as filter1
from fli.knowledge import register
from fli import storage
from fli.ops import tracing
from fli.core.text import norm
from fli.ops.llm import LLM, have_api_key


def near_dup_titles(conn) -> int:
    """Docs whose first line (title) normalizes identically but whose content
    hashes differ — the duplicate rate hash dedup does NOT catch."""
    titles: dict[str, set[str]] = {}
    for r in conn.execute("SELECT content_hash, raw_content FROM raw_documents"
                          " WHERE source_type IN ('blog','newsroom','github')"):
        first = r["raw_content"].split("\n", 1)[0]
        if first.lstrip().startswith("<"):
            continue  # raw HTML page, first line is markup not a title
        title = norm(first)[:120]
        if title:
            titles.setdefault(title, set()).add(r["content_hash"])
    return sum(len(hashes) - 1 for hashes in titles.values() if len(hashes) > 1)


def url_versions(conn) -> int:
    """URLs stored more than once (page changed between fetches)."""
    return conn.execute(
        "SELECT count(*) c FROM (SELECT url FROM raw_documents"
        " GROUP BY url HAVING count(*) > 1)").fetchone()["c"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(storage.DEFAULT_DB))
    ap.add_argument("--max-extract", type=int, default=60,
                    help="stage-2 docs per run (cost cap)")
    args = ap.parse_args()
    conn = storage.connect(Path(args.db))
    storage.init_db(conn)

    if tracing.setup():
        print("tracing: OpenInference spans -> Phoenix (FLI_TRACING on)")

    print("=== ingest ===")
    stats = feeds.ingest_all(conn)

    print("\n=== stage 1 ===")
    s1 = filter1.stage1_all(conn)
    print(f"checked: {s1['checked']}  passed: {s1['passed']}")
    for reason, n in sorted(s1["rejected"].items(), key=lambda x: -x[1]):
        print(f"  rejected {reason}: {n}")
    print(f"  near-duplicates suppressed: {filter1.suppress_near_dups(conn)}")

    # Register discovery + approval run BEFORE extraction, so the layer-below
    # people exist when stage 2 resolves per-insight person attribution.
    print("\n=== co-author expansion (research seeds) ===")
    try:
        expand.expand_coauthors(conn)
    except Exception as e:  # network/arXiv failure must not kill the run
        print(f"expansion unavailable: {e} (deterministic stages unaffected)")

    print("\n=== auto-approve register (balance re-asserted every run) ===")
    register.auto_approve(conn)

    print("\n=== stage 2 (extraction) ===")
    if not have_api_key():
        print("skipped: no ANTHROPIC_API_KEY (deterministic stages unaffected)")
    else:
        try:
            s2 = extraction.extract_all(conn, LLM(conn), max_docs=args.max_extract)
            print(f"attempted: {s2['attempted']}  docs w/ insight:"
                  f" {s2['docs_with_insights']}  insights: {s2['insights']}"
                  f"  rejected: {s2['rejected']}")
            extraction.report_measurements(conn)
            cost = conn.execute("SELECT round(sum(cost_usd),4) c"
                                " FROM llm_calls").fetchone()["c"]
            print(f"cumulative LLM cost: ${cost}")
        except Exception as e:  # API/network failure must not kill the run
            print(f"extraction unavailable: {e} (deterministic stages unaffected)")

    print("\n=== re-observe affiliations ===")
    register.observe(conn)

    print("\n=== run summary ===")
    print(f"items seen: {stats['items_seen']}  new docs: {stats['docs_new']}"
          f"  hash-dups caught: {stats['hash_dups']}")
    print(f"near-dup titles surviving hash dedup (D3): {near_dup_titles(conn)}")
    print(f"urls with multiple stored versions: {url_versions(conn)}")

    print("\n=== checks ===")
    sys.exit(checks.run(conn))


if __name__ == "__main__":
    main()
