"""Contributor scoring: which tracked people matter most right now."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from fli import storage
from fli.intelligence.features import _recency


def _linked_events(conn, rubric: str) -> list:
    """(person_id, event_id, roles, rank, published_at) for every distinct
    person↔event link that has a score under the rubric's winning model."""
    return conn.execute(
        "WITH ranked AS ("
        "  SELECT event_id, rank FROM event_scores"
        "  WHERE model LIKE ? AND components LIKE '%\"winner\"%'),"
        " links AS ("
        "  SELECT person_id, event_id, role FROM event_entities"
        "   WHERE entity_kind = 'person'"
        "  UNION"
        "  SELECT attributed_person_id, id, 'attributed' FROM insights"
        "   WHERE attributed_person_id IS NOT NULL)"
        " SELECT l.person_id, l.event_id, group_concat(DISTINCT l.role) roles,"
        "  r.rank,"
        "  COALESCE(d.published_at,"
        "    CASE WHEN ev.locator LIKE '%mobility_synthesis%'"
        "         THEN json_extract(ev.locator,'$.to_first_observed') END)"
        "    AS published_at"
        " FROM links l"
        " JOIN ranked r ON r.event_id = l.event_id"
        " JOIN insights i ON i.id = l.event_id"
        " JOIN evidence ev ON ev.id = i.evidence_id"
        " JOIN raw_documents d ON d.id = ev.document_id"
        " GROUP BY l.person_id, l.event_id",
        (f"{rubric}:%",)).fetchall()


def compute(conn, rubric: str) -> list[dict]:
    """Score every linked person under `rubric` and persist the result."""
    n_scored = conn.execute(
        "SELECT count(*) FROM event_scores WHERE model LIKE ?"
        " AND components LIKE '%\"winner\"%'", (f"{rubric}:%",)).fetchone()[0]
    if not n_scored:
        raise SystemExit(f"no winner scores for rubric {rubric!r} — run"
                         f" `python -m fli.cli score --bakeoff --rubric {rubric}` first")
    now = datetime.now(timezone.utc)
    per_person: dict[int, list[dict]] = {}
    for r in _linked_events(conn, rubric):
        pctl = (n_scored - r["rank"] + 1) / n_scored
        rec = _recency(r["published_at"], now)
        per_person.setdefault(r["person_id"], []).append({
            "event_id": r["event_id"], "roles": r["roles"],
            "percentile": round(pctl, 4), "recency": round(rec, 4),
            "contribution": round(pctl * rec, 4)})
    ts = storage.now_utc()
    conn.execute("DELETE FROM contributor_scores WHERE rubric = ?", (rubric,))
    out = []
    for pid, evs in per_person.items():
        evs.sort(key=lambda e: -e["contribution"])
        score = sum(e["contribution"] for e in evs)
        comp = {"rubric": rubric, "n_events": len(evs), "top_events": evs[:5]}
        conn.execute(
            "INSERT OR REPLACE INTO contributor_scores (person_id, rubric,"
            " score, n_events, components, created_at) VALUES (?,?,?,?,?,?)",
            (pid, rubric, score, len(evs), json.dumps(comp), ts))
        out.append({"person_id": pid, "score": score, "n_events": len(evs)})
    conn.commit()
    out.sort(key=lambda d: -d["score"])
    return out


def top(conn, rubric: str, k: int = 20) -> list:
    return conn.execute(
        "SELECT cs.person_id, p.canonical_name, p.seniority_tier,"
        " (SELECT l.name FROM affiliations a JOIN labs l ON l.id = a.lab_id"
        "   WHERE a.person_id = p.id ORDER BY a.observed_at DESC LIMIT 1) lab,"
        " cs.score, cs.n_events, cs.components"
        " FROM contributor_scores cs JOIN people p ON p.id = cs.person_id"
        " WHERE cs.rubric = ? ORDER BY cs.score DESC, cs.person_id LIMIT ?",
        (rubric, k)).fetchall()


def tier_mix(rows) -> str:
    """One-line seniority sanity check over a ranking slice."""
    from collections import Counter
    c = Counter((r["seniority_tier"] or "untiered") for r in rows)
    return ", ".join(f"{t}: {n}" for t, n in c.most_common())


def print_top(conn, rubric: str, k: int = 20) -> None:
    rows = top(conn, rubric, k)
    print(f"top {len(rows)} contributors — rubric {rubric}"
          f" (score = Σ event-percentile × recency; no new weights)")
    for n, r in enumerate(rows, 1):
        evs = json.loads(r["components"])["top_events"]
        why = ", ".join(f"#{e['event_id']}({e['contribution']:.2f})"
                        for e in evs[:3])
        print(f"  {n:>2}. {r['canonical_name']:<28} {r['score']:6.2f} "
              f"({r['n_events']} events) [{r['seniority_tier'] or '-'}]"
              f" {r['lab'] or '-'}  top: {why}")
    if rows:
        print(f"\ntier mix of this slice — {tier_mix(rows)}")


def review(conn, rubric: str, k: int = 20, reviewer: str = "soham") -> dict:
    """Keep/cut audit of the contributor ranking — precision@k, human tier."""
    rows = top(conn, rubric, k)
    print(f"contributor review — {rubric}, top {len(rows)}, reviewer {reviewer}")
    print("keep = you'd want their next move surfaced; cut = noise.\n"
          "k(eep) / c(ut) / s(kip) / q(uit)\n")
    kept = cut = 0
    for n, r in enumerate(rows, 1):
        evs = json.loads(r["components"])["top_events"]
        print(f"{n}. {r['canonical_name']} [{r['seniority_tier'] or '-'} · "
              f"{r['lab'] or 'no lab'}] score {r['score']:.2f} over "
              f"{r['n_events']} event(s)")
        for e in evs[:3]:
            claim = conn.execute("SELECT claim FROM insights WHERE id=?",
                                 (e["event_id"],)).fetchone()
            print(f"   {e['contribution']:.2f} ({e['roles']}) "
                  f"{claim['claim'][:120] if claim else '?'}")
        ans = input("   k/c/s/q > ").strip().lower()
        if ans in ("q", "quit"):
            break
        verdict = {"k": "keep", "c": "cut"}.get(ans)
        if verdict is None:
            print("   (skipped)\n")
            continue
        conn.execute(
            "INSERT OR REPLACE INTO contributor_reviews (person_id, rubric,"
            " reviewer, verdict, reviewed_at) VALUES (?,?,?,?,?)",
            (r["person_id"], rubric, reviewer, verdict, storage.now_utc()))
        conn.commit()
        kept += verdict == "keep"
        cut += verdict == "cut"
        print(f"   saved: {verdict}\n")
    total = kept + cut
    if total:
        print(f"\n{rubric}: {kept}/{total} kept — contributor precision@{total}"
              f" {kept / total:.0%}.")
    return {"kept": kept, "cut": cut}


def main() -> int:
    from fli.intelligence.scoring import primary_rubric
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(storage.DEFAULT_DB))
    ap.add_argument("--rubric", choices=["investment", "ai_team"], default=None,
                    help="default: policy primary_rubric")
    ap.add_argument("--top", type=int, default=20, help="how many to print")
    ap.add_argument("--review", action="store_true",
                    help="human keep/cut audit of the current top-K")
    ap.add_argument("--reviewer", default="soham")
    ap.add_argument("--k", type=int, default=20, help="review depth")
    args = ap.parse_args()
    conn = storage.connect(Path(args.db))
    storage.init_db(conn)
    rubric = args.rubric or primary_rubric()
    if args.review:
        review(conn, rubric, k=args.k, reviewer=args.reviewer)
        return 0
    ranked = compute(conn, rubric)
    print(f"scored {len(ranked)} people with at least one linked event\n")
    print_top(conn, rubric, args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
