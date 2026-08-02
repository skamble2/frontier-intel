"""The frozen X benchmark: 29 labelled posts, an extension set, and scoring."""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

from fli.core.paths import FIXTURES_DIR

BENCHMARK_PATH = FIXTURES_DIR / "x-benchmark-29-frozen.json"
LABELS_PATH = FIXTURES_DIR / "x-benchmark-29-labels-frozen.json"
EXT_POSTS_PATH = FIXTURES_DIR / "x-benchmark-ext-posts.json"
EXT_LABELS_PATH = FIXTURES_DIR / "x-benchmark-ext-labels.json"


def load_benchmark(path: Path | None = None) -> list[dict]:
    """The frozen 29 plus any extension posts. """
    if path is not None:
        if not path.exists():
            return []
        return json.loads(path.read_text()).get("posts", [])
    return load_benchmark(BENCHMARK_PATH) + load_benchmark(EXT_POSTS_PATH)


def _normalise(rows: list[dict]) -> list[dict]:
    """`audited` and `is_signal` were written as the strings "True"/"False".
    `audited` and `is_signal` were written as the strings "True"/"False"."""
    out = []
    for r in rows:
        r = dict(r)
        for k in ("audited", "is_signal"):
            if isinstance(r.get(k), str):
                r[k] = r[k].strip().lower() == "true"
        out.append(r)
    return out


def load_labels(path: Path | None = None) -> dict[str, dict]:
    """Reference labels keyed by post id."""
    if path is not None:
        if not path.exists():
            return {}
        return {str(r["id"]): r for r in _normalise(json.loads(path.read_text()))}
    out = load_labels(LABELS_PATH)
    for pid, r in load_labels(EXT_LABELS_PATH).items():
        if r.get("audited") is True:
            out[pid] = r
    return out


def _prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4),
            "tp": tp, "fp": fp, "fn": fn}


def channel_scores(posts, labels, policy, channel_fn) -> dict:
    """Micro-averaged precision/recall/F1 for channel assignment."""
    tp = fp = fn = 0
    for post in posts:
        label = labels.get(str(post["id"]))
        if label is None:
            continue
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
    ext = load_labels(EXT_LABELS_PATH)
    channels = {}
    for r in labels.values():
        c = r.get("channel") or "none"
        channels[c] = channels.get(c, 0) + 1
    return {"posts": len(posts), "labels": len(labels),
            "audited": sum(1 for r in labels.values() if r.get("audited")),
            "ext_pending": sum(1 for r in ext.values() if not r.get("audited")),
            "by_channel": channels}


def _post_from_document(url: str, body: str, published_at: str | None) -> dict | None:
    """Rebuild the API post shape from the stored document. """
    m = re.match(r"https://x\.com/([^/]+)/status/(\d+)$", url)
    if not m:
        return None
    text = body.split("\n\n", 1)[1] if "\n\n" in body else body
    return {"id": m.group(2), "handle": m.group(1), "url": url,
            "created_at": published_at, "text": text}


def extend(conn, n: int = 71, seed: int = 29) -> dict:
    """Grow the reference set from posts already in the DB — zero X spend."""
    have = {str(p["id"]) for p in load_benchmark()}
    by_handle: dict[str, list[dict]] = {}
    for r in conn.execute(
            "SELECT url, raw_content, published_at FROM raw_documents"
            " WHERE url LIKE 'https://x.com/%/status/%' ORDER BY url"):
        p = _post_from_document(r["url"], r["raw_content"], r["published_at"])
        if p and p["id"] not in have and p["text"].strip():
            by_handle.setdefault(p["handle"], []).append(p)

    rng = random.Random(seed)
    for posts in by_handle.values():
        rng.shuffle(posts)
    picked: list[dict] = []
    handles = sorted(by_handle)
    while len(picked) < n and any(by_handle.values()):
        for h in handles:
            if by_handle[h] and len(picked) < n:
                picked.append(by_handle[h].pop())
    if not picked:
        print("extend: no unsampled X posts left in the DB.")
        return {"added": 0}

    from fli.knowledge.channels import classify
    from fli.ops.llm import MODEL_FOR_TASK
    verdicts = classify([p["text"] for p in picked], conn=conn)

    ext_posts = (json.loads(EXT_POSTS_PATH.read_text()).get("posts", [])
                 if EXT_POSTS_PATH.exists() else [])
    ext_labels = (json.loads(EXT_LABELS_PATH.read_text())
                  if EXT_LABELS_PATH.exists() else [])
    for p in picked:
        v = verdicts[p["text"]]
        ext_posts.append(p)
        ext_labels.append({
            "id": p["id"], "handle": p["handle"], "url": p["url"],
            "text": p["text"],
            "labeler": f"llm:{MODEL_FOR_TASK['channel']}",
            "audited": False,
            "channel": v.get("channel") or "none",
            "is_signal": (v.get("channel") or "none") != "none",
            "reason": v.get("reason") or ""})
    EXT_POSTS_PATH.write_text(json.dumps(
        {"source": "sampled from data/fli.db raw_documents (already-ingested "
                   "posts; no X API spend)",
         "n": len(ext_posts), "posts": ext_posts}, indent=2))
    EXT_LABELS_PATH.write_text(json.dumps(ext_labels, indent=2))
    print(f"extend: +{len(picked)} posts -> {EXT_POSTS_PATH.name} "
          f"({len(ext_posts)} total, across {len({p['handle'] for p in ext_posts})} handles).")
    print("Seed labels are classifier verdicts and DO NOT COUNT until audited.")
    print("Run `python -m fli.cli xbench --audit` to fold them into the reference.")
    return {"added": len(picked)}


def audit(posts_path: Path = BENCHMARK_PATH,
          labels_path: Path = LABELS_PATH) -> dict:
    """Human audit pass over one labels file — the tier upgrade."""
    from fli.core.policy import load_policy
    posts = {str(p["id"]): p for p in load_benchmark(posts_path)}
    raw = json.loads(labels_path.read_text()) if labels_path.exists() else []
    channels = list(load_policy().channels) + ["none"]
    todo = [r for r in raw
            if not (str(r.get("audited", "")).strip().lower() == "true"
                    or r.get("audited") is True)]
    print(f"X benchmark audit [{labels_path.name}] — {len(raw)} labels, "
          f"{len(raw) - len(todo)} already audited, {len(todo)} to review.")
    print("For each post: ENTER = confirm the stored channel, or type the "
          "correct one.")
    print(f"channels: {', '.join(channels)}")
    print("s = skip (stays unaudited), q = quit (progress is saved)\n")

    audited = corrected = 0
    for r in todo:
        post = posts.get(str(r["id"]))
        if post is None:
            continue
        stored = r.get("channel") or "none"
        text = " ".join((post.get("text") or "").split())
        print(f"POST {r['id']}\n  {text[:400]}")
        print(f"  stored channel: {stored}")
        while True:
            ans = input("  ENTER=confirm / channel / s / q > ").strip().lower()
            if ans in ("", "s", "q") or ans in channels:
                break
            print(f"  not a channel. one of: {', '.join(channels)}")
        if ans == "q":
            break
        if ans == "s":
            print("  (skipped — stays unaudited)\n")
            continue
        if ans and ans != stored:
            r["channel"] = ans
            corrected += 1
            print(f"  corrected: {stored} -> {ans}")
        r["audited"] = True
        audited += 1
        print("  audited\n")

    labels_path.write_text(json.dumps(raw, indent=2))
    print(f"\naudited {audited} label(s) this pass ({corrected} corrected). "
          f"{sum(1 for r in raw if r.get('audited') is True or str(r.get('audited','')).strip().lower() == 'true')} "
          f"of {len(raw)} now carry a human audit.")
    print("re-run `python -m fli.cli evaluate` — f5's tier note updates itself.")
    return {"audited": audited, "corrected": corrected}


def audit_all() -> dict:
    """Audit the frozen set, then the extension set. """
    totals = {"audited": 0, "corrected": 0}
    for posts_path, labels_path in ((BENCHMARK_PATH, LABELS_PATH),
                                    (EXT_POSTS_PATH, EXT_LABELS_PATH)):
        if not labels_path.exists():
            continue
        r = audit(posts_path, labels_path)
        totals["audited"] += r["audited"]
        totals["corrected"] += r["corrected"]
    return totals


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="The frozen X benchmark: summary, audit pass, or extension.")
    ap.add_argument("--audit", action="store_true",
                    help="confirm/correct each unaudited label; sets audited=true")
    ap.add_argument("--extend", type=int, metavar="N",
                    help="sample N already-ingested posts into the extension "
                         "set (they count only once audited)")
    args = ap.parse_args()
    if args.extend:
        from fli import storage
        extend(storage.connect(), n=args.extend)
        return 0
    if args.audit:
        audit_all()
        return 0
    s = summary()
    print(f"X benchmark — {s['posts']} posts, {s['labels']} labels, "
          f"{s['audited']} audited"
          + (f", {s['ext_pending']} extension label(s) pending audit"
             if s["ext_pending"] else ""))
    for c, n in sorted(s["by_channel"].items(), key=lambda kv: -kv[1]):
        print(f"  {c:<28}{n:>4}")
    if s["labels"] and not s["audited"]:
        print("\n  NOTE: no label is human-audited. Numbers measured against "
              "this set are agreement with a stated labeler, not accuracy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
