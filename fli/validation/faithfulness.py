"""RAGAS-style faithfulness for the delivery layer (internal validation)."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from fli import storage
from fli.ops.llm import LLM, MODEL_FOR_TASK, have_api_key, preflight

SYSTEM = """You audit an analyst note against the only evidence its author was
allowed to use.

You will see the EVIDENCE (an extracted claim, the verbatim quote it was
verified against, and any pre-established position mechanisms) and the NOTE
(a hypothesis and its reasoning, written for a reader).

Decompose the note into its atomic FACTUAL assertions: numbers, dates, actors,
named products or methods, events, capabilities, causal statements about what
happened. For each one, judge:
  supported     stated in, or directly following from, the evidence
  unsupported   imported from outside the evidence — background knowledge,
                a guessed detail, an invented number

Do NOT list the note's judgement calls — its direction (threat/adopt/etc.),
its confidence, its advice, its assessment of importance. Those are the note's
job. You are checking its facts, not its opinion.

Reply with ONLY this JSON:
{"statements": [{"text": "<the assertion>", "supported": true|false}, ...]}"""


def _queue(conn, model: str, limit: int | None = None) -> list:
    """Hypotheses not yet scored by `model`. Deterministic order = resumable."""
    rows = conn.execute(
        "SELECT h.id, h.persona, h.hypothesis, h.reasoning, h.insight_id,"
        "       i.claim, ev.verbatim_content q"
        " FROM hypotheses h"
        " JOIN insights i ON i.id = h.insight_id"
        " JOIN evidence ev ON ev.id = i.evidence_id"
        " WHERE NOT EXISTS (SELECT 1 FROM hypothesis_checks c"
        "   WHERE c.hypothesis_id = h.id AND c.model = ?)"
        " ORDER BY h.id", (model,)).fetchall()
    return rows[:limit] if limit else rows


def _user(conn, r) -> str:
    """EVIDENCE must be exactly what the note's author was shown, or the audit
    flags artifacts."""
    from fli.delivery.personas import build_prompt
    parts = ["EVIDENCE (everything the note's author was shown)",
             build_prompt(conn, r["insight_id"], r["persona"])]
    if r["persona"] == "investment":
        from fli.core.policy import load_policy
        parts.append("the fund's holdings (also shown to the author): "
                     + ", ".join(h.ticker for h in load_policy().positions))
    parts.append(f"\nNOTE ({r['persona']})\nhypothesis: {r['hypothesis']}")
    parts.append(f"reasoning: {r['reasoning']}")
    return "\n".join(parts)


def _parse(text: str) -> list[tuple[str, bool]] | None:
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        d = json.loads(text[start:end])
        out = []
        for s in d.get("statements", []):
            if isinstance(s, dict) and s.get("text") and \
                    isinstance(s.get("supported"), bool):
                out.append((str(s["text"])[:300], s["supported"]))
        return out
    except (ValueError, json.JSONDecodeError):
        return None


def score_hypotheses(conn, llm: LLM, limit: int | None = None,
                     model: str | None = None, batch: bool = False) -> dict:
    model = model or MODEL_FOR_TASK["faithfulness"]
    queue = _queue(conn, model, limit)
    done = conn.execute("SELECT count(1) FROM hypothesis_checks WHERE model=?",
                        (model,)).fetchone()[0]
    print(f"hypothesis faithfulness — {done} scored, {len(queue)} to go ({model})")
    batch_results: dict[str, str | None] = {}
    if batch and queue:
        from fli.ops.llm import provider_for
        if provider_for(model) != "anthropic":
            print(f"  --batch is anthropic-only; {model} runs synchronously")
        else:
            batch_results = llm.call_batch(
                "faithfulness", SYSTEM,
                [(str(r["id"]), _user(conn, r)) for r in queue],
                max_tokens=1000, model=model)
    counts = {"scored": 0, "unparsed": 0, "flagged": 0}
    for n, r in enumerate(queue, 1):
        text = batch_results.get(str(r["id"]))
        if text is None:
            text = llm.call("faithfulness", SYSTEM, _user(conn, r),
                            max_tokens=1000, model=model)
        stmts = _parse(text)
        if stmts is None:
            counts["unparsed"] += 1
            print(f"  [{n}/{len(queue)}] hypothesis {r['id']}: UNPARSED, skipped")
            continue
        supported = sum(1 for _, ok in stmts if ok)
        unsupported = [t for t, ok in stmts if not ok]
        score = supported / len(stmts) if stmts else 1.0
        conn.execute(
            "INSERT OR IGNORE INTO hypothesis_checks (hypothesis_id, model,"
            " supported, total, score, detail, created_at) VALUES (?,?,?,?,?,?,?)",
            (r["id"], model, supported, len(stmts), score,
             "; ".join(unsupported)[:600] or None, storage.now_utc()))
        conn.commit()
        counts["scored"] += 1
        if unsupported:
            counts["flagged"] += 1
            print(f"  [{n}/{len(queue)}] hypothesis {r['id']} "
                  f"({r['persona']}, event {r['insight_id']}) "
                  f"score {score:.2f} — unsupported: {'; '.join(unsupported)[:160]}")
        elif n % 25 == 0:
            print(f"  [{n}/{len(queue)}] ...")
    print()
    for row in conn.execute(
            "SELECT h.persona, count(1) n, avg(c.score) mean,"
            "       sum(CASE WHEN c.detail IS NOT NULL THEN 1 ELSE 0 END) flagged"
            " FROM hypothesis_checks c JOIN hypotheses h ON h.id = c.hypothesis_id"
            " WHERE c.model = ? GROUP BY h.persona", (model,)):
        print(f"  {row['persona']:<12} mean faithfulness {row['mean']:.3f} "
              f"over {row['n']} notes, {row['flagged']} with >=1 "
              f"unsupported statement")
    return counts


def _normalise(q: str) -> str:
    return " ".join((q or "").split())[:600]


def check_digests(conn, out_dir: Path | None = None) -> dict:
    """Every quote and numbered claim in docs/digests/*.md must exist in the DB.
    Every quote and numbered claim in docs/digests/*.md must exist in the DB."""
    from fli.delivery.digest import OUT_DIR
    out_dir = out_dir or OUT_DIR
    quotes = {_normalise(r[0]) for r in conn.execute(
        "SELECT verbatim_content FROM evidence")}
    claims = {r[0] for r in conn.execute("SELECT claim FROM insights")}
    files = sorted(out_dir.glob("*.md"))
    print(f"digest parity — {len(files)} file(s) in {out_dir}")
    totals = {"files": len(files), "quotes": 0, "claims": 0,
              "quote_misses": 0, "claim_misses": 0}
    for f in files:
        text = f.read_text(encoding="utf-8")
        q_miss = c_miss = nq = nc = 0
        for m in re.finditer(r"^> \u201c(.+?)\u201d\s*$", text, re.M | re.S):
            nq += 1
            if m.group(1) not in quotes:
                q_miss += 1
                print(f"  {f.name}: quote not in DB — \"{m.group(1)[:80]}...\"")
        for m in re.finditer(r"^## \d+\. (.+)$", text, re.M):
            nc += 1
            if m.group(1).strip() not in claims:
                c_miss += 1
                print(f"  {f.name}: claim not in DB — {m.group(1)[:80]}")
        totals["quotes"] += nq
        totals["claims"] += nc
        totals["quote_misses"] += q_miss
        totals["claim_misses"] += c_miss
        mark = "ok" if not (q_miss or c_miss) else "MISMATCH"
        print(f"  {f.name}: {nc} claims, {nq} quotes — {mark}")
    misses = totals["quote_misses"] + totals["claim_misses"]
    print(f"\n{totals['claims']} claims and {totals['quotes']} quotes checked, "
          f"{misses} not found in the database")
    return totals


def main() -> int:
    ap = argparse.ArgumentParser(
        description="RAGAS-style faithfulness for persona notes and digests.")
    ap.add_argument("--db", default=str(storage.DEFAULT_DB))
    ap.add_argument("--n", type=int, default=None, help="cap this run's calls")
    ap.add_argument("--model", default=None,
                    help=f"scoring model (default {MODEL_FOR_TASK['faithfulness']})")
    ap.add_argument("--digests", action="store_true",
                    help="check digest files against the DB instead "
                         "(deterministic, $0)")
    ap.add_argument("--dry-run", action="store_true",
                    help="show the prompt and projected cost; spend nothing")
    ap.add_argument("--batch", action="store_true",
                    help="score through the Batch API at 50%% of the "
                         "synchronous price (anthropic models only)")
    args = ap.parse_args()
    conn = storage.connect(Path(args.db))
    storage.init_db(conn)
    if args.digests:
        totals = check_digests(conn)
        return 1 if totals["quote_misses"] + totals["claim_misses"] else 0
    model = args.model or MODEL_FOR_TASK["faithfulness"]
    queue = _queue(conn, model, args.n)
    if args.dry_run:
        print(SYSTEM)
        if queue:
            print(f"\n--- first user message ---\n{_user(conn, queue[0])}")
        preflight(model, n_calls=len(queue))
        print(f"DRY RUN — {len(queue)} note(s) would be scored. $0 spent.")
        return 0
    if not have_api_key(model):
        raise SystemExit("no API key for the faithfulness model; set it in .env")
    preflight(model, n_calls=len(queue))
    score_hypotheses(conn, LLM(conn), limit=args.n, model=args.model,
                     batch=args.batch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
