"""The frozen X benchmark: 29 labelled posts, and scoring against them.

Scores any channel-assignment approach against a fixed, human-readable
reference set. Figure f5 uses it on every `evaluate` run, at zero cost.

The labels are frozen at `fixtures/x-benchmark-29-labels-frozen.json` and there
is deliberately no generator here to rebuild them. Regenerating would spend
money to produce a *different* reference, silently invalidating every number
ever measured against the old one.

Stated limitation, because it changes how the numbers read: the 29 labels carry
`audited: false`. They are one LLM's application of the rubric, not a human
reference, so figures measured against them report agreement with a stated
labeler, never accuracy.
"""
from __future__ import annotations

import json
from pathlib import Path

from fli.core.paths import FIXTURES_DIR

# The frozen files are authoritative. The unfrozen ones are earlier, larger
# exports kept only so the freeze is diffable.
BENCHMARK_PATH = FIXTURES_DIR / "x-benchmark-29-frozen.json"
LABELS_PATH = FIXTURES_DIR / "x-benchmark-29-labels-frozen.json"


def load_benchmark(path: Path = BENCHMARK_PATH) -> list[dict]:
    """The 29 posts, exactly as returned by the live API run on 2026-07-26."""
    if not path.exists():
        return []
    return json.loads(path.read_text()).get("posts", [])


def load_labels(path: Path = LABELS_PATH) -> dict[str, dict]:
    """Reference labels keyed by post id.

    `audited` and `is_signal` were written as the strings "True"/"False".
    Normalised here rather than at every call site: a truthy check on the string
    "False" is a bug that reads as correct.
    """
    if not path.exists():
        return {}
    out = {}
    for r in json.loads(path.read_text()):
        r = dict(r)
        for k in ("audited", "is_signal"):
            if isinstance(r.get(k), str):
                r[k] = r[k].strip().lower() == "true"
        out[str(r["id"])] = r
    return out


def _prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4),
            "tp": tp, "fp": fp, "fn": fn}


def channel_scores(posts, labels, policy, channel_fn) -> dict:
    """Micro-averaged precision/recall/F1 for channel assignment.

    `channel_fn(policy, text) -> channel name or None`. "none" is the negative
    class, so:

        tp  predicted a channel and it is the labelled one
        fp  predicted a channel that is wrong, INCLUDING predicting one where
            the label says none
        fn  labelled a channel and we predicted none, or predicted the wrong one

    A wrong-channel prediction counts once as fp and once as fn. That strict
    reading is deliberate: naming the wrong transmission channel is not a
    partial success, since the channel exists to say which position it touches.
    """
    tp = fp = fn = 0
    for post in posts:
        label = labels.get(str(post["id"]))
        if label is None:
            continue                       # unlabelled posts are not scored
        truth = label.get("channel") or "none"
        truth = None if truth == "none" else truth
        pred = channel_fn(policy, post.get("text", "")) or None
        if pred and truth and pred == truth:
            tp += 1
        else:
            if pred:
                fp += 1
            if truth:
                fn += 1
    return _prf(tp, fp, fn)


def summary() -> dict:
    """Corpus-level facts about the benchmark: size, coverage, audit state."""
    posts, labels = load_benchmark(), load_labels()
    channels = {}
    for r in labels.values():
        c = r.get("channel") or "none"
        channels[c] = channels.get(c, 0) + 1
    return {"posts": len(posts), "labels": len(labels),
            "audited": sum(1 for r in labels.values() if r.get("audited")),
            "by_channel": channels}


def main() -> int:
    s = summary()
    print(f"X benchmark — {s['posts']} posts, {s['labels']} labels, "
          f"{s['audited']} audited")
    for c, n in sorted(s["by_channel"].items(), key=lambda kv: -kv[1]):
        print(f"  {c:<28}{n:>4}")
    if s["labels"] and not s["audited"]:
        print("\n  NOTE: no label is human-audited. Numbers measured against "
              "this set are agreement with a stated labeler, not accuracy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
