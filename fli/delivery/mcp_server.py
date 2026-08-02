"""MCP server: the read-only intelligence surface for agent clients.

Exposes what a human reader gets from the web UI and CLI — the ranked slate,
claim search, drift status, and the latest digest — as Model Context Protocol
tools over stdio, so Claude Desktop / VS Code agents can query the corpus
directly. Strictly read-only: no tool writes to the DB, spends tokens, or
touches the network. All tools reuse the existing layer functions rather than
re-deriving anything (top_events already applies the slate filter, entailment
rejections, and mechanism gate; drift.build already anchors its window).

The tool bodies are plain functions taking a connection so they are testable
without the mcp SDK installed; the SDK import happens only in build_server().

Register with a client as:  python -m fli.cli mcp [--db PATH]
"""
from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path

from fli import storage
from fli.core.paths import ROOT
from fli.intelligence.scoring import primary_rubric, top_events
from fli.validation import drift

DIGEST_DIR = ROOT / "docs" / "digests"

# What an agent needs to cite an insight; score_components/cluster_id are
# internal ranking detail and stay out of the wire format.
_FIELDS = ("id", "claim", "score", "event_type", "lab", "published_at",
           "url", "source_type", "quote")


def _slim(row: dict) -> dict:
    return {k: row.get(k) for k in _FIELDS}


def slate(conn: sqlite3.Connection, k: int = 10) -> list[dict]:
    """The current top-k slate under the primary rubric — same list the
    digest and web UI show, filters and all."""
    items, _dropped = top_events(conn, k=k, rubric=primary_rubric())
    return [_slim(r) for r in items]


def search(conn: sqlite3.Connection, query: str, k: int = 20) -> list[dict]:
    """Substring search over claims and their verbatim quotes, best score
    first. Matches are raw corpus hits — NOT slate-filtered — so an agent can
    also find items the slate suppressed (duplicates, not-entailed, no-mechanism)."""
    like = f"%{query}%"
    rows = conn.execute(
        "SELECT i.id, i.claim, i.score, i.event_type,"
        " COALESCE(l.name,'(unattributed)') lab, d.published_at, d.url,"
        " d.source_type, ev.verbatim_content quote"
        " FROM insights i"
        " JOIN evidence ev ON ev.id = i.evidence_id"
        " JOIN raw_documents d ON d.id = ev.document_id"
        " LEFT JOIN labs l ON l.id = i.attributed_lab_id"
        " WHERE i.claim LIKE ? OR ev.verbatim_content LIKE ?"
        " ORDER BY i.score DESC LIMIT ?", (like, like, k)).fetchall()
    return [_slim(dict(r)) for r in rows]


def drift_status(conn: sqlite3.Connection, days: int = 14) -> list[dict]:
    """PSI/KS drift rows for the last `days` (anchored to the newest doc)."""
    return drift.build(conn, days)


def latest_digest(persona: str = "") -> dict:
    """Newest digest markdown, optionally filtered to a persona
    (e.g. 'ai_team', 'investment').

    Filenames are `<window-end>-<span>d-<persona>.md`. Ordering is by end date
    then by span as an INTEGER — a plain string sort would rank 100d below 30d.
    Where one end date carries several spans the widest wins, deliberately: an
    agent asking for "the latest digest" is better served the standing picture
    than a narrow window that may legitimately be empty."""
    pattern = f"*{persona}*.md" if persona else "*.md"

    def order(p: Path) -> tuple:
        m = re.match(r"(\d{4}-\d{2}-\d{2})-(\d+)d-", p.name)
        return (m.group(1), int(m.group(2))) if m else (p.name, 0)

    files = sorted(DIGEST_DIR.glob(pattern), key=order)
    if not files:
        return {"error": f"no digest matching {pattern!r} in docs/digests"}
    path = files[-1]
    return {"file": path.name, "content": path.read_text(encoding="utf-8")}


def build_server(db_path: Path | None = None):
    """Wire the tools into an MCPServer. A fresh connection per call keeps
    the long-running server safe against a pipeline run replacing the DB."""
    from mcp.server.mcpserver import MCPServer

    def conn():
        return storage.connect(db_path) if db_path else storage.connect()

    app = MCPServer(
        name="frontier-intel",
        instructions="Read-only access to the frontier-lab intelligence "
                     "corpus: ranked insight slate, claim search, corpus "
                     "drift status, and rendered digests.")

    def _with_conn(fn, *args):
        c = conn()
        try:
            return fn(c, *args)
        finally:
            c.close()

    @app.tool()
    def top_insights(k: int = 10) -> list[dict]:
        """Current top-k intelligence slate (primary rubric, slate-filtered:
        deduped, entailment-checked, mechanism-gated)."""
        return _with_conn(slate, k)

    @app.tool()
    def search_insights(query: str, k: int = 20) -> list[dict]:
        """Search claims and source quotes by substring; returns raw corpus
        matches (not slate-filtered), best score first."""
        return _with_conn(search, query, k)

    @app.tool()
    def corpus_drift(days: int = 14) -> list[dict]:
        """PSI/KS drift report: current window vs corpus history. verdict is
        stable / MODERATE / MAJOR per metric."""
        return _with_conn(drift_status, days)

    @app.tool()
    def get_latest_digest(persona: str = "") -> dict:
        """Latest rendered digest markdown; persona filters by audience
        ('ai_team' or 'investment', empty = newest of any)."""
        return latest_digest(persona)

    return app


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Serve the read-only intelligence surface over MCP stdio.")
    ap.add_argument("--db", type=Path, default=None)
    args = ap.parse_args()
    build_server(args.db).run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
