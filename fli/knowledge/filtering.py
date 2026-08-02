"""Stage 1 filter: deterministic and free, runs on every document."""
from __future__ import annotations

import re
import sqlite3

from fli.knowledge import labs
from fli import storage
from fli.core.config import MIN_CHARS, RELEASE_FEED_MIN_CHARS

KILL_PATTERNS = [
    (r"\bwebinar\b.*\bregister\b", "event_promo"),
    (r"\bjob (opening|posting)s?\b|\bwe'?re hiring\b", "job_post"),
]

_CONTINUATION_OPENER = re.compile(
    r"^(while|because|although|though|since|whereas|"
    r"also|plus|and|but|so|however|meanwhile|"
    r"in previous|by combining|to ensure|as part of)\b",
    re.IGNORECASE)

_LEADING_ANAPHOR = re.compile(
    r"^(it|this|these|those|they|he|she)\b(?!\s+(is|are)\s+(a|an|the)\b)"
    r"|^you can also\b",
    re.IGNORECASE)


def social_fragment_reason(body: str) -> str | None:
    """Why this social post is a thread fragment, or None if it stands alone.
    Why this social post is a thread fragment, or None if it stands alone."""
    text = "\n".join(body.split("\n")[2:]).strip()
    if not text:
        return "social_empty"
    if text.startswith("@"):
        return "social_reply_fragment"
    if _CONTINUATION_OPENER.match(text) or _LEADING_ANAPHOR.match(text):
        return "social_thread_continuation"
    return None


def _lab_authored(conn, raw_content: str) -> bool:
    """True if the doc's 'authors:' line names a registered person or a lab
    collective author."""
    from fli.knowledge.expansion import LAB_COLLECTIVE_AUTHORS
    from fli.core.text import name_key
    authors_line = next((ln[len("authors: "):] for ln in raw_content.split("\n")
                         if ln.startswith("authors: ")), None)
    if authors_line is None:
        return True
    authors = [a.strip() for a in authors_line.split(";") if a.strip()]
    tracked = {name_key(r["canonical_name"]) for r in
               conn.execute("SELECT canonical_name FROM people")}
    return any(a in LAB_COLLECTIVE_AUTHORS or name_key(a) in tracked
               for a in authors)


def stage1(conn: sqlite3.Connection, document_id: int) -> tuple[bool, str | None]:
    """Returns (passed, rejection_reason). Logs the rejection if failed."""
    doc = conn.execute("SELECT * FROM raw_documents WHERE id = ?", (document_id,)).fetchone()

    min_chars = MIN_CHARS.get(doc["source_type"], 400)
    if doc["source_type"] == "github":
        src_url = conn.execute("SELECT url FROM sources WHERE id=?",
                               (doc["source_id"],)).fetchone()["url"]
        if src_url.endswith("releases.atom"):
            min_chars = RELEASE_FEED_MIN_CHARS
    if len(doc["raw_content"]) < min_chars:
        storage.log_rejection(conn, document_id, "stage1", "too_short",
                              f"{len(doc['raw_content'])} chars < {min_chars}")
        return False, "too_short"

    content_lower = doc["raw_content"].lower()
    if not labs.mentions_tracked_lab(conn, doc["raw_content"]):
        src = conn.execute("SELECT lab_id FROM sources WHERE id = ?",
                           (doc["source_id"],)).fetchone()
        if not (src and src["lab_id"]):
            storage.log_rejection(conn, document_id, "stage1", "no_tracked_entity", None)
            return False, "no_tracked_entity"

    if doc["source_type"] == "social":
        reason = social_fragment_reason(doc["raw_content"])
        if reason:
            storage.log_rejection(conn, document_id, "stage1", reason, None)
            return False, reason

    if doc["source_type"] == "arxiv" and not _lab_authored(conn, doc["raw_content"]):
        storage.log_rejection(conn, document_id, "stage1", "not_lab_authored", None)
        return False, "not_lab_authored"

    for pattern, reason in KILL_PATTERNS:
        if re.search(pattern, content_lower):
            storage.log_rejection(conn, document_id, "stage1", f"kill_list:{reason}", pattern)
            return False, f"kill_list:{reason}"

    return True, None


def stage1_all(conn: sqlite3.Connection) -> dict:
    """Run stage 1 over every content document without a prior stage-1 rejection.
    Run stage 1 over every content document without a prior stage-1 rejection."""
    docs = conn.execute(
        "SELECT d.id FROM raw_documents d JOIN sources s ON s.id = d.source_id"
        " WHERE s.purpose = 'content' AND d.id NOT IN"
        " (SELECT document_id FROM rejections WHERE stage='stage1'"
        "  AND document_id IS NOT NULL)").fetchall()
    passed = 0
    reasons: dict[str, int] = {}
    for d in docs:
        ok, reason = stage1(conn, d["id"])
        if ok:
            passed += 1
        else:
            reasons[reason] = reasons.get(reason, 0) + 1
    return {"checked": len(docs), "passed": passed, "rejected": reasons}


def suppress_near_dups(conn: sqlite3.Connection) -> int:
    """Suppress one story published at two URLs (e.g. """
    from fli.core.text import norm
    groups: dict[str, list] = {}
    for r in conn.execute(
            "SELECT d.id, d.raw_content, d.published_at FROM latest_documents d"
            " JOIN sources s ON s.id = d.source_id"
            " WHERE s.purpose='content' AND d.source_type IN ('blog','github')"
            " AND d.id NOT IN (SELECT document_id FROM rejections"
            "                  WHERE document_id IS NOT NULL)"):
        first = r["raw_content"].split("\n", 1)[0]
        title = norm(first)[:120]
        if title and not first.lstrip().startswith("<"):
            groups.setdefault(title, []).append(r)
    suppressed = 0
    for docs in groups.values():
        if len(docs) < 2:
            continue
        docs.sort(key=lambda d: (d["published_at"] or "9999", d["id"]))
        keep = docs[0]
        for dup in docs[1:]:
            storage.log_rejection(conn, dup["id"], "stage1", "near_duplicate",
                                  f"duplicate of doc {keep['id']}")
            suppressed += 1
    return suppressed


def main() -> None:
    import argparse
    from pathlib import Path
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(storage.DEFAULT_DB))
    args = ap.parse_args()
    conn = storage.connect(Path(args.db))
    storage.init_db(conn)
    stats = stage1_all(conn)
    print(f"checked: {stats['checked']}  passed: {stats['passed']}")
    for reason, n in sorted(stats["rejected"].items(), key=lambda x: -x[1]):
        print(f"  rejected {reason}: {n}")


if __name__ == "__main__":
    main()
