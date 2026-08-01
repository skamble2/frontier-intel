"""LLM channel classifier — replaces keyword matching.

The keyword lexicon it replaces reaches F1 0.195 on the labelled X benchmark
(3 true positives, 20 false), which is the number this has to beat.

Why the lexicon fails, which shapes the design: in a corpus entirely about AI,
AI vocabulary carries almost no information. The top false-positive triggers
were `energy`, `license`, `gpus`, `open weights` and `cluster` — the ambient
language of the domain. Channel membership is a semantic question ("does this
move a number in a thesis?") and keywords answer a topical one.

Caching is the cost story: a verdict is keyed on
(sha256(text), policy_version, model), so re-running is free and a policy edit
correctly invalidates.

Run:  python3 -m fli.cli channels        # classify insights in the database
      python3 -m fli.cli evaluate        # head-to-head vs the lexicon (fig 5)
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


def classify(texts: list[str], conn=None, verbose: bool = True,
             batch: bool = False) -> dict[str, dict]:
    """Classify texts, using and updating the on-disk cache.

    Returns {text -> verdict}. Only uncached texts cost anything. `batch`
    sends uncached texts through the Batch API at 50%; items the batch fails
    fall back to a synchronous call, so the cache fills either way.
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
        batch_results: dict[str, str | None] = {}
        if batch:
            batch_results = llm.call_batch(
                "channel", system,
                [(str(i), t[:2000]) for i, t in enumerate(todo)],
                max_tokens=200, model=model)
        for i, t in enumerate(todo, 1):
            raw = batch_results.get(str(i - 1))
            if raw is None:
                raw = llm.call("channel", system, t[:2000], max_tokens=200)
            raw = raw.strip()
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


def cached_verdicts(texts: list[str]) -> dict[str, dict]:
    """Cache-only lookup: {text -> verdict} for texts already classified,
    silently omitting the rest. Never calls the API, so callers that must
    stay free and deterministic (the feature builder) can consume classifier
    verdicts without inheriting its cost or its network dependency. The cache
    is committed to the repo, so "cached" is reproducible, not machine-local.
    """
    policy = load_policy()
    from fli.ops.llm import MODEL_FOR_TASK
    model = MODEL_FOR_TASK["channel"]
    cache = _load_cache()
    out = {}
    for t in dict.fromkeys(texts):
        v = cache.get(_key(t, policy.version, model))
        if v is not None:
            out[t] = v
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="LLM channel classifier.")
    ap.add_argument("--db", default=str(storage.DEFAULT_DB))
    ap.add_argument("--batch", action="store_true",
                    help="classify uncached texts through the Batch API at "
                         "50%% of the synchronous price")
    args = ap.parse_args()
    conn = storage.connect(Path(args.db))
    storage.init_db(conn)
    rows = conn.execute(
        "SELECT i.id, ev.verbatim_content q FROM insights i"
        " JOIN evidence ev ON ev.id = i.evidence_id").fetchall()
    verdicts = classify([r["q"] for r in rows], conn=conn, batch=args.batch)
    from collections import Counter
    print("\ncorpus channel distribution:")
    for ch, n in Counter(v["channel"] for v in verdicts.values()).most_common():
        print(f"  {ch:<26}{n:>5}")


if __name__ == "__main__":
    main()
