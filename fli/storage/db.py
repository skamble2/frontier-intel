"""SQLite storage layer. Documents are insert-only and deduped by hash."""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fli.core.paths import DEFAULT_DB, SCHEMA_PATH


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def connect(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Additive column migrations: `CREATE TABLE IF NOT EXISTS` cannot add a column
# to an existing table. No framework — single-user project, one database.
# To retire one, delete the row and its schema.sql column default together.
_MIGRATIONS = [
    # (table, column, DDL fragment)
    ("event_scores", "policy_version", "INTEGER NOT NULL DEFAULT 0"),
]

# Tables whose SHAPE changed. SQLite cannot alter a UNIQUE constraint in place,
# so the table is rebuilt — but only while empty, so no collected data is ever
# destroyed. A non-empty one is left alone and reported.
_REBUILD_IF_EMPTY = [
    # (table, a column the NEW shape has)
    ("pairwise_labels", "labeler"),
]


def _rebuild_empty_tables(conn: sqlite3.Connection) -> list[str]:
    """Runs BEFORE schema.sql, so the dropped table is recreated by it."""
    notes = []
    for table, new_column in _REBUILD_IF_EMPTY:
        exists = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?",
            (table,)).fetchone()[0]
        if not exists:
            continue
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if new_column in cols:
            continue                                   # already the new shape
        rows = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        if rows:
            notes.append(f"WARNING {table} has an outdated shape and {rows} row(s); "
                         f"refusing to rebuild. Migrate it by hand.")
            continue
        conn.execute(f"DROP TABLE {table}")
        notes.append(f"rebuilt empty table {table} (shape changed)")
    if notes:
        conn.commit()
    return notes


def _apply_migrations(conn: sqlite3.Connection) -> list[str]:
    applied = []
    for table, column, ddl in _MIGRATIONS:
        exists = conn.execute(
            "SELECT count(*) c FROM sqlite_master WHERE type='table' AND name=?",
            (table,)).fetchone()[0]
        if not exists:
            continue                      # schema.sql just created it, correctly
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            applied.append(f"{table}.{column}")
    if applied:
        conn.commit()
    return applied


def init_db(conn: sqlite3.Connection) -> None:
    notes = _rebuild_empty_tables(conn)      # must precede schema.sql
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()
    notes += [f"added column {m}" for m in _apply_migrations(conn)]
    for n in notes:
        # Never silent: a schema change to a database holding measured results
        # is something the operator should see happen.
        print(f"migrated: {n}")


def upsert_source(conn, source_type: str, name: str, url: str,
                  lab_id: int | None = None, channel: str = "official",
                  purpose: str = "content") -> int:
    row = conn.execute("SELECT id FROM sources WHERE url = ?", (url,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO sources (source_type, lab_id, name, url, channel, purpose)"
        " VALUES (?,?,?,?,?,?)",
        (source_type, lab_id, name, url, channel, purpose))
    conn.commit()
    return cur.lastrowid


def store_document(conn, source_id: int, source_type: str, url: str,
                   raw_content: str, published_at: str | None) -> tuple[int, bool]:
    """Insert a document unless its content hash already exists.
    Returns (document_id, is_new)."""
    h = content_hash(raw_content)
    row = conn.execute("SELECT id FROM raw_documents WHERE content_hash = ?", (h,)).fetchone()
    if row:
        return row["id"], False
    cur = conn.execute(
        "INSERT INTO raw_documents (source_type, source_id, url, content_hash, raw_content,"
        " published_at, retrieved_at) VALUES (?,?,?,?,?,?,?)",
        (source_type, source_id, url, h, raw_content, published_at, now_utc()))
    conn.commit()
    return cur.lastrowid, True


def store_page(conn, source_id: int, source_type: str, url: str,
               html: str, published_at: str | None) -> tuple[int, bool]:
    """store_document for HTML pages, deduped on extracted TEXT: dynamic
    sites (build hashes, nonces) change bytes on every fetch, so raw-hash
    dedup never fires and versions pile up. If the visible text equals the
    URL's latest stored version, nothing is stored."""
    from fli.core.text import html_to_text
    last = conn.execute("SELECT raw_content FROM latest_documents WHERE url=?",
                        (url,)).fetchone()
    if last and html_to_text(last["raw_content"]) == html_to_text(html):
        row = conn.execute("SELECT id FROM latest_documents WHERE url=?",
                           (url,)).fetchone()
        return row["id"], False
    return store_document(conn, source_id, source_type, url, html, published_at)


def log_fetch(conn, source_id: int, status: str, items_found: int | None = None,
              detail: str | None = None) -> None:
    conn.execute(
        "INSERT INTO fetch_log (source_id, attempted_at, status, items_found, detail)"
        " VALUES (?,?,?,?,?)",
        (source_id, now_utc(), status, items_found, detail))
    conn.commit()


def log_rejection(conn, document_id: int | None, stage: str, reason: str,
                  detail: str | None = None) -> None:
    conn.execute(
        "INSERT INTO rejections (document_id, stage, reason, detail, created_at) VALUES (?,?,?,?,?)",
        (document_id, stage, reason, detail, now_utc()))
    conn.commit()


def insert_evidence(conn, document_id: int, locator: str, verbatim_content: str,
                    verification: str, verification_score: float | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO evidence (document_id, locator, verbatim_content, verification,"
        " verification_score) VALUES (?,?,?,?,?)",
        (document_id, locator, verbatim_content, verification, verification_score))
    conn.commit()
    return cur.lastrowid


def insert_event_entity(conn, event_id: int, entity_kind: str, entity_id: int,
                        role: str, evidence_id: int,
                        basis: str = "model_asserted", commit: bool = True) -> int:
    """One attributed entity of an event, independently cited. entity_kind
    ('person'|'lab') selects which FK the id fills (XOR, enforced by schema).
    basis records confidence: 'model_asserted' vs 'source_inferred'.
    commit=False lets a caller batch this into one transaction with related
    rows a checks invariant spans (e.g. the insight it mirrors)."""
    cur = conn.execute(
        "INSERT INTO event_entities (event_id, entity_kind, person_id, lab_id,"
        " role, basis, evidence_id) VALUES (?,?,?,?,?,?,?)",
        (event_id, entity_kind,
         entity_id if entity_kind == "person" else None,
         entity_id if entity_kind == "lab" else None,
         role, basis, evidence_id))
    if commit:
        conn.commit()
    return cur.lastrowid


def insert_insight(conn, evidence_id: int, event_type: str, claim: str,
                   attributed_lab_id: int | None = None,
                   attributed_person_id: int | None = None,
                   basis: str = "model_asserted") -> int:
    # The insight and its event_entities mirror are one logical write that C11
    # spans, so they share a transaction — a crash can't leave an attributed
    # insight without its cited mirror. `basis` records how the lab was resolved,
    # so a publisher default is never scored as a verbatim one.
    cur = conn.execute(
        "INSERT INTO insights (evidence_id, attributed_person_id, attributed_lab_id,"
        " event_type, claim, created_at) VALUES (?,?,?,?,?,?)",
        (evidence_id, attributed_person_id, attributed_lab_id, event_type, claim, now_utc()))
    event_id = cur.lastrowid
    if attributed_lab_id is not None:
        insert_event_entity(conn, event_id, "lab", attributed_lab_id, "subject",
                            evidence_id, basis, commit=False)
    if attributed_person_id is not None:
        # always model_asserted: no publisher fallback exists for people
        insert_event_entity(conn, event_id, "person", attributed_person_id, "subject",
                            evidence_id, "model_asserted", commit=False)
    conn.commit()
    return event_id


def backfill_event_entities(conn) -> int:
    """One-time, idempotent: mirror existing insights' denormalized attributed_*
    caches into event_entities (same rule as insert_insight). Safe to re-run —
    NOT EXISTS skips events already carrying the entity. Returns rows inserted."""
    before = conn.execute("SELECT count(*) c FROM event_entities").fetchone()["c"]
    conn.execute(
        "INSERT INTO event_entities (event_id, entity_kind, person_id, lab_id, role, evidence_id)"
        " SELECT i.id, 'lab', NULL, i.attributed_lab_id, 'subject', i.evidence_id FROM insights i"
        " WHERE i.attributed_lab_id IS NOT NULL AND NOT EXISTS ("
        "   SELECT 1 FROM event_entities ee WHERE ee.event_id = i.id"
        "   AND ee.entity_kind = 'lab' AND ee.lab_id = i.attributed_lab_id)")
    conn.execute(
        "INSERT INTO event_entities (event_id, entity_kind, person_id, lab_id, role, evidence_id)"
        " SELECT i.id, 'person', i.attributed_person_id, NULL, 'subject', i.evidence_id FROM insights i"
        " WHERE i.attributed_person_id IS NOT NULL AND NOT EXISTS ("
        "   SELECT 1 FROM event_entities ee WHERE ee.event_id = i.id"
        "   AND ee.entity_kind = 'person' AND ee.person_id = i.attributed_person_id)")
    conn.commit()
    after = conn.execute("SELECT count(*) c FROM event_entities").fetchone()["c"]
    return after - before


def backfill_attribution_from_source(conn) -> int:
    """One-time, idempotent: insights the extractor left unattributed adopt the
    document's publisher lab on official first-party channels (same fallback
    run_stage2 now applies inline), then re-mirror into event_entities. For
    events written before that fallback existed. Returns insights newly
    attributed."""
    rows = conn.execute(
        "SELECT i.id, i.evidence_id, s.lab_id FROM insights i"
        " JOIN evidence e ON e.id = i.evidence_id"
        " JOIN raw_documents d ON d.id = e.document_id"
        " JOIN sources s ON s.id = d.source_id"
        " WHERE i.attributed_lab_id IS NULL AND s.channel = 'official'"
        "   AND s.lab_id IS NOT NULL").fetchall()
    for r in rows:
        conn.execute("UPDATE insights SET attributed_lab_id = ? WHERE id = ?",
                     (r["lab_id"], r["id"]))
        insert_event_entity(conn, r["id"], "lab", r["lab_id"], "subject",
                            r["evidence_id"], basis="source_inferred", commit=False)
    conn.commit()
    return len(rows)


def log_llm_call(conn, task: str, model: str, input_tokens: int, output_tokens: int,
                 cost_usd: float) -> None:
    conn.execute(
        "INSERT INTO llm_calls (task, model, input_tokens, output_tokens, cost_usd, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (task, model, input_tokens, output_tokens, cost_usd, now_utc()))
    conn.commit()
