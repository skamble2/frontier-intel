"""Single entry point: `python -m fli.cli <layer> [options]`.

Deliberately a thin dispatcher. Each layer keeps its own `main()` and its own
flags, and stays runnable on its own (`python -m fli.intelligence.clustering`)
- that independence is the point of the layering, and a facade that re-declared
every flag here would quietly become a second source of truth.

Layer order below is pipeline order, so `--help` doubles as the data flow.
"""
from __future__ import annotations

import importlib
import sys

# command -> module providing main()
COMMANDS = {
    # LAYER 1 - raw sources
    "ingest": "fli.ingestion.feeds",
    "x": "fli.ingestion.x_api",          # paid source; --dry-run first
    # LAYER 2 - knowledge
    "filter": "fli.knowledge.filtering",
    "extract": "fli.knowledge.extraction",        # stage 2 (SPENDS)
    "register": "fli.knowledge.register.cli",
    "expand": "fli.knowledge.expansion",
    # LAYER 3 - intelligence
    "cluster": "fli.intelligence.clustering",
    "features": "fli.intelligence.features",
    "label": "fli.intelligence.labeling",
    "judge": "fli.intelligence.judge",             # LLM pairwise judge (SPENDS)
    "channels": "fli.knowledge.channels",          # LLM channel classifier
    "score": "fli.intelligence.scoring",
    "contributors": "fli.intelligence.contributors",  # people ranked by their events
    # validation + orchestration
    "evaluate": "fli.validation.evaluation",       # figures + report
    "verify": "fli.validation.entailment",         # claim faithfulness (SPENDS)
    "checks": "fli.validation.checks",
    "xbench": "fli.validation.x_benchmark",
    "pipeline": "fli.orchestration.pipeline",
    # LAYER 4 - delivery, the reader-facing surface
    "positions": "fli.delivery.positions",
    "personas": "fli.delivery.personas",          # LLM reading (SPENDS)
    "digest": "fli.delivery.digest",
    "alerts": "fli.delivery.alerts",
    "skeleton": "fli.orchestration.skeleton",
    "web": "fli.web.app",                     # browse UI + candidate review
}

USAGE = "usage: python -m fli.cli {" + "|".join(COMMANDS) + "} [options]"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        print("\nEach command forwards its remaining arguments to the layer, so")
        print("`python -m fli.cli score --help` shows that layer's own options.")
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd not in COMMANDS:
        print(f"unknown command: {cmd}\n{USAGE}", file=sys.stderr)
        return 2
    module = importlib.import_module(COMMANDS[cmd])
    sys.argv = [f"python -m {COMMANDS[cmd]}"] + rest
    return module.main() or 0


if __name__ == "__main__":
    raise SystemExit(main())
