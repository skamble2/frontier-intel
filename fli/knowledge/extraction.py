"""Stage 2 extraction: classify -> extract -> verify.

Every extracted claim must carry a verbatim quote, which is matched back
into the stored document. Claims whose quote does not verify are discarded
and logged — that counter is the hallucination-control number.
"""
from __future__ import annotations

import json
import sqlite3

from pydantic import BaseModel, ValidationError

from fli import storage
from fli.core.text import contains_verbatim, html_to_text, name_key, norm
from fli.knowledge.labs import resolve_lab  # shared lab-name resolver
from fli.ops.llm import LLM
from fli.core.config import MAX_INSIGHTS_PER_DOC

EVENT_TYPES = ["research", "personnel", "release", "infrastructure",
               "benchmark", "open_source", "commercial", "other"]


class Classification(BaseModel):
    event_type: str
    substantive: bool
    reason: str


class ExtractedInsight(BaseModel):
    claim: str
    quote: str            # verbatim from document, verified downstream
    event_type: str
    attributed_lab: str | None = None
    attributed_person: str | None = None


CLASSIFY_SYSTEM = """You classify documents from frontier AI lab channels.
Return ONLY JSON: {"event_type": one of %s,
"substantive": true|false, "reason": "<one line>"}.
substantive=false means: marketing fluff, event promotion, job posting,
or no concrete technical/personnel/product information.""" % EVENT_TYPES

_EXTRACT_TEMPLATE = """You extract the decision-relevant events from a
frontier-AI-lab document for an investment fund's intelligence system.
A document MAY contain several DISTINCT events — e.g. a launch may carry a model
release, its pricing, benchmark results, and safety measures — but MOST contain
one. An arXiv abstract almost always describes a SINGLE research contribution.
Return ONLY JSON:
{"insights": [
  {"claim": "<one-sentence factual claim, no speculation>",
   "quote": "<VERBATIM contiguous quote from the document, 10-60 words, that fully supports the claim>",
   "event_type": one of %s,
   "attributed_lab": "<lab name or null>",
   "attributed_person": "<person name or null>"}
]}
PICK THE QUOTE FIRST, THEN WRITE THE CLAIM FROM IT. Every load-bearing fact in
the claim — each number, date, price, model name, version, actor — must appear
INSIDE the quote you chose. If the decisive number sits in a different sentence
than the story, quote the sentence with the number. A modest claim its quote
fully carries beats a rich claim the quote only half-supports: facts you
remember from elsewhere in the document do not belong in the claim.
Return one object per DISTINCT event, most decision-relevant first, at most %d.
Do NOT split one event into several claims, do NOT invent events the text does
not support, and do NOT pad the list to the maximum — return FEWER when the
document supports fewer. Each quote must be copied character-for-character."""


def _extract_system(max_insights: int) -> str:
    return _EXTRACT_TEMPLATE % (EVENT_TYPES, max_insights)


def _json_of(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        t = t[4:] if t.startswith("json") else t
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        # model wrapped the JSON in prose — take the outermost brace span
        s, e = t.find("{"), t.rfind("}")
        if s != -1 and e > s:
            return json.loads(t[s:e + 1])
        raise


def classify(llm: LLM, content: str) -> Classification:
    """Cheap Haiku gate before the expensive Sonnet extract: it kills
    ~28% of documents, which more than pays for the 6k prefix both stages see."""
    out = llm.call("classify", CLASSIFY_SYSTEM, content[:6000], max_tokens=200)
    return Classification(**_json_of(out))


def extract(llm: LLM, content: str) -> list[ExtractedInsight]:
    """The document's distinct events. The cap scales with available
    evidence (~1 per 1000 chars) so short documents can't quota-fill events;
    the model is told the per-document max and the list is truncated to it.
    Empty list is valid; malformed JSON raises and is logged upstream.

    Truncates at 12k chars: an event described only in a long
    document's tail is currently unreachable.
    """
    max_insights = max(1, min(MAX_INSIGHTS_PER_DOC, len(content) // 1000))
    out = llm.call("extract", _extract_system(max_insights), content[:12000], max_tokens=1500)
    data = _json_of(out)
    return [ExtractedInsight(**x)
            for x in data.get("insights", [])][:max_insights]


def resolve_person(conn: sqlite3.Connection, name: str | None) -> int | None:
    """Extracted person name -> people.id via the order-insensitive name_key
    (same key the register resolves with). Unmatched -> None, so an absence is
    recorded rather than guessed."""
    if not name:
        return None
    key = name_key(name)
    for r in conn.execute("SELECT id, canonical_name FROM people"):
        if name_key(r["canonical_name"]) == key:
            return r["id"]
    return None


def readable_text(raw_content: str) -> str:
    """What stage 2 should actually read: visible text for stored HTML pages,
    the raw body otherwise. Newsroom pages store full HTML, so without this the
    input cap is spent on <head>/nav/chrome instead of the article."""
    return html_to_text(raw_content) if raw_content.lstrip().startswith("<") else raw_content


def verify_quote(raw_content: str, quote: str) -> tuple[str, float] | None:
    """Exact match under the shared normalization (same one checks.py
    re-verifies with, so stored evidence always re-verifies). Tries the raw
    bytes and their visible text, since stage 2 may quote from either.
    Returns (verification_method, score) or None."""
    if contains_verbatim(raw_content, quote) or contains_verbatim(html_to_text(raw_content), quote):
        return ("exact", 1.0)
    return None


def _attribute_lab(conn, ins: ExtractedInsight, src) -> tuple[int | None, str]:
    """Resolve the insight's lab and its basis: the model's named lab
    (model_asserted), else the publisher of an official channel (source_inferred,
    src is the (lab_id, channel) row of the document's source."""
    lab_id = resolve_lab(conn, ins.attributed_lab)
    if lab_id is not None:
        return lab_id, "model_asserted"
    if src and src["channel"] == "official" and src["lab_id"] is not None:
        return src["lab_id"], "source_inferred"
    return None, "model_asserted"


def run_stage2(conn: sqlite3.Connection, llm: LLM, document_id: int) -> list[int]:
    """Full stage 2 for one document. Returns the list of insight ids it
    produced (empty if the document yielded none — reason logged). Idempotent:
    a document that already has any insight is never re-extracted. A document
    can yield several events, each independently quote-verified."""
    existing = conn.execute(
        "SELECT i.id FROM insights i JOIN evidence e ON e.id = i.evidence_id"
        " WHERE e.document_id = ?", (document_id,)).fetchall()
    if existing:
        return [r["id"] for r in existing]

    doc = conn.execute("SELECT * FROM raw_documents WHERE id = ?", (document_id,)).fetchone()
    content = readable_text(doc["raw_content"])

    try:
        cls = classify(llm, content)
    except (ValidationError, json.JSONDecodeError) as e:
        storage.log_rejection(conn, document_id, "stage2", "classify_parse_error", str(e))
        return []
    if not cls.substantive:
        storage.log_rejection(conn, document_id, "stage2", "low_substance", cls.reason)
        return []

    try:
        extracted = extract(llm, content)
    except (ValidationError, json.JSONDecodeError) as e:
        storage.log_rejection(conn, document_id, "stage2", "extract_parse_error", str(e))
        return []

    src = conn.execute(
        "SELECT s.lab_id, s.channel FROM raw_documents d"
        " JOIN sources s ON s.id = d.source_id WHERE d.id = ?", (document_id,)).fetchone()

    insight_ids: list[int] = []
    seen_quotes: set[str] = set()
    for ins in extracted:
        verdict = verify_quote(doc["raw_content"], ins.quote)
        if verdict is None:
            # Per-insight hallucination-control counter. This is the
            # denominator of the quote-verification rate, so it must be logged
            # rather than silently skipped.
            storage.log_rejection(conn, document_id, "verification",
                                  "quote_unverified", ins.quote[:200])
            continue
        qkey = norm(ins.quote)
        if qkey in seen_quotes:      # two events citing the identical span -> one evidence
            storage.log_rejection(conn, document_id, "stage2",
                                  "duplicate_quote_in_doc", ins.quote[:200])
            continue
        seen_quotes.add(qkey)
        method, score = verdict
        para_idx = next((i for i, p in enumerate(doc["raw_content"].split("\n\n"))
                         if ins.quote[:40] in p), None)
        evidence_id = storage.insert_evidence(
            conn, document_id, json.dumps({"paragraph_idx": para_idx}),
            ins.quote, method, score)
        lab_id, basis = _attribute_lab(conn, ins, src)
        person_id = resolve_person(conn, ins.attributed_person)
        # extract's per-event type is authoritative; fall back to classify's
        # document-level type when the model returned an out-of-taxonomy value.
        event_type = ins.event_type if ins.event_type in EVENT_TYPES else (
            cls.event_type if cls.event_type in EVENT_TYPES else "other")
        insight_ids.append(storage.insert_insight(
            conn, evidence_id, event_type, ins.claim,
            attributed_lab_id=lab_id, attributed_person_id=person_id, basis=basis))

    if not insight_ids:
        # document-level: nothing survived (distinct from the per-insight counter)
        detail = extracted[0].quote[:200] if extracted else "no insights returned"
        storage.log_rejection(conn, document_id, "verification", "no_verified_insight", detail)
    return insight_ids


def extract_all(conn: sqlite3.Connection, llm: LLM, max_docs: int = 60) -> dict:
    """Stage 2 over stage-1 survivors that have no insight and no prior
    stage-2 verdict. Only the latest version of each URL is extracted.
    Capped per run so a large backlog cannot surprise the budget.

    Parse errors (classify/extract returned malformed JSON) are TRANSIENT —
    a retry usually succeeds — so they do not permanently exclude a document
    the way substantive rejections do. Retries stay bounded by max_docs."""
    docs = conn.execute(
        "SELECT d.id FROM latest_documents d"
        " JOIN sources s ON s.id = d.source_id"
        " WHERE s.purpose = 'content'"
        " AND d.id NOT IN (SELECT document_id FROM rejections"
        "                  WHERE document_id IS NOT NULL"
        "                  AND reason NOT IN ('classify_parse_error',"
        "                                     'extract_parse_error'))"
        " AND d.id NOT IN (SELECT e.document_id FROM insights i"
        "                  JOIN evidence e ON e.id = i.evidence_id)"
        " ORDER BY d.published_at DESC LIMIT ?", (max_docs,)).fetchall()
    stats = {"attempted": 0, "docs_with_insights": 0, "insights": 0, "rejected": 0}
    for d in docs:
        stats["attempted"] += 1
        ids = run_stage2(conn, llm, d["id"])
        if ids:
            stats["docs_with_insights"] += 1
            stats["insights"] += len(ids)
        else:
            stats["rejected"] += 1
    return stats


def report_measurements(conn: sqlite3.Connection) -> None:
    """Quote-verification rate, event-type distribution, rejection reasons."""
    total = conn.execute("SELECT count(*) c FROM insights").fetchone()["c"]
    # Per-insight failures: each dropped insight is one quote_unverified row,
    # so this is the true kept/(kept+failed) rate, not inflated by multi-
    # insight documents.
    fails = conn.execute("SELECT count(*) c FROM rejections"
                         " WHERE reason='quote_unverified'").fetchone()["c"]
    if total + fails:
        print(f"quote verification (cumulative, per-insight): {total}/{total + fails}"
              f" exact ({100 * total / (total + fails):.0f}%); dropped: {fails}")
    if total:
        print("event types:")
        for r in conn.execute("SELECT event_type, count(*) c FROM insights"
                              " GROUP BY 1 ORDER BY c DESC"):
            print(f"  {r['event_type']}: {r['c']} ({100 * r['c'] / total:.0f}%)")
    for r in conn.execute("SELECT reason, count(*) c FROM rejections"
                          " WHERE stage IN ('stage2','verification')"
                          " GROUP BY 1 ORDER BY c DESC"):
        print(f"  stage2 rejected {r['reason']}: {r['c']}")


def backfill_arxiv_authors(conn: sqlite3.Connection) -> dict:
    """Deterministic person attribution from arXiv author lines. Free.

    The extractor names a person only when the TEXT is about one, so research
    events almost never carry person attribution (14 of 734) even though every
    arXiv document arrives with a machine-readable `authors:` line and the
    register already tracks many of those authors. This closes that gap
    without an LLM: match each listed author against `people` under the same
    order-insensitive name_key the register resolves with, and record the hit
    as an event_entities row with role='author'.

    Distinctions that keep the row honest:
      role   'author', never 'subject' — being on the paper is not the same
             as the event being about you, so attributed_person_id stays NULL.
      basis  'source_inferred' — the link comes from the publisher's metadata,
             not a model assertion (C12 holds: arXiv sources are official).
      cite   a NEW evidence row quoting the `authors:` line verbatim, one per
             document, shared by that document's author entities — the quote
             an auditor re-verifies is the line that actually names the person
             (C11), not the insight's unrelated claim quote.

    Idempotent: existing (event, person) entities are skipped, the evidence
    row is reused on re-run."""
    docs = conn.execute(
        "SELECT DISTINCT d.id, d.raw_content FROM insights i"
        " JOIN evidence e ON e.id = i.evidence_id"
        " JOIN raw_documents d ON d.id = e.document_id"
        " WHERE d.source_type = 'arxiv'").fetchall()
    people = {name_key(r["canonical_name"]): r["id"] for r in
              conn.execute("SELECT id, canonical_name FROM people")}
    stats = {"docs": 0, "entities": 0, "events_gained": 0}
    for doc in docs:
        line = next((ln for ln in doc["raw_content"].split("\n")
                     if ln.startswith("authors: ")), None)
        if line is None:
            continue
        matched = [(a.strip(), people[name_key(a.strip())])
                   for a in line[len("authors: "):].split(";")
                   if a.strip() and name_key(a.strip()) in people]
        if not matched:
            continue
        stats["docs"] += 1
        ev = conn.execute(
            "SELECT id FROM evidence WHERE document_id = ?"
            " AND verbatim_content = ?", (doc["id"], line)).fetchone()
        evidence_id = ev["id"] if ev else storage.insert_evidence(
            conn, doc["id"], json.dumps({"line": "authors"}), line, "exact", 1.0)
        for event in conn.execute(
                "SELECT i.id FROM insights i JOIN evidence e ON e.id = i.evidence_id"
                " WHERE e.document_id = ?", (doc["id"],)).fetchall():
            had = conn.execute(
                "SELECT 1 FROM event_entities WHERE event_id = ?"
                " AND entity_kind = 'person'", (event["id"],)).fetchone()
            for _name, pid in matched:
                if conn.execute(
                        "SELECT 1 FROM event_entities WHERE event_id = ?"
                        " AND entity_kind = 'person' AND person_id = ?",
                        (event["id"], pid)).fetchone():
                    continue
                storage.insert_event_entity(conn, event["id"], "person", pid,
                                            "author", evidence_id,
                                            "source_inferred", commit=False)
                stats["entities"] += 1
            if not had:
                stats["events_gained"] += 1
    conn.commit()
    return stats


def main() -> None:
    """Stage 2 on its own, without re-running ingestion."""
    import argparse
    from pathlib import Path
    from fli.ops.llm import LLM, have_api_key
    ap = argparse.ArgumentParser(description="Stage-2 extraction. SPENDS MONEY.")
    ap.add_argument("--db", default=str(storage.DEFAULT_DB))
    ap.add_argument("--max", type=int, default=60,
                    help="cost cap: documents to extract this run")
    ap.add_argument("--backfill-authors", action="store_true",
                    help="deterministic person attribution from arXiv author"
                         " lines; free, no API key, idempotent")
    args = ap.parse_args()
    conn = storage.connect(Path(args.db))
    storage.init_db(conn)
    if args.backfill_authors:
        s = backfill_arxiv_authors(conn)
        print(f"arxiv author backfill — {s['entities']} author entit(ies) on"
              f" insights from {s['docs']} document(s);"
              f" {s['events_gained']} event(s) newly person-linked")
        return
    if not have_api_key():
        raise SystemExit("ANTHROPIC_API_KEY not set (put it in .env).")
    print(extract_all(conn, LLM(conn), max_docs=args.max))
    report_measurements(conn)


if __name__ == "__main__":
    main()
