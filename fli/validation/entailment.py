"""Claim faithfulness: is each extracted claim licensed by its verified quote?

Every insight's quote is already byte-verified against the stored source (C2),
so hallucinated QUOTES cannot exist. What C2 cannot see is the gap between the
quote and the CLAIM written above it — an extractor can quote faithfully and
still overclaim. This module closes that gap: for every insight, a cheap judge
is shown the claim and the quote ALONE (no document, no lab name) and asked
whether the quote supports the claim.

Three verdicts, because "partially" is the common failure and collapsing it
into either pole would hide it:

    entailed      every load-bearing fact in the claim is in the quote
    partial       the core is supported but some fact (a number, a date, an
                  actor) is not in the quote
    not_entailed  the claim asserts something the quote does not say

Resumable and idempotent: one verdict per (insight, model), already-checked
insights are skipped, so a crashed run continues where it stopped.

The result is JUDGED-tier by this repo's own rules: an LLM's reading of an
entailment rubric, not ground truth. It is reported as such in figure f15.

Run:  python -m fli.cli verify              # SPENDS (~$0.5 on 734 events)
      python -m fli.cli verify --dry-run    # prompt preview + projection, $0
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from fli import storage
from fli.ops.llm import LLM, MODEL_FOR_TASK, have_api_key, preflight

SYSTEM = """You check whether a claim is supported by a quote.

You will see one CLAIM and one QUOTE. The quote is verbatim from a primary
source; the claim was written by an extraction system that read the full
document. Judge ONLY whether the quote supports the claim — you have no access
to the rest of the document, and that is the point of the check.

Verdicts:
  "entailed"      every load-bearing fact in the claim (numbers, dates, actors,
                  causal statements) is stated in or directly follows from the quote.
  "partial"       the core of the claim is supported, but at least one
                  load-bearing fact is NOT in the quote. Name it.
  "not_entailed"  the claim asserts something the quote does not say, or
                  contradicts it.

Do not reward plausibility: a claim that is probably true but not supported by
this quote is "partial" or "not_entailed". Reply with ONLY this JSON:
{"verdict": "entailed" | "partial" | "not_entailed", "reason": "<one line>"}"""


def _queue(conn, model: str, limit: int | None = None) -> list:
    """Insights not yet checked by `model`. Deterministic order = resumable."""
    sql = ("SELECT i.id, i.claim, ev.verbatim_content q FROM insights i"
           " JOIN evidence ev ON ev.id = i.evidence_id"
           " WHERE NOT EXISTS (SELECT 1 FROM claim_checks c"
           "   WHERE c.insight_id = i.id AND c.model = ?)"
           " ORDER BY i.id")
    rows = conn.execute(sql, (model,)).fetchall()
    return rows[:limit] if limit else rows


def _parse(text: str) -> tuple[str, str] | None:
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        d = json.loads(text[start:end])
        v = d.get("verdict", "").strip()
        if v in ("entailed", "partial", "not_entailed"):
            return v, str(d.get("reason", ""))[:300]
    except (ValueError, json.JSONDecodeError):
        pass
    # Salvage a reply truncated by max_tokens: the verdict is written first,
    # so it survives even when the closing brace of the reason does not.
    m = re.search(r'"verdict"\s*:\s*"(entailed|partial|not_entailed)"', text)
    if m:
        rm = re.search(r'"reason"\s*:\s*"([^"]*)', text)
        return m.group(1), (rm.group(1) if rm else "")[:300]
    return None


def check_all(conn, llm: LLM, limit: int | None = None,
              model: str | None = None) -> dict:
    model = model or MODEL_FOR_TASK["verify"]
    queue = _queue(conn, model, limit)
    done = conn.execute("SELECT count(*) FROM claim_checks WHERE model=?",
                        (model,)).fetchone()[0]
    print(f"faithfulness — {done} checked, {len(queue)} to go ({model})")
    counts = {"entailed": 0, "partial": 0, "not_entailed": 0, "unparsed": 0}
    for n, r in enumerate(queue, 1):
        user = f"CLAIM: {r['claim']}\n\nQUOTE: \"{r['q'][:1200]}\""
        text = llm.call("verify", SYSTEM, user, max_tokens=350, model=model)
        parsed = _parse(text)
        if parsed is None:
            counts["unparsed"] += 1
            print(f"  [{n}/{len(queue)}] event {r['id']}: UNPARSED, skipped")
            continue
        verdict, reason = parsed
        conn.execute(
            "INSERT OR IGNORE INTO claim_checks (insight_id, model, verdict,"
            " reason, created_at) VALUES (?,?,?,?,?)",
            (r["id"], model, verdict, reason, storage.now_utc()))
        conn.commit()
        counts[verdict] += 1
        if verdict != "entailed":
            print(f"  [{n}/{len(queue)}] event {r['id']}: {verdict} — {reason}")
        elif n % 50 == 0:
            print(f"  [{n}/{len(queue)}] ...")
    total = sum(counts[k] for k in ("entailed", "partial", "not_entailed"))
    if total:
        print(f"\nverdicts: {counts['entailed']} entailed"
              f" ({counts['entailed'] / total:.1%}), {counts['partial']} partial,"
              f" {counts['not_entailed']} not_entailed"
              f" ({counts['unparsed']} unparsed)")
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Judge claim<->quote entailment for every insight (f15).")
    ap.add_argument("--db", default=str(storage.DEFAULT_DB))
    ap.add_argument("--n", type=int, default=None, help="cap this run's calls")
    ap.add_argument("--model", default=None,
                    help=f"judge model (default: {MODEL_FOR_TASK['verify']})")
    ap.add_argument("--dry-run", action="store_true",
                    help="show the prompt and projected cost; spend nothing")
    args = ap.parse_args()
    conn = storage.connect(Path(args.db))
    storage.init_db(conn)
    model = args.model or MODEL_FOR_TASK["verify"]
    queue = _queue(conn, model, args.n)
    if args.dry_run:
        print(SYSTEM)
        if queue:
            r = queue[0]
            print(f"\n--- first user message ---\nCLAIM: {r['claim']}\n\n"
                  f"QUOTE: \"{r['q'][:1200]}\"")
        preflight(model, n_calls=len(queue))
        print(f"DRY RUN — {len(queue)} call(s) would be made. $0 spent.")
        return 0
    if not have_api_key(model):
        raise SystemExit("no API key for the verify model; set it in .env")
    preflight(model, n_calls=len(queue))
    check_all(conn, LLM(conn), limit=args.n, model=args.model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
