"""Canonical lab-name knowledge in one place.

Two operations legitimately differ — stage 1 asks "does this text mention a
tracked lab" (substring presence), stage 2 asks "resolve the model's lab string
to an id" — but they share the same alias data, which used to be split between
filter1.LAB_ALIASES and extraction's resolver. This module owns that data and
exposes both operations over it, the same discipline as the shared norm().
"""
from __future__ import annotations

import re
import sqlite3

from fli.core.text import norm

# Extra strings that denote a tracked lab but aren't its canonical register name.
# Seeded from measured failures: papers say "DeepMind", the register says
# "Google DeepMind". Keyed by normalized alias -> canonical lab name.
ALIASES = {"deepmind": "Google DeepMind"}

# Suffix/filler tokens that don't disambiguate a lab, dropped before matching so
# "Meta" == "Meta AI" and "Mistral AI" == "Mistral".
_STOPWORDS = frozenset({"ai", "labs", "lab", "inc", "team", "research"})


def _tokens(name: str) -> frozenset[str]:
    # split on any non-alphanumeric, not just whitespace, so hyphenated/slashed
    # names tokenize: 'DeepSeek-AI' -> {deepseek} (matches 'DeepSeek'),
    # 'Meta-Llama' -> {meta, llama}, 'Qwen-Agent' -> {qwen}. Whitespace-only
    # splitting left these as one opaque token that never matched.
    return frozenset(t for t in re.split(r"[^0-9a-z]+", norm(name)) if t) - _STOPWORDS


def mentions_tracked_lab(conn: sqlite3.Connection, text: str) -> bool:
    """True if any canonical lab name or alias appears in text (substring under
    lowercasing). The stage-1 recall net — a mention, not attribution."""
    low = text.lower()
    names = [r["name"] for r in conn.execute("SELECT name FROM labs")]
    return any(n.lower() in low for n in names) or any(a in low for a in ALIASES)


def resolve_lab(conn: sqlite3.Connection, name: str | None) -> int | None:
    """Extracted lab string -> lab id. Alias exact match first, then a unique
    token-subset match ('Meta'->'Meta AI', 'Mistral AI'->'Mistral', 'DeepMind'->
    'Google DeepMind'). Ambiguous (>1 lab) or unmatched -> None."""
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
