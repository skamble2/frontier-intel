"""The periodic report: one audience, one period, every claim cited.

WHAT THE DIGEST IS. Everything upstream produces a ranked list. A ranked list
is not a report — it has no period, no coverage statement, and no way for the
reader to tell an item the system understands from one it merely scored highly.
This module is the reader-facing surface, and it is deliberately the dumbest
thing in the repo: it selects nothing on its own and computes no scores. It
asks `scoring.top_events` for the slate — the same call the persona layer makes
— joins whatever readings exist, and lays it out.

That shared call is the point. The two used to diverge: the persona layer read
the raw ranking straight from `event_scores` while the digest applied the
editorial rules, so ZERO of the ten events rendered for the engineering
audience appeared in the technical digest at any window. Every reading was paid
for and never shown; every published item arrived bare.

ONE CONTENT MODEL, TWO RENDERERS. `blocks()` returns a list of
(style, payload) pairs; markdown and PDF are two functions over that list.
Writing the report twice would let the exported PDF drift from the markdown a
reviewer reads in the repo, and the drift would be silent.

WHAT IT REFUSES TO DO. No item is promoted for having a signed direction, and
`unclear` items are not hidden — 57 of 59 position edges are `unclear` by
design, and a digest that showed only the two signed ones would imply the
system knows more than it does. Coverage is stated in the header, including how
many items carry no reading at all.

Free and deterministic: no LLM call, no network. Re-running overwrites.

Run:  python3 -m fli.cli digest --persona investment --days 7
      python3 -m fli.cli digest --all --days 7 --pdf
"""
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
# The reader is told what `unclear` means BEFORE meeting 8 of them, so it reads
# as a deliberate answer rather than a broken field.
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


def blocks(conn, persona: str, days: int, k: int = 10) -> tuple[list, dict]:
    """(blocks, stats). The single content model both renderers consume."""
    from fli.intelligence.scoring import top_events        # layer 3 -> layer 2

    policy = load_policy()
    rubric = load_rubric(RUBRIC_FOR_PERSONA[persona])
    rows, dropped = top_events(conn, k=k, window_days=days,
                               rubric=rubric.name)
    hyp = _hypotheses(conn, persona)
    pos = _positions(conn) if persona == "investment" else {}

    today = dt.date.today()
    start = today - dt.timedelta(days=days)
    covered = sum(1 for r in rows if r["id"] in hyp)
    stats = {"items": len(rows), "with_reading": covered,
             "window_days": days, "dropped": dropped}

    b: list[tuple[str, object]] = [
        ("h1", f"Frontier Lab Intelligence — {PERSONA_TITLE[persona]}"),
        ("meta", f"{start.isoformat()} to {today.isoformat()}   ·   "
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
                    f"failure: {dropped.get('outside_window', 0)} scored "
                    f"events fell outside the window and "
                    f"{dropped.get('same_story', 0)} were suppressed as "
                    f"repeats of a story already selected."),
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
    """Markdown emphasis is markup, not content.

    The same block list feeds both renderers, so a backtick that reads as code
    formatting in the .md would be printed literally in the PDF. Stripping it
    here keeps the content model renderer-agnostic — the alternative, writing
    two versions of every sentence, is exactly the drift this design avoids.
    """
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
    args = ap.parse_args()

    conn = storage.connect(Path(args.db))
    storage.init_db(conn)
    personas = list(PERSONA_TITLE) if args.all else [args.persona]
    for p in personas:
        write(conn, p, days=args.days, k=args.k, want_pdf=not args.no_pdf)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
