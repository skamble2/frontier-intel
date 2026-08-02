"""Claim faithfulness: is each extracted claim licensed by its verified quote?
Claim faithfulness: is each extracted claim licensed by its verified quote?"""
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
    m = re.search(r'"verdict"\s*:\s*"(entailed|partial|not_entailed)"', text)
    if m:
        rm = re.search(r'"reason"\s*:\s*"([^"]*)', text)
        return m.group(1), (rm.group(1) if rm else "")[:300]
    return None


def check_all(conn, llm: LLM, limit: int | None = None,
              model: str | None = None, batch: bool = False) -> dict:
    model = model or MODEL_FOR_TASK["verify"]
    queue = _queue(conn, model, limit)
    done = conn.execute("SELECT count(*) FROM claim_checks WHERE model=?",
                        (model,)).fetchone()[0]
    print(f"faithfulness — {done} checked, {len(queue)} to go ({model})")
    counts = {"entailed": 0, "partial": 0, "not_entailed": 0, "unparsed": 0}
    batch_results: dict[str, str | None] = {}
    if batch and queue:
        from fli.ops.llm import provider_for
        if provider_for(model) != "anthropic":
            print(f"  --batch is anthropic-only; {model} runs synchronously")
        else:
            batch_results = llm.call_batch(
                "verify", SYSTEM,
                [(str(r["id"]), f"CLAIM: {r['claim']}\n\nQUOTE: \"{r['q'][:1200]}\"")
                 for r in queue],
                max_tokens=350, model=model)
    for n, r in enumerate(queue, 1):
        text = batch_results.get(str(r["id"]))
        if text is None:
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


REPAIR_SYSTEM = """You tighten a claim so it is fully supported by a quote.

You will see one CLAIM, the verbatim QUOTE that is its only evidence, and the
GAP an entailment check named (a fact in the claim that the quote does not
state). Rewrite the claim so that EVERY load-bearing fact in it — numbers,
dates, actors, causal statements — is stated in or directly follows from the
quote. Keep everything the quote does support; the goal is the strongest claim
the quote can carry, not the weakest.

Rules:
 - Never add information. The quote is all you have; the rest of the document
   does not exist for this task.
 - Keep the claim one sentence, declarative, specific.
 - If the quote cannot support any substantive claim at all, say so.

Reply with ONLY this JSON:
{"claim": "<rewritten claim>" | null, "reason": "<one line>"}"""


def _repair_queue(conn, verify_model: str) -> list:
    """Insights whose CURRENT verdict from `verify_model` is `partial`. """
    return conn.execute(
        "SELECT i.id, i.claim, ev.verbatim_content q, c.reason"
        " FROM insights i"
        " JOIN evidence ev ON ev.id = i.evidence_id"
        " JOIN claim_checks c ON c.insight_id = i.id AND c.model = ?"
        " WHERE c.verdict = 'partial' ORDER BY i.id", (verify_model,)).fetchall()


def _parse_repair(text: str) -> tuple[str | None, str] | None:
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        d = json.loads(text[start:end])
        if "claim" in d:
            c = d["claim"]
            if c is None or (isinstance(c, str) and c.strip()):
                return (c.strip() if c else None), str(d.get("reason", ""))[:300]
    except (ValueError, json.JSONDecodeError):
        pass
    return None


def repair_all(conn, llm: LLM, limit: int | None = None,
               verify_model: str | None = None) -> dict:
    """Rewrite `partial` claims against their own quote, then re-verify."""
    verify_model = verify_model or MODEL_FOR_TASK["verify"]
    queue = _repair_queue(conn, verify_model)
    if limit:
        queue = queue[:limit]
    print(f"claim repair — {len(queue)} partial claim(s) "
          f"(rewrite: {MODEL_FOR_TASK['repair']}, re-verify: {verify_model})")
    counts = {"entailed": 0, "partial": 0, "not_entailed": 0,
              "unrepairable": 0, "unparsed": 0}
    for n, r in enumerate(queue, 1):
        user = (f"CLAIM: {r['claim']}\n\nQUOTE: \"{r['q'][:1200]}\"\n\n"
                f"GAP: {r['reason'] or '(not named)'}")
        parsed = _parse_repair(llm.call("repair", REPAIR_SYSTEM, user,
                                        max_tokens=400))
        if parsed is None:
            counts["unparsed"] += 1
            print(f"  [{n}/{len(queue)}] event {r['id']}: UNPARSED, skipped")
            continue
        new_claim, why = parsed
        if new_claim is None:
            counts["unrepairable"] += 1
            print(f"  [{n}/{len(queue)}] event {r['id']}: unrepairable — {why}")
            continue
        storage.log_rejection(conn, None, "verification", "claim_rewritten",
                              f"event {r['id']}: {r['claim'][:300]}")
        conn.execute("UPDATE insights SET claim=? WHERE id=?",
                     (new_claim, r["id"]))
        conn.execute("DELETE FROM claim_checks WHERE insight_id=? AND model=?",
                     (r["id"], verify_model))
        v = _parse(llm.call("verify", SYSTEM,
                            f"CLAIM: {new_claim}\n\nQUOTE: \"{r['q'][:1200]}\"",
                            max_tokens=350, model=verify_model))
        verdict, reason = v if v else ("partial", "re-verify unparsed; kept partial")
        conn.execute(
            "INSERT OR IGNORE INTO claim_checks (insight_id, model, verdict,"
            " reason, created_at) VALUES (?,?,?,?,?)",
            (r["id"], verify_model, verdict, reason, storage.now_utc()))
        conn.commit()
        counts[verdict] += 1
        mark = "->" if verdict == "entailed" else "still"
        print(f"  [{n}/{len(queue)}] event {r['id']}: {mark} {verdict}")
    print(f"\nrepair: {counts['entailed']} now entailed, {counts['partial']} "
          f"still partial, {counts['not_entailed']} not_entailed, "
          f"{counts['unrepairable']} unrepairable, {counts['unparsed']} unparsed."
          f"\nClaims changed -> re-run: python -m fli.cli cluster && "
          f"python -m fli.cli features && python -m fli.cli score --bakeoff"
          f" --all-rubrics")
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Judge claim<->quote entailment for every insight (f15).")
    ap.add_argument("--db", default=str(storage.DEFAULT_DB))
    ap.add_argument("--n", type=int, default=None, help="cap this run's calls")
    ap.add_argument("--model", default=None,
                    help=f"judge model (default: {MODEL_FOR_TASK['verify']})")
    ap.add_argument("--repair", action="store_true",
                    help="rewrite `partial` claims against their own quote,"
                         " then re-verify (SPENDS, ~2 calls per claim)")
    ap.add_argument("--dry-run", action="store_true",
                    help="show the prompt and projected cost; spend nothing")
    ap.add_argument("--batch", action="store_true",
                    help="verify through the Batch API at 50%% of the "
                         "synchronous price (anthropic models only; blocks "
                         "until the batch ends). Failed items retry "
                         "synchronously")
    args = ap.parse_args()
    conn = storage.connect(Path(args.db))
    storage.init_db(conn)
    model = args.model or MODEL_FOR_TASK["verify"]
    if args.repair:
        queue = _repair_queue(conn, model)
        if args.n:
            queue = queue[:args.n]
        if args.dry_run:
            print(REPAIR_SYSTEM)
            if queue:
                r = queue[0]
                print(f"\n--- first user message ---\nCLAIM: {r['claim']}\n\n"
                      f"QUOTE: \"{r['q'][:1200]}\"\n\nGAP: {r['reason']}")
            preflight(MODEL_FOR_TASK["repair"], n_calls=len(queue))
            preflight(model, n_calls=len(queue))
            print(f"DRY RUN — {len(queue)} claim(s) would be repaired. $0 spent.")
            return 0
        if not have_api_key(MODEL_FOR_TASK["repair"]) or not have_api_key(model):
            raise SystemExit("no API key for the repair/verify models; set it in .env")
        repair_all(conn, LLM(conn), limit=args.n, verify_model=args.model)
        return 0
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
    check_all(conn, LLM(conn), limit=args.n, model=args.model, batch=args.batch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
