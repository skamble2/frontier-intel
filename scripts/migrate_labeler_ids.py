"""One-shot: retag pre-rubric labeler ids with the rubric they were made under.

`llm:claude-sonnet-5/r4`  ->  `llm:claude-sonnet-5/investment/r1`

WHY RETAG RATHER THAN DISCARD: the 615 existing judgements were made under the
rules that are now config/rubrics/investment.yml — the channel cascade, quantity
over topic, sooner over later. They are investment judgements; they were simply
made before the rubric had a name. Throwing them away would cost ~$12 and lose
the label count that got held-out accuracy to 0.845.

WHY IT MATTERS THAT THEY ARE TAGGED: `load_pairs(rubric=...)` selects training
labels by the rubric in the labeler id. Untagged rows would be silently absent
from every per-rubric bake-off, and the investment model would train on nothing.

Safe to re-run: the UPDATE only matches the old shape.

    python3 scripts/migrate_labeler_ids.py --db data/fli.db [--apply]
"""
from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path

OLD = re.compile(r"^llm:(?P<model>[^/]+)/r[234]$")
NEW_RUBRIC = "investment"
NEW_VERSION = 1


def plan(conn: sqlite3.Connection) -> list[tuple[str, str, int]]:
    out = []
    for r in conn.execute("SELECT labeler, COUNT(*) n FROM pairwise_labels"
                          " GROUP BY 1 ORDER BY 1"):
        m = OLD.match(r["labeler"])
        if m:
            out.append((r["labeler"],
                        f"llm:{m['model']}/{NEW_RUBRIC}/r{NEW_VERSION}",
                        r["n"]))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/fli.db")
    ap.add_argument("--apply", action="store_true",
                    help="without this the script only prints what it would do")
    args = ap.parse_args()
    conn = sqlite3.connect(Path(args.db))
    conn.row_factory = sqlite3.Row

    moves = plan(conn)
    if not moves:
        print("nothing to migrate — no labeler ids in the pre-rubric shape.")
        return
    for old, new, n in moves:
        print(f"  {old:<38} -> {new:<44} {n:>5} rows")
    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        return

    for old, new, _n in moves:
        # A collision would mean the same pair judged under both ids, which
        # UNIQUE(event_a, event_b, labeler) would reject. Checked, not assumed.
        clash = conn.execute(
            "SELECT COUNT(*) FROM pairwise_labels a JOIN pairwise_labels b"
            " ON a.event_a=b.event_a AND a.event_b=b.event_b"
            " WHERE a.labeler=? AND b.labeler=?", (old, new)).fetchone()[0]
        if clash:
            raise SystemExit(f"{clash} pair(s) already exist under {new}; "
                             f"refusing to merge two label sets silently.")
        conn.execute("UPDATE pairwise_labels SET labeler=? WHERE labeler=?",
                     (new, old))
    conn.commit()
    print("\nmigrated. labelers now:")
    for r in conn.execute("SELECT labeler, COUNT(*) n FROM pairwise_labels"
                          " GROUP BY 1 ORDER BY n DESC"):
        print(f"  {r['labeler']:<46}{r['n']:>6}")


if __name__ == "__main__":
    main()
