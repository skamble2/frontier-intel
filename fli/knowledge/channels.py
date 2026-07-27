"""D4 — the channel classifier. Replaces keyword matching.

WHY THIS EXISTS, measured not assumed: the keyword lexicon reaches
**F1 0.195** on 346 labelled X posts (3 true positives, 20 false). Hypothesis
H4 pre-committed that a lexicon scoring below 0.80 justifies a classifier, and
that if it scored above, the classifier would NOT be built. It scored 0.195, so
this is built and 0.195 is the number it must beat.

WHY THE LEXICON FAILS, which shapes the design: in a corpus that is entirely
about AI, AI vocabulary carries almost no information. The top false-positive
triggers were `energy`, `license`, `gpus`, `open weights`, `cluster` — the
ambient language of the domain. Channel membership is a SEMANTIC question
("does this move a number in a thesis?"), and keywords answer a TOPICAL one.

Caching is the whole cost story: a verdict is keyed on
(sha256(text), policy_version, model). Re-running is free, changing the policy
correctly invalidates, and the experiment stays reproducible.

Run:  python3 -m fli.cli xeval --d4             # head-to-head vs the lexicon
      python3 -m fli.cli channels --corpus      # classify insights in the DB
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from fli import storage
from fli.core.paths import FIXTURES_DIR
from fli.core.policy import load_policy

CACHE_PATH = FIXTURES_DIR / "channel-classifier-cache.json"

CHANNEL_SYSTEM = """You decide how a frontier-AI-lab event reaches a technology
fund's portfolio, or that it does not.

The fund cannot trade the labs: they are private or far above its market-cap
band. An event matters ONLY through one of these transmission channels:

%s

DECIDE ON MECHANISM, NOT TOPIC. The corpus is entirely about AI, so AI
vocabulary is uninformative. "GPU", "cluster", "energy", "license" appearing in
the text means nothing on its own.

Ask: does this change a QUANTITY someone outside the lab must respond to?
 - "900 megawatts contracted in Abilene"        -> energy_datacenter (a number moved)
 - "our model tops the reasoning benchmark"     -> none (impressive, no quantity)
 - "300M people ask ChatGPT health questions"   -> competitive_displacement (a market moved)
 - "we clustered 3,000 values in this analysis" -> none (topical word, no mechanism)
 - "Dr X is joining as research lead"           -> talent_movement
 - "board appointed a new trustee"              -> none (governance, not research talent)

Default to "none". Most posts are marketing, product UX, or research with no
portfolio consequence, and saying so is the correct answer.

Reply with ONLY:
{"channel": "<one of the channels above, or none>",
 "quantity": "<the number/commitment that moved, or null>",
 "confidence": "high" | "medium" | "low",
 "reason": "<one line>"}"""


def _key(text: str, version: int, model: str) -> str:
    h = hashlib.sha256(text.encode()).hexdigest()[:16]
    return f"{h}:v{version}:{model}"


def _load_cache() -> dict:
    return json.loads(CACHE_PATH.read_text()) if CACHE_PATH.exists() else {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=1, ensure_ascii=False) + "\n")


def build_system(policy) -> str:
    lines = []
    for name in policy.channels:
        lines.append(f" - {name}")
    lines.append(" - none")
    return CHANNEL_SYSTEM % "\n".join(lines)


def classify(texts: list[str], conn=None, verbose: bool = True) -> dict[str, dict]:
    """Classify texts, using and updating the on-disk cache.

    Returns {text -> verdict}. Only uncached texts cost anything.
    """
    from fli.ops.llm import LLM, MODEL_FOR_TASK, have_api_key
    policy = load_policy()
    model = MODEL_FOR_TASK["channel"]
    system = build_system(policy)
    cache = _load_cache()

    todo = [t for t in dict.fromkeys(texts)
            if _key(t, policy.version, model) not in cache]
    if verbose:
        print(f"channel classifier — {len(set(texts))} unique texts, "
              f"{len(todo)} uncached (policy v{policy.version}, {model})")
    if todo:
        if not have_api_key():
            raise SystemExit("ANTHROPIC_API_KEY not set (put it in .env).")
        llm = LLM(conn if conn is not None else storage.connect(storage.DEFAULT_DB))
        for i, t in enumerate(todo, 1):
            raw = llm.call("channel", system, t[:2000], max_tokens=200).strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                raw = raw[4:] if raw.startswith("json") else raw
            try:
                v = json.loads(raw)
                if v.get("channel") not in list(policy.channels) + ["none"]:
                    v = {"channel": "none", "quantity": None,
                         "confidence": "low", "reason": "unrecognised channel"}
            except json.JSONDecodeError:
                v = {"channel": "none", "quantity": None,
                     "confidence": "low", "reason": "unparseable"}
            cache[_key(t, policy.version, model)] = v
            if verbose and i % 25 == 0:
                print(f"  {i}/{len(todo)}")
                _save_cache(cache)               # crash-safe: never lose paid calls
        _save_cache(cache)

    return {t: cache[_key(t, policy.version, model)] for t in texts}


def channel_for(text: str, conn=None) -> str | None:
    """Drop-in replacement for Policy.channel_for, backed by the classifier."""
    v = classify([text], conn=conn, verbose=False)[text]
    return None if v["channel"] == "none" else v["channel"]


def main() -> None:
    ap = argparse.ArgumentParser(description="D4 channel classifier.")
    ap.add_argument("--db", default=str(storage.DEFAULT_DB))
    args = ap.parse_args()
    conn = storage.connect(Path(args.db))
    storage.init_db(conn)
    rows = conn.execute(
        "SELECT i.id, ev.verbatim_content q FROM insights i"
        " JOIN evidence ev ON ev.id = i.evidence_id").fetchall()
    verdicts = classify([r["q"] for r in rows], conn=conn)
    from collections import Counter
    print("\ncorpus channel distribution:")
    for ch, n in Counter(v["channel"] for v in verdicts.values()).most_common():
        print(f"  {ch:<26}{n:>5}")


if __name__ == "__main__":
    main()
