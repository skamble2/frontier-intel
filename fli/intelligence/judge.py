"""LLM pairwise judge.

Fills `pairwise_labels` with `llm:<model>/<rubric>/r<version>` rows so the
bake-off has something to train on. Until this runs, scoring has nothing to
learn from.

This is not ground truth. The judge applies a rubric from `config/rubrics/`,
and its reliability is estimated from disagreement with other labelers rather
than assumed — see `fli/intelligence/weak_supervision.py`.

Three properties of the prompt are load-bearing:

1. The lab name is withheld. Rubrics ban lab identity as a reason and per-lab
   precision@10 is the fairness check, so a judge primed by lab prestige would
   invalidate it. It sees claim, type, date and the verified quote.
2. A rule number is required. A verdict that cannot cite the ordering rule that
   decided it is rejected and retried once, which keeps the reasoning auditable.
3. Presentation order is randomised per pair (deterministically) and un-swapped
   on store, so position and content are not confounded.

Model and rubric are both part of the labeler id, so a methodology change lands
as a new labeler instead of mixing into the old one.

Run:  python3 -m fli.cli judge --n 200                # SPENDS
      python3 -m fli.cli judge --n 5 --dry-run        # prompt preview, $0
      python3 -m fli.cli judge --consistency 40       # flip rate (2N calls)
      python3 -m fli.cli judge --agreement A B        # Cohen's kappa, $0
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from fli import storage
from fli.core.policy import load_policy
from fli.intelligence.labeling import record_label, sample_pairs

# The rubric-driven prompt: one format, always binary with a mandatory
# confidence. Of 615 investment verdicts, 274 came back `low` and were excluded
# from training, which raised held-out accuracy — a silent coin flip could not
# have been excluded at all.
_BINARY_TAIL = """
YOU MUST CHOOSE "a" OR "b". "tie" is not an available answer.

Instead, report how forced the choice was:
  "high"   — a rule clearly separated them; you would answer the same way again.
  "medium" — a rule separated them, but weakly.
  "low"    — no rule separated them and you effectively guessed. SAY SO. A
             truthful "low" is more useful than a confident coin flip, because
             low-confidence pairs are excluded from training.

Do not inflate confidence. If you would not give the same answer when shown the
two events in the opposite order, that is "low".
"""


def build_rubric_system(rubric, channels: list[str] | None = None) -> str:
    """Compose the judge prompt from a rubric file.

    Everything audience-specific comes from the YAML. Only the reply contract
    and confidence semantics are fixed here, since the parser depends on them.
    """
    parts = [f"You rank frontier-AI-lab events for: {rubric.audience}.",
             "",
             f"THE ONLY QUESTION: {rubric.question}",
             ""]
    if rubric.use_policy_channels:
        parts += ["CHANNELS (pick the one that decided it, or \"none\"):",
                  *(f" - {c}" for c in (channels or [])), ""]
    parts += ["ORDERING RULES — apply in order, stop at the first that "
              "SEPARATES the pair.",
              "A rule only separates a pair when it applies to one event and "
              "not the other.", ""]
    parts += [f" {i}. {r}" for i, r in enumerate(rubric.rules, 1)]
    parts += ["", "BANNED REASONS:"]
    parts += [f" - {b}" for b in rubric.banned]
    parts += [_BINARY_TAIL, "", "Reply with ONLY this JSON:",
              '{"winner": "a" | "b", '
              + ('"thesis_channel": "<channel or none>", '
                 if rubric.use_policy_channels else "")
              + f'"rule": <1-{len(rubric.rules)}>, '
              + '"confidence": "high" | "medium" | "low", '
              '"reason": "<one line citing the rule>"}']
    return "\n".join(parts)


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

    `sample_pairs` stores pairs as (min(id), max(id)), so without this the lower
    insight id is always event A. Ids follow extraction order, which correlates
    with quote length, so position and content would be confounded.

    Deterministic in the pair: a re-run never re-asks the same pair in the
    other order.
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


def _parse(raw: str, max_rule: int = 6) -> dict | None:
    """Verdict, or None if unusable.

    A verdict with no rule number is unusable: the citation is what makes the
    judgement auditable. A missing confidence is also unusable, since it is the
    only thing separating a forced choice from undetectable noise — the prompt
    is always binary, so every verdict must say how forced it was.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text[4:] if text.startswith("json") else text
    try:
        v = json.loads(text)
    except json.JSONDecodeError:
        return None
    if v.get("winner") not in ("a", "b"):     # "tie" is not on offer; retry
        return None
    if not isinstance(v.get("rule"), int) or not 1 <= v["rule"] <= max_rule:
        return None
    if v.get("confidence") not in ("high", "medium", "low"):
        return None
    return v


def judge_pairs(conn, n: int = 150, dry_run: bool = False,
                model: str | None = None,
                rubric_name: str | None = None) -> dict:
    """Judge pairs under one rubric with one model.

    Both are part of the labeler id (`llm:<model>/<rubric>/r<v>`), and
    `pairwise_labels` is UNIQUE (event_a, event_b, labeler), so:

      - a second model lands as its own labeler over the identical seeded
        pairs, which is what makes an inter-family kappa possible; and
      - a second rubric lands as its own labeler too, so investment and
        technical judgements are never pooled into one training set.

    Two audiences disagreeing about which event matters is the point; averaging
    them would produce a ranking that serves neither.
    """
    from fli.ops.llm import LLM, MODEL_FOR_TASK
    from fli.core.rubric import load_rubric

    policy = load_policy()
    rubric = load_rubric(rubric_name or policy.primary_rubric)
    system = build_rubric_system(rubric, list(policy.channels) + ["none"])
    model = model or MODEL_FOR_TASK["judge"]
    labeler = f"llm:{model}/{rubric.label_suffix}"

    pairs = sample_pairs(conn, n)
    done = {(r["event_a"], r["event_b"]) for r in conn.execute(
        "SELECT event_a, event_b FROM pairwise_labels WHERE labeler = ?", (labeler,))}
    todo = [p for p in pairs if p not in done]

    print(f"pairwise judge — {labeler}")
    print(f"  {len(pairs)} sampled, {len(done)} already judged, {len(todo)} to do")

    # Key + price + projected spend, checked before anything is sent.
    from fli.ops.llm import preflight
    preflight(model, len(todo))

    if dry_run:
        if todo:
            a, b = todo[0]
            print("\n--- SYSTEM ---\n" + system)
            print("\n--- USER (first pair) ---\n" + build_prompt(conn, a, b))
        print("\nDRY RUN — nothing sent, nothing spent.")
        return {"dry_run": True, "todo": len(todo)}

    llm = LLM(conn)                      # key/price already verified above
    stats = Counter()
    for i, (a, b) in enumerate(todo, 1):
        user = build_prompt(conn, a, b)
        nrules = len(rubric.rules)
        verdict = _parse(llm.call("judge", system, user, max_tokens=300,
                                  model=model),
                         max_rule=nrules)
        if verdict is None:
            # one retry with an explicit correction, then give up and COUNT it
            verdict = _parse(llm.call(
                "judge", system,
                user + f"\n\nYour previous reply was unusable. Reply with ONLY "
                       f"valid JSON containing an integer `rule` 1-{nrules}, a "
                       f"`confidence` of high/medium/low, and a `winner` of a "
                       f"or b (NOT tie).",
                max_tokens=300, model=model),
                max_rule=nrules)
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
        # Position bias is measurable because A/B order is stable per pair.
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


def consistency_check(conn, n: int = 40, rubric_name: str | None = None) -> dict:
    """Judge each pair both ways and count how often the answer flips.

    Removing the tie option makes the judge decisive, not reproducible. Asking
    the same pair in both presentation orders is what turns determinism into a
    measurement:

        agree -> the verdict is a property of the events
        flip  -> the verdict is a property of the order; an empirical tie

    Costs 2 calls per pair, so it runs on a small sample. A judge that flips on
    30% of pairs caps effective accuracy at 85%, whatever the ranker does.

    Uses the SAME rubric prompt `judge --n` runs, so the flip rate describes
    the judge that actually produced the labels.
    """
    from fli.ops.llm import LLM, preflight, MODEL_FOR_TASK
    from fli.core.rubric import load_rubric
    model = MODEL_FOR_TASK["judge"]
    preflight(model, n * 2)
    policy = load_policy()
    rubric = load_rubric(rubric_name or policy.primary_rubric)
    system = build_rubric_system(rubric, list(policy.channels) + ["none"])
    llm = LLM(conn)
    pairs = sample_pairs(conn, n)[:n]

    print(f"consistency check — {rubric.label_suffix}, {len(pairs)} pairs "
          f"x 2 orders "
          f"= {len(pairs) * 2} calls")
    agree = flip = unusable = 0
    conf_flip = Counter()
    for i, (a, b) in enumerate(pairs, 1):
        out = {}
        for swapped in (False, True):
            first, second = (b, a) if swapped else (a, b)
            user = (f"{_event_block(conn, first, 'A')}\n\n"
                    f"{_event_block(conn, second, 'B')}")
            v = _parse(llm.call("judge", system, user, max_tokens=300),
                       max_rule=len(rubric.rules))
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


def agreement(conn, labeler_a: str, labeler_b: str) -> dict:
    """Cohen's kappa between two labelers on the pairs they both judged.

    Raw agreement alone is misleading: the verdict is binary, so two labelers
    that each answered 'a' 60% of the time agree ~52% by chance while sharing
    no judgement at all. Kappa subtracts that expectation.

    Most useful across model families. Dawid-Skene assumes labelers are
    conditionally independent, and prompt variants of one model are not —
    measured at 92-100% agreement, which rated all of them ~0.99. Two families
    disagreeing gives the reliability estimate something real to work with.

    Reads only. Costs nothing.
    """
    rows = {}
    for lab in (labeler_a, labeler_b):
        rows[lab] = {(r["event_a"], r["event_b"]): r["winner"] for r in conn.execute(
            "SELECT event_a, event_b, winner FROM pairwise_labels WHERE labeler=?",
            (lab,))}
        print(f"  {lab:<42}{len(rows[lab]):>5} judged")

    both = sorted(set(rows[labeler_a]) & set(rows[labeler_b]))
    if not both:
        print("\n  no overlapping pairs — run both judges at the same --n so the "
              "seeded sample matches.")
        return {}

    va = [rows[labeler_a][k] for k in both]
    vb = [rows[labeler_b][k] for k in both]
    n = len(both)
    observed = sum(1 for x, y in zip(va, vb) if x == y) / n

    labels = sorted(set(va) | set(vb))
    expected = sum((va.count(v) / n) * (vb.count(v) / n) for v in labels)
    kappa = (observed - expected) / (1 - expected) if expected < 1 else float("nan")

    print(f"\n  {n} pairs judged by both")
    print(f"    raw agreement      {observed:.3f}")
    print(f"    expected by chance {expected:.3f}   "
          f"(from each labeler's own a/b/tie rates)")
    print(f"    Cohen's kappa      {kappa:.3f}   {_kappa_reading(kappa)}")
    for lab, v in ((labeler_a, va), (labeler_b, vb)):
        dist = {x: v.count(x) for x in labels}
        print(f"    {lab:<40}{dist}")
    return {"n": n, "observed": observed, "expected": expected, "kappa": kappa}


def _kappa_reading(k: float) -> str:
    """Landis & Koch (1977) bands, named so the number is not over-read."""
    if k != k:
        return "undefined"
    for cut, word in ((0.0, "none — no better than chance"), (0.20, "slight"),
                      (0.40, "fair"), (0.60, "moderate"), (0.80, "substantial")):
        if k <= cut:
            return word
    return "almost perfect — suspiciously high for independent judges"


def main() -> None:
    ap = argparse.ArgumentParser(description="LLM pairwise judge. SPENDS MONEY.")
    ap.add_argument("--db", default=str(storage.DEFAULT_DB))
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the prompt and exit without spending")
    ap.add_argument("--consistency", type=int, metavar="N",
                    help="judge N pairs BOTH ways and report the flip rate "
                         "(2N calls; the real determinism test)")
    ap.add_argument("--model", metavar="MODEL",
                    help="override the judge model, e.g. a second provider. "
                         "Lands as its own labeler id, so both verdicts are "
                         "kept and can be compared with --agreement")
    ap.add_argument("--agreement", nargs=2, metavar=("LABELER_A", "LABELER_B"),
                    help="Cohen's kappa between two labeler ids on the pairs "
                         "both judged. Reads only, spends nothing")
    ap.add_argument("--labelers", action="store_true",
                    help="list labeler ids present in the database")
    ap.add_argument("--rubric", default=None, metavar="NAME",
                    help="which audience's definition of important to judge by "
                         "(config/rubrics/NAME.yml). Default: the policy's "
                         "`primary_rubric`")
    ap.add_argument("--rubrics", action="store_true",
                    help="list available rubrics and exit")
    args = ap.parse_args()
    conn = storage.connect(Path(args.db))
    storage.init_db(conn)
    if args.rubrics:
        from fli.core.rubric import available, load_rubric
        for name in available():
            r = load_rubric(name)
            print(f"  {name:<14} v{r.version}  {r.audience}")
            print(f"  {'':<14} labeler suffix: {r.label_suffix}   "
                  f"{len(r.rules)} rules   "
                  f"channels: {'yes' if r.use_policy_channels else 'no'}")
        return
    if args.labelers:
        for r in conn.execute("SELECT labeler, COUNT(*) n FROM pairwise_labels"
                              " GROUP BY 1 ORDER BY n DESC"):
            print(f"  {r['labeler']:<44}{r['n']:>6}")
        return
    if args.agreement:
        agreement(conn, *args.agreement)
        return
    if args.consistency:
        consistency_check(conn, args.consistency, args.rubric)
        return
    judge_pairs(conn, args.n, dry_run=args.dry_run,
                model=args.model, rubric_name=args.rubric)


if __name__ == "__main__":
    main()
