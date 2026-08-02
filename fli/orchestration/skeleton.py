"""End-to-end walking skeleton: one document -> one cited insight."""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from fli import storage
from fli.ingestion import manual as ingestion
from fli.knowledge import filtering as filter1
from fli.ops import tracing
from fli.knowledge.extraction import run_stage2
from fli.ops.llm import LLM, load_dotenv
from fli.knowledge.register import seed_labs
from fli.core.paths import ROOT


def render_insight(conn: sqlite3.Connection, insight_id: int) -> str:
    row = conn.execute("""
        SELECT i.claim, i.event_type, l.name AS lab, e.verbatim_content,
               e.verification, d.url, d.published_at
        FROM insights i
        JOIN evidence e ON e.id = i.evidence_id
        JOIN raw_documents d ON d.id = e.document_id
        LEFT JOIN labs l ON l.id = i.attributed_lab_id
        WHERE i.id = ?""", (insight_id,)).fetchone()
    return (f"[{row['event_type']}] {row['lab'] or 'unattributed'}: {row['claim']}\n"
            f'  evidence ({row["verification"]}): "{row["verbatim_content"][:160]}..."\n'
            f"  source: {row['url']} ({row['published_at']})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(storage.DEFAULT_DB))
    ap.add_argument("--fixture",
                    default=str(ROOT / "fixtures" / "anthropic_news_claude-sonnet-5.md"))
    args = ap.parse_args()

    load_dotenv()
    if tracing.setup():
        print("tracing: OpenInference spans -> Phoenix (FLI_TRACING on)")
    conn = storage.connect(Path(args.db))
    storage.init_db(conn)
    seed_labs(conn)

    doc_id = ingestion.ingest_fixture(conn, Path(args.fixture))
    print(f"ingested document id={doc_id}")

    passed, reason = filter1.stage1(conn, doc_id)
    if not passed:
        print(f"stage1 rejected: {reason}")
        return

    llm = LLM(conn)
    insight_ids = run_stage2(conn, llm, doc_id)
    if not insight_ids:
        print("stage2 produced no insight (see rejections table)")
        return

    print(f"\n=== INSIGHTS ({len(insight_ids)}) ===")
    for iid in insight_ids:
        print(render_insight(conn, iid))

    cost = conn.execute("SELECT count(*) n, sum(cost_usd) c, sum(input_tokens) i,"
                        " sum(output_tokens) o FROM llm_calls").fetchone()
    print(f"\nllm calls: {cost['n']}  tokens in/out: {cost['i']}/{cost['o']}"
          f"  cost: ${cost['c']:.4f}")


if __name__ == "__main__":
    main()
