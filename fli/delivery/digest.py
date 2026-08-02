"""The periodic report: one audience, one period, every claim cited."""
from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
from pathlib import Path

from fli import storage
from fli.core.policy import load_policy
from fli.core.rubric import load_rubric
from fli.delivery import pdf as pdf_writer
from fli.delivery.personas import RUBRIC_FOR_PERSONA

OUT_DIR = Path(__file__).resolve().parents[2] / "docs" / "digests"

PERSONA_TITLE = {"investment": "Investment digest",
                 "ai_team": "Engineering digest"}
PERSONA_LEAD = {
    "investment":
        "What frontier labs shipped this period, and what it means for the "
        "fund's positions. Every claim below is quoted from the lab's own "
        "publication and linked to it.",
    "ai_team":
        "What frontier labs shipped this period, and what an engineering team "
        "should do about it. Commercial consequence is out of scope here by "
        "design. Every claim is quoted from the lab's own publication.",
}
UNCLEAR_NOTE = {
    "investment":
        "`unclear` is the most common direction and is an answer, not a gap: "
        "the event touches a holding through an identified mechanism, but the "
        "evidence does not establish which way it points. Acting on a "
        "manufactured direction is the expensive error here.",
    "ai_team":
        "`monitor` and `investigate` outnumber `adopt`, which is the honest "
        "shape of a frontier-lab feed: most of what is published is not "
        "something a team can pick up this quarter.",
}


def _fmt_date(s: str | None) -> str:
    return (s or "undated")[:10]


def _hypotheses(conn, persona: str) -> dict[int, sqlite3.Row]:
    return {r["insight_id"]: r for r in conn.execute(
        "SELECT * FROM hypotheses WHERE persona = ?", (persona,))}


def _positions(conn) -> dict[int, list[sqlite3.Row]]:
    out: dict[int, list[sqlite3.Row]] = {}
    for r in conn.execute(
            "SELECT event_id, ticker, direction, channel, rationale"
            " FROM event_positions ORDER BY ticker"):
        out.setdefault(r["event_id"], []).append(r)
    return out


def _mechanism_first(rows, hyp, pos):
    """Stable reorder: items the system can SAY something about come first."""
    def band(r):
        h = hyp.get(r["id"])
        signed = h is not None and h["direction"] != "unclear"
        mech = any(e["channel"] for e in pos.get(r["id"], []))
        return 0 if (signed or mech) else 1
    return sorted(rows, key=band)


def blocks(conn, persona: str, days: int, k: int = 10) -> tuple[list, dict]:
    """(blocks, stats). The single content model both renderers consume."""
    from fli.intelligence.scoring import slate_anchor, top_events

    policy = load_policy()
    rubric = load_rubric(RUBRIC_FOR_PERSONA[persona])
    rows, dropped = top_events(conn, k=k, window_days=days,
                               rubric=rubric.name)
    hyp = _hypotheses(conn, persona)
    pos = _positions(conn) if persona == "investment" else {}
    rows = _mechanism_first(rows, hyp, pos)

    # The period the header states must be the period the slate enforced:
    # both end at the newest document, not at the wall clock.
    end = slate_anchor(conn).date()
    start = end - dt.timedelta(days=days)
    covered = sum(1 for r in rows if r["id"] in hyp)
    stats = {"items": len(rows), "with_reading": covered,
             "window_days": days, "dropped": dropped}

    b: list[tuple[str, object]] = [
        ("h1", f"Frontier Lab Intelligence — {PERSONA_TITLE[persona]}"),
        ("meta", f"{start.isoformat()} to {end.isoformat()}   ·   "
                 f"rubric {rubric.name} r{rubric.version}   ·   "
                 f"policy v{policy.version}   ·   generated "
                 f"{storage.now_utc()[:16]}Z"),
        ("p", PERSONA_LEAD[persona]),
        ("rule", None),
    ]

    if not rows:
        b += [("h2", "Nothing to report for this period"),
              ("p", f"No event inside the last {days} days survived the slate "
                    f"rules. That is a statement about the period, not a "
                    f"failure — the ledger below says where every scored "
                    f"event went."),
              ("meta", "Suppressed by the slate rules — " + ", ".join(
                  f"{v} {k2.replace('_', ' ')}" for k2, v in sorted(
                      dropped.items(), key=lambda kv: -kv[1])) + "."),
              ("p", "Widen the period with `--days 30` to see the standing "
                    "picture rather than the week's news.")]
        return b, stats

    b += [("h2", "This period at a glance"),
          ("p", f"{len(rows)} item(s) selected from "
                f"{sum(dropped.values()) + len(rows)} scored events. "
                f"{covered} of them carry a written reading; "
                f"{len(rows) - covered} are ranked but not yet read."),
          ("meta", "Suppressed by the slate rules — " + ", ".join(
              f"{v} {k2.replace('_', ' ')}" for k2, v in sorted(
                  dropped.items(), key=lambda kv: -kv[1])) + "."),
          ("meta", "Ordering: items with a signed reading or an established "
                   "mechanism first, score order within each band — the "
                   "per-item score shows where each stood in the raw ranking."),
          ("p", UNCLEAR_NOTE[persona])]

    for n, r in enumerate(rows, 1):
        b.append(("h2", f"{n}. {r['claim']}"))
        b.append(("meta",
                  f"{r['lab']}   ·   {r['event_type']}   ·   "
                  f"{_fmt_date(r['published_at'])}   ·   "
                  f"score {r['score']:.2f}   ·   event {r['id']}"))

        for e in pos.get(r["id"], []):
            mech = e["channel"] or "no mechanism established"
            b.append(("bullet",
                      f"{e['ticker']} — {e['direction']} via {mech}"))

        h = hyp.get(r["id"])
        if h:
            b.append(("h3", f"What it means — {h['direction']}"
                            f" ({h['confidence']} confidence, "
                            f"{h['time_horizon']})"))
            b.append(("p", h["hypothesis"]))
            b.append(("p", h["reasoning"]))
            if h["tickers"]:
                b.append(("meta", f"positions named: {h['tickers']}"))
        else:
            b.append(("meta", "No reading rendered for this event yet "
                              "(`python3 -m fli.cli personas`). It is shown "
                              "here as ranked-only rather than dropped, so the "
                              "gap is visible."))

        quote = " ".join((r["quote"] or "").split())
        b.append(("quote", f"“{quote[:600]}”"))
        b.append(("link", (r["url"], r["url"])))

    b += [("rule", None),
          ("h2", "What this report does not claim"),
          ("p", "The ranking is learned from pairwise judgements made against "
                "the published rubric, not from any measured outcome — no "
                "market return, adoption count or citation is used anywhere. "
                "It orders what a reader of that rubric would call important."),
          ("p", "Direction is stated only where a mechanism was established by "
                "the channel classifier. A keyword match is recorded as "
                "exposure and left `unclear` on purpose: the keyword lexicon "
                "scores F1 0.195 against the classifier's 0.571, and its "
                "failures are confident ones."),
          ("p", "Every quote above was re-verified against the stored bytes of "
                "the source document during this run. An item whose quote no "
                "longer matches its source is removed, not silently reworded.")]
    return b, stats


def to_markdown(b) -> str:
    out: list[str] = []
    for style, payload in b:
        if style == "h1":
            out.append(f"# {payload}\n")
        elif style == "h2":
            out.append(f"\n## {payload}\n")
        elif style == "h3":
            out.append(f"\n### {payload}\n")
        elif style == "meta":
            out.append(f"*{payload}*\n")
        elif style == "quote":
            out.append(f"> {payload}\n")
        elif style == "bullet":
            out.append(f"- {payload}\n")
        elif style == "link":
            label, url = payload
            out.append(f"[{label}]({url})\n")
        elif style == "rule":
            out.append("\n---\n")
        elif style == "p":
            out.append(f"{payload}\n")
    return "\n".join(out).replace("\n\n\n", "\n\n")


def _for_pdf(b):
    """Markdown emphasis is markup, not content."""
    out = []
    for style, payload in b:
        if style == "link" or not isinstance(payload, str):
            out.append((style, payload))
        else:
            out.append((style, payload.replace("`", "")))
    return out


def write(conn, persona: str, days: int = 7, k: int = 10,
          out_dir: Path = OUT_DIR, want_pdf: bool = True,
          verbose: bool = True) -> dict:
    b, stats = blocks(conn, persona, days, k)
    stem = f"{dt.date.today().isoformat()}-{persona}"
    out_dir.mkdir(parents=True, exist_ok=True)
    md = out_dir / f"{stem}.md"
    md.write_text(to_markdown(b), encoding="utf-8")
    written = [md]
    if want_pdf:
        written.append(pdf_writer.render(
            out_dir / f"{stem}.pdf", _for_pdf(b),
            footer=f"frontier-intel · {PERSONA_TITLE[persona]} · "
                   f"{dt.date.today().isoformat()}"))
    if verbose:
        print(f"{persona:<11} {stats['items']:>2} item(s), "
              f"{stats['with_reading']} with a reading  "
              f"(last {days} days)")
        for p in written:
            print(f"    {p}")
        if stats["items"] and stats["with_reading"] < stats["items"]:
            print(f"    NOTE {stats['items'] - stats['with_reading']} ranked "
                  f"item(s) carry no reading. `python3 -m fli.cli personas "
                  f"--k {k}` renders them.")
    stats["files"] = [str(p) for p in written]
    return stats


def review(conn, persona: str, days: int = 7, k: int = 10,
           reviewer: str = "soham") -> dict:
    """Keep/cut pass over the slate the digest would deliver — precision@k."""
    from fli.intelligence.scoring import top_events
    rubric = load_rubric(RUBRIC_FOR_PERSONA[persona])
    rows, _ = top_events(conn, k=k, window_days=days, rubric=rubric.name)
    hyp = _hypotheses(conn, persona)
    pos = _positions(conn) if persona == "investment" else {}
    rows = _mechanism_first(rows, hyp, pos)
    print(f"slate review — {persona}, last {days} days, {len(rows)} item(s), "
          f"reviewer {reviewer}")
    print("keep = you'd want this in the report; cut = noise.\n"
          "k(eep) / c(ut) / s(kip) / q(uit)\n")
    kept = cut = 0
    for n, r in enumerate(rows, 1):
        h = hyp.get(r["id"])
        print(f"{n}. [{r['lab']} · {r['event_type']} · "
              f"{_fmt_date(r['published_at'])}] {r['claim']}")
        if h:
            print(f"   reading: {h['direction']} ({h['confidence']}) — "
                  f"{h['hypothesis'][:140]}")
        for e in pos.get(r["id"], []):
            print(f"   {e['ticker']} — {e['direction']} via "
                  f"{e['channel'] or 'no mechanism'}")
        ans = input("   k/c/s/q > ").strip().lower()
        if ans in ("q", "quit"):
            break
        verdict = {"k": "keep", "c": "cut"}.get(ans)
        if verdict is None:
            print("   (skipped)\n")
            continue
        conn.execute(
            "INSERT OR REPLACE INTO slate_reviews (event_id, persona,"
            " reviewer, verdict, reviewed_at) VALUES (?,?,?,?,?)",
            (r["id"], persona, reviewer, verdict, storage.now_utc()))
        conn.commit()
        kept += verdict == "keep"
        cut += verdict == "cut"
        print(f"   saved: {verdict}\n")
    total = kept + cut
    if total:
        print(f"\n{persona}: {kept}/{total} kept — precision@{total} "
              f"{kept / total:.0%}. Figure f16 picks this up on the next "
              f"`evaluate` run.")
    return {"kept": kept, "cut": cut}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(storage.DEFAULT_DB))
    ap.add_argument("--persona", choices=list(PERSONA_TITLE), default="investment")
    ap.add_argument("--all", action="store_true",
                    help="write both personas' digests")
    ap.add_argument("--days", type=int, default=7,
                    help="reporting period, in days (default: a week)")
    ap.add_argument("--k", type=int, default=10, help="max items")
    ap.add_argument("--no-pdf", action="store_true",
                    help="markdown only, skip the PDF export")
    ap.add_argument("--review", action="store_true",
                    help="keep/cut review of the slate instead of writing it "
                         "(feeds precision@k, figure f16)")
    ap.add_argument("--by", default="soham", help="reviewer name for --review")
    args = ap.parse_args()

    conn = storage.connect(Path(args.db))
    storage.init_db(conn)
    personas = list(PERSONA_TITLE) if args.all else [args.persona]
    for p in personas:
        if args.review:
            review(conn, p, days=args.days, k=args.k, reviewer=args.by)
        else:
            write(conn, p, days=args.days, k=args.k, want_pdf=not args.no_pdf)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
