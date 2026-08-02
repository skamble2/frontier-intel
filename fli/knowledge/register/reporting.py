"""Register reporting - the per-lab balance evidence printed every run (check
C13)."""
import json
import sqlite3

from fli.knowledge.register.approval import valid_candidate_name


def balance_by_lab(conn: sqlite3.Connection) -> dict:
    """Per-lab candidates / approved layer-below / insights. """
    lab_name = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM labs")}
    out = {n: {"candidates": 0, "approved": 0, "insights": 0} for n in lab_name.values()}
    seed_lab: dict[int, set] = {}
    for r in conn.execute("SELECT DISTINCT person_id, lab_id FROM affiliations"
                          " WHERE lab_id IS NOT NULL AND basis='page_verbatim'"):
        seed_lab.setdefault(r["person_id"], set()).add(r["lab_id"])
    for r in conn.execute("SELECT seed_person_ids FROM person_candidates"):
        labs: set = set()
        for sid in json.loads(r["seed_person_ids"]):
            labs |= seed_lab.get(sid, set())
        for lid in labs:
            out[lab_name[lid]]["candidates"] += 1
    for r in conn.execute(
            "SELECT DISTINCT a.lab_id, p.id FROM affiliations a JOIN people p"
            " ON p.id=a.person_id WHERE a.lab_id IS NOT NULL AND p.discovered_via != 'seed'"):
        out[lab_name[r["lab_id"]]]["approved"] += 1
    for r in conn.execute("SELECT l.name, count(DISTINCT ee.event_id) c FROM event_entities ee"
                          " JOIN labs l ON l.id=ee.lab_id GROUP BY 1"):
        out[r["name"]]["insights"] = r["c"]
    return out


def print_balance(conn: sqlite3.Connection) -> None:
    print("register balance (candidates / approved layer-below / insights, per lab):")
    for lab, d in sorted(balance_by_lab(conn).items(),
                         key=lambda x: -x[1]["insights"]):
        print(f"  {lab:<16} candidates={d['candidates']:<4}"
              f" approved={d['approved']:<3} insights={d['insights']}")


def report(conn: sqlite3.Connection) -> None:
    def q(sql):
        return conn.execute(sql).fetchall()

    print("labs:", conn.execute("SELECT count(*) c FROM labs").fetchone()["c"])
    print("people by discovery:")
    for r in q("SELECT discovered_via, count(*) c FROM people GROUP BY 1"):
        print(f"  {r['discovered_via']}: {r['c']}")
    print("identities by platform/tier:")
    for r in q("SELECT platform, confidence_tier, count(*) c"
               " FROM identities GROUP BY 1,2"):
        print(f"  {r['platform']}/{r['confidence_tier']}: {r['c']}")
    print("affiliations by basis:")
    for r in q("SELECT CASE WHEN lab_id IS NULL THEN 'none_recorded' ELSE basis END b,"
               " count(*) c FROM affiliations GROUP BY 1"):
        print(f"  {r['b']}: {r['c']}")
    print("candidates by status:")
    for r in q("SELECT status, count(*) c FROM person_candidates GROUP BY 1"):
        print(f"  {r['status']}: {r['c']}")
    bad = sum(1 for r in q("SELECT name FROM person_candidates WHERE status='pending'")
              if not valid_candidate_name(r["name"]))
    print(f"  pending failing name-hygiene gate (excluded from review): {bad}")
    print("register rejections (distinct by reason+detail; log rows in parens):")
    for r in q("SELECT reason, count(DISTINCT COALESCE(detail,'')) d, count(*) c"
               " FROM rejections WHERE reason LIKE 'seed_%' GROUP BY 1"):
        print(f"  {r['reason']}: {r['d']} ({r['c']} rows)")
    print_balance(conn)
