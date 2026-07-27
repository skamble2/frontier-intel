"""`python -m fli.knowledge.register <command>` entry point."""
import argparse
from pathlib import Path

from fli import storage
from fli.knowledge.register.approval import auto_approve, review, show_queue
from fli.knowledge.register.observation import observe
from fli.knowledge.register.reporting import report
from fli.knowledge.register.seeding import seed_people
from fli.knowledge.register.x_identities import seed_x_identities


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["seed", "queue", "approve", "reject",
                                    "auto_approve", "observe", "report",
                                    "x_identities"])
    ap.add_argument("ids", nargs="*", type=int)
    ap.add_argument("--db", default=str(storage.DEFAULT_DB))
    ap.add_argument("--dry-run", action="store_true",
                    help="x_identities: print the cost and spend nothing")
    args = ap.parse_args()

    conn = storage.connect(Path(args.db))
    storage.init_db(conn)
    if args.cmd == "seed":
        seed_people(conn)
    elif args.cmd == "queue":
        show_queue(conn)
    elif args.cmd in ("approve", "reject"):
        review(conn, args.ids, "approved" if args.cmd == "approve" else "rejected")
    elif args.cmd == "auto_approve":
        auto_approve(conn)
    elif args.cmd == "observe":
        observe(conn)
    elif args.cmd == "report":
        report(conn)
    elif args.cmd == "x_identities":
        seed_x_identities(conn, dry_run=args.dry_run)
