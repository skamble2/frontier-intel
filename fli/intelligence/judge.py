"""The LLM pairwise judge.

Fills `pairwise_labels` with `llm:<model>` rows so the bake-off has something to
train on. Until this runs, `pairwise_labels` is empty and scoring has nothing
to learn from.

WHAT THIS IS NOT: ground truth. The judge applies `docs/labeling-rubric.md`,
which is BIT's published thesis turned into six ordering rules. Its reliability
is not assumed — it is estimated from disagreement with the labeling functions
and the human audit (`fli/intelligence/weak_supervision.py`), and reported.

THREE THINGS THE PROMPT DELIBERATELY DOES:

1. Withholds the lab name. Rubric section 4 bans lab identity as a reason, and
   per-lab precision@10 is the fairness check — a judge primed by lab prestige
   would invalidate it. The judge sees claim, type, date and the verified quote.
2. Demands a rule number. A verdict that cannot cite which of the six ordering
   rules decided it is rejected and retried once. That makes the reasoning
   auditable rather than decorative.
3. Randomises which event is shown as A (deterministically per pair) and
   un-swaps the verdict on store, so position and content are not confounded.
   The method version is part of the labeler id, so a methodology change is
   visible in the data rather than silently mixed into it.

PROMPT VERSIONS are kept side by side (JUDGE_RULES) rather than replaced, so a
stored label always traces to the exact instructions that produced it, and two
versions can be run over the identical seeded sample for a head-to-head.

Run:  python3 -m fli.cli judge --n 200                  # current version (SPENDS)
      python3 -m fli.cli judge --n 200 --version r2     # the older prompt
      python3 -m fli.cli judge --compare r3 r4          # head-to-head, $0
      python3 -m fli.cli judge --consistency 40         # flip rate (2N calls)
      python3 -m fli.cli judge --n 5 --dry-run          # prompt preview, $0
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from fli import storage
from fli.core.policy import load_policy
from fli.intelligence.labeling import record_label, sample_pairs

# Bump when ANYTHING that could change a verdict changes: the prompt, the
# rules, the presentation order, the fields shown. It is part of the labeler
# identity, so `pairwise_labels` records WHICH METHOD produced each judgement.
#
# Learned the hard way: the A/B randomisation fix shipped without bumping this,
# so 148 pre-fix and 49 post-fix labels both wrote `llm:claude-sonnet-5` and
# became impossible to separate. The diagnostic that motivated the fix could
# then not be evaluated at all.
#   r1 — original; A was always the lower insight id (position confound)
#   r2 — presentation order randomised per pair, verdict un-swapped on store
JUDGE_VERSION = "r4"

# Every prompt version is KEPT, not replaced. Two reasons: an old label can
# always be traced to the exact instructions that produced it, and two versions
# can be run over the identical seeded pair sample for a head-to-head.
#   r1 — original; A was always the lower insight id (position confound)
#   r2 — presentation order randomised per pair, verdict un-swapped on store
#   r3 — tie demoted to a genuine last resort
#   r4 — BINARY: no tie at all, but a mandatory confidence field

_PREAMBLE = """You rank frontier-AI-lab events for a technology investment fund.

THE ONLY QUESTION: which event moves a number in one of the fund's transmission
channels, more directly and sooner?

CHANNELS (pick the one that decided it, or "none"):
%s
"""

# r2: rule 1 stops the whole cascade. When NEITHER event has a channel — which
# is most of this corpus — the judge returns a tie immediately. Measured: 61%
# ties across two runs (120 of 197), so ~3 of every 5 paid calls produced no
# training signal at all.
_RULES_R2 = """
ORDERING RULES — apply in order, stop at the first that separates the pair:
 1. Channel over no channel. An event touching a channel beats one that does
    not, however technically impressive the latter is.
 2. Quantity over topic. An event that changes a NUMBER in a channel beats one
    that merely relates to it. "Trained on 100k H100s" moves a number;
    "we care about efficiency" does not.
 3. Sooner over later. Shipped/contracted/hired beats stated intention.
    Announced beats rumoured.
 4. Specific over vague. Named parties, dates, magnitudes, model names.
 5. New over restated. An echo of an earlier event carries less.
 6. Otherwise "tie". Ties are a legitimate answer — forcing a winner on an
    equal pair injects noise.
"""

# r3: the ONLY change is when a tie is permitted. Rules 1-5 are word-for-word
# identical to r2 so the comparison isolates that single variable.
_RULES_R3 = """
ORDERING RULES — apply in order, stop at the first that SEPARATES the pair.
A rule only separates a pair when it applies to one event and not the other.

 1. Channel over no channel. An event touching a channel beats one that does
    not, however technically impressive the latter is.
    -> If BOTH have a channel, or NEITHER does, this rule does not separate
       them. Continue to rule 2. Do NOT answer "tie" here.
 2. Quantity over topic. An event that changes a NUMBER in a channel beats one
    that merely relates to it. "Trained on 100k H100s" moves a number;
    "we care about efficiency" does not.
 3. Sooner over later. Shipped/contracted/hired beats stated intention.
    Announced beats rumoured.
 4. Specific over vague. Named parties, dates, magnitudes, model names.
 5. New over restated. An echo of an earlier event carries less.
 6. "tie" — LAST RESORT ONLY. Use it when rules 1-5 have all been checked and
    every one of them failed to separate the pair. Two channel-less research
    posts are still separable on recency (3), specificity (4) and novelty (5),
    so "neither has a channel" is NOT a reason to tie.

Most pairs ARE separable. Reach for a tie only when you genuinely cannot
choose after working through all five rules.
"""

_BANNED = """
BANNED REASONS:
 - Lab identity or prestige. You are not told which lab published these, and
   guessing is a rule violation.
 - Technical impressiveness on its own. A benchmark SOTA is channel "none"
   unless it implies compute, energy, data or displacement.

Reply with ONLY this JSON:
{"winner": "a" | "b" | "tie", "thesis_channel": "<channel or none>",
 "rule": <1-6>, "confidence": "high" | "medium" | "low",
 "reason": "<one line citing the rule>"}

(`tie` is permitted only by prompt versions that offer it; `confidence` is
optional for those and REQUIRED for binary versions.)"""

# r4: forced binary. Removing the tie option makes the judge DECISIVE, which is
# not the same as deterministic — on a genuinely equal pair a forced choice is a
# coin flip, and that noise enters training looking like signal. So the tie is
# not deleted, it is MOVED into `confidence`: the judge must still choose, but
# must say when the choice was arbitrary. Low-confidence pairs can then be
# down-weighted or dropped at training time, which a silent coin flip cannot be.
_RULES_R4 = """
ORDERING RULES — apply in order, stop at the first that SEPARATES the pair.
A rule only separates a pair when it applies to one event and not the other.

 1. Channel over no channel. An event touching a channel beats one that does
    not, however technically impressive the latter is.
    -> If BOTH have a channel, or NEITHER does, this rule does not separate
       them. Continue to rule 2.
 2. Quantity over topic. An event that changes a NUMBER in a channel beats one
    that merely relates to it. "Trained on 100k H100s" moves a number;
    "we care about efficiency" does not.
 3. Sooner over later. Shipped/contracted/hired beats stated intention.
    Announced beats rumoured.
 4. Specific over vague. Named parties, dates, magnitudes, model names.
 5. New over restated. An echo of an earlier event carries less.

YOU MUST CHOOSE "a" OR "b". "tie" is not an available answer.

Instead, report how forced the choice was:
  "high"   — a rule clearly separated them; you would answer the same way again.
  "medium" — a rule separated them, but weakly.
  "low"    — no rule separated them and you effectively guessed. SAY SO. A
             truthful "low" is far more useful than a confident coin flip,
             because low-confidence pairs are excluded from training.

Do not inflate confidence. Roughly speaking, if you would not give the same
answer when shown the two events in the opposite order, that is "low".
"""

JUDGE_RULES = {"r2": _RULES_R2, "r3": _RULES_R3, "r4": _RULES_R4}


def build_system(channels: list[str], version: str = JUDGE_VERSION) -> str:
    if version not in JUDGE_RULES:
        raise SystemExit(f"unknown judge version {version!r}; "
                         f"have {sorted(JUDGE_RULES)}")
    return (_PREAMBLE % "\n".join(f" - {c}" for c in channels)
            + JUDGE_RULES[version] + _BANNED)


def _event_block(conn, event_id: int, letter: str) -> str:
    """Claim, type, date and verified quote. NO lab name — see module docstring."""
    r = conn.execute(
        "SELECT i.event_type, i.claim, ev.verbatim_content q, d.published_at"
        " FROM insights i"
        " JOIN evidence ev ON ev.id = i.evidence_id"
        " JOIN raw_documents d ON d.id = ev.document_id"
        " WHERE i.id = ?", (event_id,)).fetchone()
    return (f"EVENT {letter}\n"
            f"  type: {r['event_type']}\n"
            f"  date: {(r['published_at'] or 'unknown')[:10]}\n"
            f"  claim: {r['claim']}\n"
            f"  quote: \"{r['q'][:400]}\"")


def presentation_order(a: int, b: int) -> bool:
    """True if the pair should be shown swapped (b as A).

    WHY: `sample_pairs` stores pairs as (min(id), max(id)), so without this the
    lower insight id is ALWAYS event A. Ids follow extraction order, and the
    first run showed B's quotes averaging 203 characters against A's 172 — so
    position and content were confounded, and the judge returned a=16 / b=41.
    That 10.8% a-rate was measuring the sampler, not the events.

    Deterministic in the pair, so the experiment stays reproducible and a
    re-run never re-asks the same pair in the other order.
    """
    return ((a * 31 + b * 17) % 2) == 1


def build_prompt(conn, a: int, b: int) -> str:
    first, second = (b, a) if presentation_order(a, b) else (a, b)
    return f"{_event_block(conn, first, 'A')}\n\n{_event_block(conn, second, 'B')}"


def unswap(a: int, b: int, winner: str) -> str:
    """Translate the judge's A/B answer back to the stored (a, b) order."""
    if winner == "tie" or not presentation_order(a, b):
        return winner
    return "b" if winner == "a" else "a"


BINARY_VERSIONS = {"r4"}


def _parse(raw: str, version: str = JUDGE_VERSION) -> dict | None:
    """Verdict, or None if unusable. A verdict with no rule number is unusable:
    the citation is what makes the judgement auditable. For a binary version a
    verdict with no confidence is also unusable — confidence is the only thing
    standing between a forced choice and undetectable noise."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text[4:] if text.startswith("json") else text
    try:
        v = json.loads(text)
    except json.JSONDecodeError:
        return None
    if v.get("winner") not in ("a", "b", "tie"):
        return None
    if not isinstance(v.get("rule"), int) or not 1 <= v["rule"] <= 6:
        return None
    if version in BINARY_VERSIONS:
        if v["winner"] == "tie":
            return None                       # not on offer; retry
        if v.get("confidence") not in ("high", "medium", "low"):
            return None
    return v


def judge_pairs(conn, n: int = 150, dry_run: bool = False,
                version: str = JUDGE_VERSION) -> dict:
    from fli.ops.llm import LLM, MODEL_FOR_TASK, have_api_key

    policy = load_policy()
    system = build_system(list(policy.channels) + ["none"], version)
    labeler = f"llm:{MODEL_FOR_TASK['judge']}/{version}"

    pairs = sample_pairs(conn, n)
    done = {(r["event_a"], r["event_b"]) for r in conn.execute(
        "SELECT event_a, event_b FROM pairwise_labels WHERE labeler = ?", (labeler,))}
    todo = [p for p in pairs if p not in done]

    print(f"pairwise judge — {labeler}")
    print(f"  {len(pairs)} sampled, {len(done)} already judged, {len(todo)} to do")

    if dry_run:
        if todo:
            a, b = todo[0]
            print("\n--- SYSTEM ---\n" + system)
            print("\n--- USER (first pair) ---\n" + build_prompt(conn, a, b))
        print("\nDRY RUN — nothing sent, nothing spent.")
        return {"dry_run": True, "todo": len(todo)}

    if not have_api_key():
        raise SystemExit("ANTHROPIC_API_KEY not set (put it in .env).")

    llm = LLM(conn)
    stats = Counter()
    for i, (a, b) in enumerate(todo, 1):
        user = build_prompt(conn, a, b)
        verdict = _parse(llm.call("judge", system, user, max_tokens=300), version)
        if verdict is None:
            # one retry with an explicit correction, then give up and COUNT it
            verdict = _parse(llm.call(
                "judge", system,
                user + "\n\nYour previous reply was unusable. Reply with ONLY "
                       "valid JSON containing an integer `rule` 1-6"
                       + (", a `confidence` of high/medium/low, and a `winner` "
                          "of a or b (NOT tie)." if version in BINARY_VERSIONS
                          else "."),
                max_tokens=300), version)
        if verdict is None:
            stats["unparseable"] += 1
            print(f"  [{i:>3}/{len(todo)}] {a} vs {b}  UNPARSEABLE (counted, not skipped)")
            continue
        winner = unswap(a, b, verdict["winner"])   # back to stored (a, b) order
        record_label(conn, a, b, winner, labeler,
                     thesis_channel=verdict.get("thesis_channel"),
                     reason=(f"rule {verdict['rule']}"
                             + (f" conf={verdict['confidence']}"
                                if verdict.get("confidence") else "")
                             + f": {verdict.get('reason', '')}")[:400])
        stats[winner] += 1
        stats["shown_first_" + ("b" if presentation_order(a, b) else "a")] += 1
        stats[f"rule{verdict['rule']}"] += 1
        if verdict.get("confidence"):
            stats["conf_" + verdict["confidence"]] += 1
        print(f"  [{i:>3}/{len(todo)}] {a} vs {b}  -> {winner:<4}"
              f" rule {verdict['rule']}  {verdict.get('thesis_channel')}")

    total = stats["a"] + stats["b"] + stats["tie"]
    print(f"\njudged {total}, unparseable {stats['unparseable']}")
    if total:
        # Position bias is measurable because A/B order is stable. A judge that
        # picks 'a' 80% of the time is answering a different question.
        decided = stats['a'] + stats['b']
        print(f"  winner distribution: a={stats['a']} b={stats['b']} tie={stats['tie']}")
        print(f"  ties are {stats['tie'] / total:.0%} of judgements — only "
              f"{decided} pairs carry training signal")
        if decided:
            print(f"  a-rate among decided: {stats['a'] / decided:.1%} "
                  f"(50% expected; presentation order IS randomised, so a large "
                  f"deviation is a real asymmetry in the events, not the sampler)")
        rules = {k: v for k, v in stats.items() if k.startswith("rule")}
        print(f"  rules cited: {dict(sorted(rules.items()))}")
    return dict(stats)


def consistency_check(conn, n: int = 40, version: str = JUDGE_VERSION) -> dict:
    """Judge each pair BOTH ways and count how often the answer flips.

    This is the only honest test of determinism. "Is the judge deterministic?"
    cannot be answered by removing the tie option — that makes it decisive, not
    reproducible. Asking the same pair in both presentation orders turns the
    question into a measurement:

        agree    -> the verdict is a property of the EVENTS
        flip     -> the verdict is a property of the ORDER; an empirical tie,
                    detected rather than self-reported

    Costs 2 calls per pair, so it runs on a small sample. The flip rate is the
    headline number: a judge that flips on 30% of pairs has an effective
    accuracy ceiling of 85% no matter what the ranker does downstream.
    """
    from fli.ops.llm import LLM, have_api_key
    if not have_api_key():
        raise SystemExit("ANTHROPIC_API_KEY not set (put it in .env).")
    policy = load_policy()
    system = build_system(list(policy.channels) + ["none"], version)
    llm = LLM(conn)
    pairs = sample_pairs(conn, n)[:n]

    print(f"consistency check — {version}, {len(pairs)} pairs x 2 orders "
          f"= {len(pairs) * 2} calls")
    agree = flip = unusable = 0
    conf_flip = Counter()
    for i, (a, b) in enumerate(pairs, 1):
        out = {}
        for swapped in (False, True):
            first, second = (b, a) if swapped else (a, b)
            user = (f"{_event_block(conn, first, 'A')}\n\n"
                    f"{_event_block(conn, second, 'B')}")
            v = _parse(llm.call("judge", system, user, max_tokens=300), version)
            if v is None:
                out = None
                break
            # normalise to "which stored id won", independent of display order
            win = v["winner"]
            out[swapped] = (first if win == "a" else second if win == "b" else "tie",
                            v.get("confidence"))
        if out is None:
            unusable += 1
            continue
        same = out[False][0] == out[True][0]
        agree += same
        flip += not same
        if not same:
            conf_flip[out[False][1] or "n/a"] += 1
        print(f"  [{i:>3}/{len(pairs)}] {a} vs {b}  "
              f"{'agree' if same else 'FLIP '}  "
              f"({out[False][1] or '-'}/{out[True][1] or '-'})")

    total = agree + flip
    print(f"\n  agree {agree}  flip {flip}  unusable {unusable}")
    if total:
        print(f"  FLIP RATE {flip / total:.1%} — the share of verdicts that are a "
              f"property of\n  presentation order rather than of the events. "
              f"Effective accuracy ceiling {1 - flip / total / 2:.1%}.")
        if conf_flip:
            print(f"  flips by stated confidence: {dict(conf_flip)}")
            if conf_flip.get("high"):
                print(f"  WARNING {conf_flip['high']} flip(s) were called 'high' "
                      f"confidence — the confidence field is not calibrated.")
    return {"agree": agree, "flip": flip, "unusable": unusable,
            "flip_rate": flip / total if total else None}


def compare_versions(conn, v1: str = "r2", v2: str = "r3") -> dict:
    """Head-to-head on the pairs BOTH versions judged.

    This is why old prompts are kept: the seeded sample means both versions see
    the identical pairs, so the only variable is the rules block. Anything else
    would be comparing two experiments rather than two prompts.
    """
    from fli.ops.llm import MODEL_FOR_TASK
    model = MODEL_FOR_TASK["judge"]
    rows = {}
    for v in (v1, v2):
        rows[v] = {(r["event_a"], r["event_b"]): r for r in conn.execute(
            "SELECT event_a, event_b, winner, reason FROM pairwise_labels"
            " WHERE labeler = ?", (f"llm:{model}/{v}",))}
        n = len(rows[v])
        ties = sum(1 for r in rows[v].values() if r["winner"] == "tie")
        decided = n - ties
        a = sum(1 for r in rows[v].values() if r["winner"] == "a")
        print(f"  {v}: {n:>4} judged, {ties:>4} ties ({ties / n:.0%})"
              f", {decided:>4} decided"
              + (f", a-rate {a / decided:.0%}" if decided else "")
              if n else f"  {v}: none — run `fli.cli judge --version {v}`")

    both = set(rows[v1]) & set(rows[v2])
    if not both:
        print("\n  no overlapping pairs; run both versions at the same --n")
        return {}

    agree = sum(1 for k in both if rows[v1][k]["winner"] == rows[v2][k]["winner"])
    broke = [k for k in both
             if rows[v1][k]["winner"] == "tie" and rows[v2][k]["winner"] != "tie"]
    added = [k for k in both
             if rows[v1][k]["winner"] != "tie" and rows[v2][k]["winner"] == "tie"]
    flipped = [k for k in both
               if "tie" not in (rows[v1][k]["winner"], rows[v2][k]["winner"])
               and rows[v1][k]["winner"] != rows[v2][k]["winner"]]

    print(f"\n  {len(both)} pairs judged by both")
    print(f"    identical verdict        {agree:>4}  ({agree / len(both):.0%})")
    print(f"    {v1} tie -> {v2} decided     {len(broke):>4}  <- the point of {v2}")
    print(f"    {v1} decided -> {v2} tie     {len(added):>4}")
    print(f"    both decided, DISAGREE   {len(flipped):>4}  <- {v2} did not just "
          f"break ties, it changed its mind")
    if flipped:
        print(f"\n  a flip is the concerning case — rules 1-5 are identical between "
              f"{v1} and {v2},\n  so a decided pair should not change winner. "
              f"{len(flipped)} of {len(both) - len(broke) - len(added)} did.")
    return {"n_both": len(both), "agree": agree, "tie_broken": len(broke),
            "tie_added": len(added), "flipped": len(flipped)}


def main() -> None:
    ap = argparse.ArgumentParser(description="LLM pairwise judge. SPENDS MONEY.")
    ap.add_argument("--db", default=str(storage.DEFAULT_DB))
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the prompt and exit without spending")
    ap.add_argument("--version", default=JUDGE_VERSION,
                    choices=sorted(JUDGE_RULES),
                    help=f"prompt version (default {JUDGE_VERSION})")
    ap.add_argument("--compare", nargs=2, metavar=("V1", "V2"),
                    help="head-to-head two versions on the pairs both judged")
    ap.add_argument("--consistency", type=int, metavar="N",
                    help="judge N pairs BOTH ways and report the flip rate "
                         "(2N calls; the real determinism test)")
    args = ap.parse_args()
    conn = storage.connect(Path(args.db))
    storage.init_db(conn)
    if args.compare:
        compare_versions(conn, *args.compare)
        return
    if args.consistency:
        consistency_check(conn, args.consistency, args.version)
        return
    judge_pairs(conn, args.n, dry_run=args.dry_run, version=args.version)


if __name__ == "__main__":
    main()
