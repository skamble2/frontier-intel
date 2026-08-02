"""Document ingestion."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fli import storage


def parse_fixture(path: Path) -> tuple[dict, str]:
    """Parse a fixture file with front matter. Returns (meta, body)."""
    text = path.read_text()
    if not text.startswith("---"):
        raise ValueError(f"{path}: missing front matter")
    _, fm, body = text.split("---", 2)
    meta = {}
    for line in fm.strip().splitlines():
        k, _, v = line.partition(":")
        meta[k.strip()] = v.strip()
    return meta, body.strip()


def ingest_fixture(conn: sqlite3.Connection, path: Path) -> int:
    """Ingest one fixture. Returns document_id. Logs the fetch either way."""
    meta, body = parse_fixture(path)
    lab_name = meta.get("lab")
    lab_row = conn.execute("SELECT id FROM labs WHERE name = ?", (lab_name,)).fetchone()
    source_id = storage.upsert_source(
        conn, meta["source_type"], f"{lab_name} {meta['source_type']}",
        meta["url"].rsplit("/", 1)[0],
        lab_id=lab_row["id"] if lab_row else None,
        channel="official")
    doc_id, is_new = storage.store_document(
        conn, source_id, meta["source_type"], meta["url"], body,
        meta.get("published_at"))
    storage.log_fetch(conn, source_id, "ok", items_found=1 if is_new else 0,
                      detail=f"replay:{path.name}" + ("" if is_new else " (hash-dup)"))
    return doc_id
