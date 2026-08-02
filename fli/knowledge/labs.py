"""Canonical lab-name knowledge in one place."""
from __future__ import annotations

import re
import sqlite3

from fli.core.text import norm

ALIASES = {"deepmind": "Google DeepMind"}

_STOPWORDS = frozenset({"ai", "labs", "lab", "inc", "team", "research"})


def _tokens(name: str) -> frozenset[str]:
    return frozenset(t for t in re.split(r"[^0-9a-z]+", norm(name)) if t) - _STOPWORDS


def mentions_tracked_lab(conn: sqlite3.Connection, text: str) -> bool:
    """True if any canonical lab name or alias appears in text (substring under
    lowercasing)."""
    low = text.lower()
    names = [r["name"] for r in conn.execute("SELECT name FROM labs")]
    return any(n.lower() in low for n in names) or any(a in low for a in ALIASES)


def resolve_lab(conn: sqlite3.Connection, name: str | None) -> int | None:
    """Extracted lab string -> lab id. """
    if not name:
        return None
    canonical = ALIASES.get(norm(name))
    if canonical:
        row = conn.execute("SELECT id FROM labs WHERE name=?", (canonical,)).fetchone()
        if row:
            return row["id"]
    want = _tokens(name)
    if not want:
        return None
    hits = [r["id"] for r in conn.execute("SELECT id, name FROM labs")
            if (t := _tokens(r["name"])) and (want <= t or t <= want)]
    return hits[0] if len(hits) == 1 else None
