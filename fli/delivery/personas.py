"""What an event MEANS, written for one audience at a time.

The test for this layer: "'Actionable' means a reader knows what it
means and what to do, not just a summary of what happened." A ranked claim with
a citation is still a summary. This is where the system commits to a reading.

WHY AN LLM DOES THIS AND THE DETERMINISTIC LAYER DOES NOT.

`positions.py` establishes which holding an event touches and through what
mechanism, and it deliberately refuses to state a direction for a demand
channel. The reason is measured: these three events share two channels and
point opposite ways —

    "Mistral is building a 10 MW data center"        demand UP
    "Meta's scheduler saved 3.28 megawatts"          demand DOWN
    "Gemma 4 12B matches a 26B model while smaller"  demand DOWN

Getting that right means reading whether the quantity rose or fell, which is a
judgement about a sentence, not a property of a channel. So the sign is decided
here, by something that reads the claim, and `reasoning` is NOT NULL in the
schema so the reader can always check the working.

TWO AUDIENCES, TWO QUESTIONS, and the fields mean different things:

    investment  "what does this mean for our positions?"
                direction = threat | tailwind | unclear
    ai_team     "what should we adopt or investigate?"
                direction = adopt | investigate | monitor

THE PROMPT IS CONSTRAINED TO THE EVIDENCE. The model sees the claim, the
verified verbatim quote and the date — the same material a reader can check —
and is told that a hypothesis resting on anything else is a failure. It is not
given the lab name, for the same reason the judge is not: a reading driven by
who published it is not a reading of the event.

Cost: one call per (event, persona). At the default k=10 that is ~20 calls,
roughly $0.20. Idempotent — an event already rendered for a persona is skipped.

Run:  python3 -m fli.cli personas --dry-run     # prompt + cost, spends nothing
      python3 -m fli.cli personas --k 10
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from fli import storage
from fli.core.policy import load_policy

PERSONAS = ("investment", "ai_team")

# A hypothesis plus its reasoning is several times longer than a judge verdict,
# and sonnet-5 spends part of the budget on adaptive thinking before writing a
# character. At 500 the two most nuanced events — both "efficiency means less
# demand" readings, which need the longest chain — were cut off mid-JSON.
MAX_TOKENS = 1200

_SHARED = """You will be given ONE event extracted from a frontier AI lab's own
publication: a claim, the verbatim quote it was verified against, and the date.

HARD RULES
 - Use only what is in the claim and the quote. If the quote does not support a
   consequence, say so rather than inferring one. A confident reading of
   something the evidence does not say is the worst outcome here.
 - You are not told which lab published this. Do not guess, and do not let a
   guess shape the reading.
 - `reasoning` is shown to the reader verbatim. Write the actual chain, not a
   restatement of the claim.
"""

_INVESTMENT = """You are writing for a portfolio manager at a technology fund.

THE QUESTION: what does this mean for the fund's positions?

The fund holds the tickers listed below, and analysis has already established
WHICH holdings this event touches and through WHAT mechanism. It deliberately
did NOT decide the direction, because a mechanism has no sign — building a
10 MW datacenter and saving 3.28 megawatts are the same channel pointing
opposite ways.

YOUR JOB IS THE SIGN. Read whether the quantity in this event moved UP or DOWN
for the holding, and say which:
  "threat"   the event erodes the holding's market, margin or moat
  "tailwind" the event increases demand for what the holding sells
  "unclear"  the quote does not establish a direction — a common, correct answer

`time_horizon`: "now" (already shipped/contracted), "quarters", or "years".
`confidence`: high only where the quote itself carries the number or commitment.

Reply with ONLY this JSON:
{"hypothesis": "<one sentence a PM could act on>",
 "tickers": ["TICK", ...],
 "direction": "threat" | "tailwind" | "unclear",
 "confidence": "high" | "medium" | "low",
 "time_horizon": "now" | "quarters" | "years",
 "reasoning": "<why, citing what in the quote drove it>"}"""

_AI_TEAM = """You are writing for the AI engineering team that would build on
this.

THE QUESTION: what should we adopt or investigate?

Commercial consequence is irrelevant here. Pricing, market share and share
prices do not matter unless they change what the team could afford to run.

  "adopt"       usable now: weights, code, an API or a fully described method,
                and a clear reason to prefer it over what we run today
  "investigate" promising but unproven for us — worth a spike, not a migration
  "monitor"     interesting direction, nothing to do this quarter

`time_horizon`: "now", "quarters", or "years" — when this would change our stack.
`confidence`: high only where the quote states what was released or how it works.

Reply with ONLY this JSON:
{"hypothesis": "<one sentence: what an engineer should do about this>",
 "direction": "adopt" | "investigate" | "monitor",
 "confidence": "high" | "medium" | "low",
 "time_horizon": "now" | "quarters" | "years",
 "reasoning": "<why, citing what in the quote drove it>"}"""

_DIRECTIONS = {"investment": {"threat", "tailwind", "unclear"},
               "ai_team": {"adopt", "investigate", "monitor"}}


def build_system(persona: str, policy) -> str:
    if persona == "investment":
        holdings = "\n".join(
            f" - {h.ticker}  {h.name}: {h.thesis}" for h in policy.positions)
        return f"{_SHARED}\nTHE FUND'S HOLDINGS:\n{holdings}\n\n{_INVESTMENT}"
    return f"{_SHARED}\n{_AI_TEAM}"


def build_prompt(conn, event_id: int, persona: str) -> str:
    r = conn.execute(
        "SELECT i.claim, i.event_type, ev.verbatim_content q, d.published_at"
        " FROM insights i JOIN evidence ev ON ev.id = i.evidence_id"
        " JOIN raw_documents d ON d.id = ev.document_id WHERE i.id = ?",
        (event_id,)).fetchone()
    parts = [f"type: {r['event_type']}",
             f"date: {(r['published_at'] or 'unknown')[:10]}",
             f"claim: {r['claim']}",
             f'quote: "{r["q"][:600]}"']
    if persona == "investment":
        edges = conn.execute(
            "SELECT ticker, channel, direction FROM event_positions"
            " WHERE event_id = ?", (event_id,)).fetchall()
        if edges:
            parts.append("\nholdings this touches (mechanism already established;"
                         " the direction is yours to decide):")
            for e in edges:
                parts.append(f"  {e['ticker']} via "
                             f"{e['channel'] or 'no established channel'}")
        else:
            parts.append("\nno holding exposure was found for this event.")
    return "\n".join(parts)


def parse(raw: str, persona: str) -> tuple[dict | None, str]:
    """(verdict, why_it_failed). The reason is returned, not swallowed.

    An earlier version printed a bare "UNUSABLE", which said nothing about
    whether the model had answered the wrong question, omitted its working, or
    simply been cut off. Two events failed that way and the cause — a reply
    truncated at max_tokens mid-JSON — was invisible until the payload was
    inspected by hand. A failure that cannot be diagnosed from the log is a
    failure that gets ignored.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text[4:] if text.startswith("json") else text
    if not text:
        return None, "empty reply"
    try:
        v = json.loads(text)
    except json.JSONDecodeError as e:
        # An unterminated string or object almost always means the reply hit
        # the token ceiling rather than that the model wrote bad JSON.
        hint = ("reply appears TRUNCATED (raise max_tokens)"
                if text.rstrip()[-1:] not in "}]" else f"bad JSON: {e}")
        return None, f"{hint}; {len(text)} chars received"
    if not v.get("hypothesis"):
        return None, "no hypothesis"
    if not v.get("reasoning"):
        return None, "no reasoning (the reader is shown it verbatim)"
    if v.get("direction") not in _DIRECTIONS[persona]:
        return None, (f"direction {v.get('direction')!r} is not in the "
                      f"{persona} vocabulary {sorted(_DIRECTIONS[persona])} — "
                      f"the model answered the other audience's question")
    return v, ""


def _parse(raw: str, persona: str) -> dict | None:
    """Verdict only. Kept because the tests exercise the decision, not the
    diagnostics."""
    return parse(raw, persona)[0]


RUBRIC_FOR_PERSONA = {"investment": "investment", "ai_team": "technical"}


def _candidates(conn, persona: str, k: int) -> list[int]:
    """Which events this persona renders — THE SAME EVENTS THE DIGEST PUBLISHES.

    An earlier version read the raw ranking straight out of `event_scores`,
    which is the scorer's ordering before any editorial rule is applied. The
    digest applies those rules — the 90-day window, one item per story, a cap
    per lab — so the two lists came apart completely: of the ten events the
    engineering persona had been rendered for, ZERO appeared in the technical
    digest at any window, and only 3 of 10 did on the investment side. Every
    one of those calls described an event no reader would ever be shown, while
    every item in the digest arrived with no reading attached.

    So the slate is the single source of truth for what gets published, and it
    is computed here by the same call the digest makes.

    The investment reader additionally gets anything with an established
    position edge, because an event touching a holding is relevant whatever its
    global rank — but still only inside the window, since a digest is a report
    on a period.
    """
    from fli.intelligence.scoring import top_events   # layer 3 -> layer 2
    rubric = RUBRIC_FOR_PERSONA[persona]
    top = [r["id"] for r in top_events(conn, k=k, rubric=rubric)[0]]
    if persona != "investment":
        return top
    # EVERY event with position exposure, not only the ones already signed.
    #
    # This filtered on `direction != 'unclear'` and so fed the model only the
    # events the deterministic layer had already decided — 1 of 54. The three
    # cases that justified building this layer (a 10 MW datacenter, Project
    # Camellia, Gemma 4 needing less memory) are all `unclear` by design,
    # because a demand channel carries no sign. Those are precisely the events
    # that need a reader, and they were the ones excluded.
    #
    # An established channel comes first: exposure with a mechanism is a
    # candidate worth a judgement, exposure without one usually is not.
    #
    # The window applies here too. Nine of the ten exposed events are inside
    # 90 days; the tenth is a 2024 post, and putting a two-year-old event at the
    # head of this week's report would be a bug the reader would catch first.
    touching = [r["event_id"] for r in conn.execute(
        "SELECT DISTINCT ep.event_id FROM event_positions ep"
        " JOIN insights i ON i.id = ep.event_id"
        " JOIN evidence ev ON ev.id = i.evidence_id"
        " JOIN raw_documents d ON d.id = ev.document_id"
        " WHERE ep.channel IS NOT NULL"
        "   AND d.published_at IS NOT NULL"
        "   AND julianday('now') - julianday(d.published_at) <= ?"
        " ORDER BY i.score DESC", (load_policy().window_days,))]
    seen, out = set(), []
    for e in touching + top:                 # position hits lead the list
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out


def build(conn: sqlite3.Connection, k: int = 10, dry_run: bool = False,
          personas: tuple[str, ...] = PERSONAS) -> dict:
    from fli.ops.llm import LLM, MODEL_FOR_TASK, preflight
    policy = load_policy()
    model = MODEL_FOR_TASK["persona"]

    todo: list[tuple[int, str]] = []
    for p in personas:
        done = {r["insight_id"] for r in conn.execute(
            "SELECT insight_id FROM hypotheses WHERE persona = ?", (p,))}
        for e in _candidates(conn, p, k):
            if e not in done:
                todo.append((e, p))

    print(f"personas — {len(todo)} (event, persona) pair(s) to render")
    for p in personas:
        print(f"    {p:<12}{sum(1 for _, x in todo if x == p):>4}")
    preflight(model, len(todo))
    if dry_run:
        if todo:
            e, p = todo[0]
            print(f"\n--- SYSTEM ({p}) ---\n{build_system(p, policy)}")
            print(f"\n--- USER ---\n{build_prompt(conn, e, p)}")
        print("\nDRY RUN — nothing sent, nothing spent.")
        return {"dry_run": True, "todo": len(todo)}
    if not todo:
        print("  nothing to do — every candidate already has a hypothesis.")
        return {"made": 0}

    llm = LLM(conn)
    made = unusable = 0
    systems = {p: build_system(p, policy) for p in personas}
    for i, (event_id, persona) in enumerate(todo, 1):
        user = build_prompt(conn, event_id, persona)
        v, why = parse(llm.call("persona", systems[persona], user,
                                max_tokens=MAX_TOKENS), persona)
        if v is None:
            v, why = parse(llm.call(
                "persona", systems[persona],
                user + f"\n\nYour previous reply was unusable ({why}). Reply "
                       f"with ONLY the JSON object, keeping `reasoning` to two "
                       f"sentences.",
                max_tokens=MAX_TOKENS), persona)
        if v is None:
            unusable += 1
            print(f"  [{i:>3}/{len(todo)}] event {event_id} {persona}: "
                  f"UNUSABLE — {why}")
            continue
        conn.execute(
            "INSERT INTO hypotheses (insight_id, persona, hypothesis, tickers,"
            " direction, confidence, time_horizon, reasoning)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (event_id, persona, v["hypothesis"],
             ",".join(v.get("tickers") or []) or None,
             v["direction"], v.get("confidence"), v.get("time_horizon"),
             v["reasoning"]))
        conn.commit()
        made += 1
        print(f"  [{i:>3}/{len(todo)}] {persona:<11}{v['direction']:<12}"
              f"{v['hypothesis'][:72]}")

    print(f"\nwrote {made} hypotheses, {unusable} unusable")
    for p in personas:
        rows = conn.execute(
            "SELECT direction, COUNT(*) n FROM hypotheses WHERE persona=?"
            " GROUP BY 1 ORDER BY n DESC", (p,)).fetchall()
        print(f"  {p:<12}{ {r['direction']: r['n'] for r in rows} }")
    return {"made": made, "unusable": unusable}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(storage.DEFAULT_DB))
    ap.add_argument("--k", type=int, default=10,
                    help="top events per persona (default 10)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the prompt and projected cost, spend nothing")
    ap.add_argument("--persona", choices=PERSONAS,
                    help="render one persona only")
    args = ap.parse_args()
    conn = storage.connect(Path(args.db))
    storage.init_db(conn)
    build(conn, k=args.k, dry_run=args.dry_run,
          personas=(args.persona,) if args.persona else PERSONAS)


if __name__ == "__main__":
    main()
