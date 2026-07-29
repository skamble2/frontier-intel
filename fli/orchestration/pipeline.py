"""Daily pipeline: ingest all feeds -> stage-1 filter -> stage-2 extraction
(skipped without an API key; everything else stays deterministic and green)
-> re-observe affiliations -> cluster -> features -> score (per rubric,
skipped for a rubric with too few judge labels) -> evaluation report ->
validation battery. Exit code is the battery's verdict.

The paid stages stay manual: `judge` (new pairwise labels) and `x` (paid
source) are never run here — scoring trains on the labels already in the DB.

Run:  python -m fli.cli pipeline [--db PATH] [--max-extract N]
Scheduled daily by .github/workflows/pipeline.yml, which commits data/fli.db
and the regenerated report back after a green run.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fli.validation import checks
from fli.validation import evaluation
from fli.knowledge import expansion as expand
from fli.knowledge import extraction
from fli.ingestion import feeds
from fli.knowledge import filtering as filter1
from fli.knowledge import register
from fli.intelligence import clustering
from fli.intelligence import features as featmod
from fli.intelligence import scoring
from fli import storage
from fli.core.rubric import available as available_rubrics
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

    print("\n=== cluster ===")
    c = clustering.cluster_all(conn)
    print(f"clusters: {c['clusters']} over {c['insights']} insights"
          f" ({c['clustered_events']} in multi-event clusters,"
          f" largest={c['largest_cluster']})")

    print("\n=== features ===")
    f = featmod.compute_features(conn)
    print(f"features: {f['features_per_insight']} per insight"
          f" x {f['insights']} insights")

    print("\n=== score (per rubric, existing judge labels only) ===")
    for name in available_rubrics():
        try:
            scoring.print_report(scoring.bakeoff(conn, rubric=name))
        except SystemExit as e:
            # bakeoff refuses to train on <10 labels; a missing rubric's
            # labels must not kill the scheduled run.
            print(f"  {name}: skipped ({e})")

    print("\n=== evaluate (figures + report) ===")
    try:
        evaluation.build(conn)
    except Exception as e:  # a chart must not kill the run
        print(f"evaluation unavailable: {type(e).__name__}: {e}")

    print("\n=== run summary ===")
    print(f"items seen: {stats['items_seen']}  new docs: {stats['docs_new']}"
          f"  hash-dups caught: {stats['hash_dups']}")
    print(f"near-dup titles surviving hash dedup: {near_dup_titles(conn)}")
    print(f"urls with multiple stored versions: {url_versions(conn)}")

    print("\n=== checks ===")
    verdict = checks.run(conn)
    # Checkpoint the WAL back into fli.db before exiting: the -wal/-shm
    # sidecars are gitignored, so a committed DB that hasn't been
    # checkpointed would silently miss this run's writes.
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    sys.exit(verdict)


if __name__ == "__main__":
    main()
