"""Single entry point: `python -m fli.cli <layer> [options]`."""
from __future__ import annotations

import importlib
import sys

COMMANDS = {
    "ingest": "fli.ingestion.feeds",
    "x": "fli.ingestion.x_api",
    "filter": "fli.knowledge.filtering",
    "extract": "fli.knowledge.extraction",
    "register": "fli.knowledge.register.cli",
    "expand": "fli.knowledge.expansion",
    "cluster": "fli.intelligence.clustering",
    "features": "fli.intelligence.features",
    "label": "fli.intelligence.labeling",
    "judge": "fli.intelligence.judge",
    "channels": "fli.knowledge.channels",
    "score": "fli.intelligence.scoring",
    "contributors": "fli.intelligence.contributors",
    "evaluate": "fli.validation.evaluation",
    "verify": "fli.validation.entailment",
    "faithfulness": "fli.validation.faithfulness",
    "checks": "fli.validation.checks",
    "drift": "fli.validation.drift",
    "xbench": "fli.validation.x_benchmark",
    "pipeline": "fli.orchestration.pipeline",
    "graph": "fli.orchestration.graph",
    "positions": "fli.delivery.positions",
    "personas": "fli.delivery.personas",
    "digest": "fli.delivery.digest",
    "alerts": "fli.delivery.alerts",
    "mcp": "fli.delivery.mcp_server",
    "skeleton": "fli.orchestration.skeleton",
    "web": "fli.web.app",
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
