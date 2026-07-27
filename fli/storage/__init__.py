"""Persistence layer (SQLite). No domain logic lives here.

`fli.storage` is the public surface, `fli.storage.db` the implementation.
Re-exported explicitly rather than with `import *`: this is the contract every
other layer codes against, so it should be readable in one screen.
"""
from fli.storage import db  # noqa: F401
from fli.storage.db import (DEFAULT_DB, SCHEMA_PATH,
                            backfill_attribution_from_source,
                            backfill_event_entities, connect, content_hash,
                            init_db, insert_event_entity, insert_evidence,
                            insert_insight, log_fetch, log_llm_call,
                            log_rejection, now_utc, store_document, store_page,
                            upsert_source)

__all__ = [
    "DEFAULT_DB", "SCHEMA_PATH", "backfill_attribution_from_source",
    "backfill_event_entities", "connect", "content_hash", "db", "init_db",
    "insert_event_entity", "insert_evidence", "insert_insight", "log_fetch",
    "log_llm_call", "log_rejection", "now_utc", "store_document", "store_page",
    "upsert_source",
]
