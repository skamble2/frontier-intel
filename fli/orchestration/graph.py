"""LangGraph packaging of the full run: one command instead of six.

`python -m fli.cli pipeline` already chains the free stages but leaves the
paid ones (`verify --repair`, `personas`, `faithfulness`) as manual CLI steps.
This graph packages ALL of them behind a single entry point with one explicit
gate: paid stages need --spend AND an API key AND an in-run human approval —
the graph pauses at an interrupt with the work sized (how many unaudited
claims), and resumes on the operator's answer. `--yes` skips the pause for
schedulers; declining continues the free path. A default invocation costs
exactly what `pipeline` costs.

The graph owns ONLY ordering and gating — every node body is the same layer
function the CLI commands call, so there is no second implementation of any
stage to drift. One ordering fix over the CLI habit is deliberate: delivery
(digest) runs AFTER claim repair and persona notes, because running them in
the reverse order produced digests citing claims that repair had already
rewritten (2 stale claims found in docs/digests/2026-07-30-ai_team.md).

Run:  python -m fli.cli graph [--db PATH] [--spend] [--max-extract N]
      python -m fli.cli graph --mermaid   # print the topology, run nothing

langgraph is an optional dependency like flask: imported lazily, everything
else runs without it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Annotated, TypedDict

from fli import storage
from fli.delivery import alerts, digest, personas, positions
from fli.ingestion import feeds
from fli.intelligence import clustering, scoring
from fli.intelligence import features as featmod
from fli.knowledge import expansion as expand
from fli.knowledge import extraction, register
from fli.knowledge import filtering as filter1
from fli.core.rubric import available as available_rubrics
from fli.ops import tracing
from fli.ops.llm import LLM, have_api_key
from fli.validation import checks, drift, entailment, evaluation, faithfulness


def _merge(a: dict, b: dict) -> dict:
    return {**a, **b}


class RunState(TypedDict):
    """Everything a node may read or write. The DB connection is NOT state —
    it is bound into the nodes by closure, because state should stay printable
    and the DB is the actual shared medium between stages anyway."""
    spend: bool
    auto_approve: bool                # --yes: schedulers skip the interrupt
    approved: bool                    # resolved at the approve gate
    max_extract: int
    report: Annotated[dict, _merge]   # stage name -> that stage's summary
    verdict: int                      # checks battery exit code


def _spend_ready(state: RunState) -> bool:
    return state["spend"] and have_api_key()


def spend_estimate(conn) -> dict:
    """What the paid stages would actually touch — shown at the interrupt so
    the approval is informed, not a blind y/N."""
    unaudited = conn.execute(
        "SELECT count(1) FROM insights WHERE id NOT IN"
        " (SELECT insight_id FROM claim_checks)").fetchone()[0]
    notes = conn.execute("SELECT count(1) FROM hypotheses").fetchone()[0]
    return {"unaudited_claims": unaudited, "existing_notes": notes}


def build(conn):
    """The compiled graph. Node bodies are one call each into the layer that
    already owns the logic; `tolerant` mirrors pipeline.py exactly — a network
    or API failure in an optional stage must not kill the deterministic run."""
    from langgraph.graph import StateGraph, START, END
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import interrupt

    def node(name, fn, tolerant=False):
        def run(state: RunState) -> dict:
            print(f"\n=== {name} ===")
            with tracing.chain_span(f"node.{name}") as span:
                try:
                    out = fn(state) or "ok"
                    tracing.annotate(span, {tracing.OUTPUT_VALUE: str(out)})
                    return {"report": {name: out}}
                except SystemExit as e:    # a layer refusing is a summary, not a crash
                    tracing.annotate(span, {tracing.OUTPUT_VALUE: f"skipped: {e}"})
                    return {"report": {name: f"skipped: {e}"}}
                except Exception as e:
                    if not tolerant:
                        raise              # chain_span records the exception
                    print(f"{name} unavailable: {e} (deterministic stages unaffected)")
                    tracing.annotate(span, {tracing.OUTPUT_VALUE: f"unavailable: {e}"})
                    return {"report": {name: f"unavailable: {e}"}}
        return name, run

    def extract_node(state):
        if not have_api_key():
            return "skipped: no ANTHROPIC_API_KEY"
        s2 = extraction.extract_all(conn, LLM(conn), max_docs=state["max_extract"])
        extraction.report_measurements(conn)
        return s2

    def observe_node(state):
        register.observe(conn)
        from fli.ingestion.x_api import bearer_token
        for name, gate, fn in [
                ("x bios", bearer_token(), register.reobserve_x_bios),
                ("gh profiles", True, register.observe_gh_profiles)]:
            try:
                if gate:
                    fn(conn)
            except Exception as e:       # network surface, never fatal
                print(f"{name} unavailable: {e}")
        return "ok"

    def score_node(state):
        for name in available_rubrics():
            try:
                scoring.print_report(scoring.bakeoff(conn, rubric=name))
            except SystemExit as e:      # <10 labels for this rubric
                print(f"  {name}: skipped ({e})")
        return "ok"

    def verify_node(state):
        entailment.check_all(conn, LLM(conn))
        entailment.repair_all(conn, LLM(conn))
        return "ok"

    def digest_node(state):
        for persona in digest.PERSONA_TITLE:
            digest.write(conn, persona, days=7)
        return "ok"

    def checks_node(state):
        with tracing.chain_span("node.checks") as span:
            verdict = checks.run(conn)
            tracing.annotate(span, {tracing.OUTPUT_VALUE: f"verdict={verdict}"})
        return {"verdict": verdict, "report": {"checks": "ran"}}

    def approve_node(state):
        """The human gate on spend. --spend states intent at launch; this node
        confirms it mid-run with the actual work sized (interrupt pauses the
        graph until the operator resumes it). --yes skips the pause for
        schedulers. Declining is not an error: the run continues on the free
        path exactly as if --spend had been absent."""
        if not _spend_ready(state):
            return {"approved": False}
        if state.get("auto_approve"):
            return {"approved": True,
                    "report": {"approve": "auto-approved (--yes)"}}
        answer = interrupt({"question": "run the paid stages"
                            " (verify+repair, personas, faithfulness)?",
                            **spend_estimate(conn)})
        granted = str(answer).strip().lower() in ("y", "yes")
        return {"approved": granted,
                "report": {"approve": "granted" if granted else "declined"}}

    g = StateGraph(RunState)
    linear = [
        node("ingest", lambda s: feeds.ingest_all(conn)),
        node("stage1", lambda s: {**filter1.stage1_all(conn),
                                  "near_dups": filter1.suppress_near_dups(conn)}),
        node("expand", lambda s: expand.expand_coauthors(conn), tolerant=True),
        node("register", lambda s: register.auto_approve(conn)),
        node("extract", extract_node, tolerant=True),
        node("authors", lambda s: extraction.backfill_arxiv_authors(conn)),
        node("observe", observe_node),
        node("mobility", lambda s: register.detect_mobility_events(conn)),
        node("cluster", lambda s: clustering.cluster_all(conn)),
        node("features", lambda s: featmod.compute_features(conn)),
        # free monitoring signal; informational, never gates the verdict
        node("drift", lambda s: {"major": drift.report(conn)}),
        node("score", score_node),
        # ---- paid audit + reading; reached only through the spend gate ----
        node("verify", verify_node),
        node("personas", lambda s: personas.build(conn)),
        # ---- delivery: AFTER repair/personas so nothing published is stale ----
        node("evaluate", lambda s: evaluation.build(conn), tolerant=True),
        node("positions", lambda s: positions.build(conn), tolerant=True),
        node("digest", digest_node),
        node("alerts", lambda s: alerts.run(conn, days=7)),
        node("digest_parity", lambda s: faithfulness.check_digests(conn)),
        node("faithfulness",
             lambda s: faithfulness.score_hypotheses(conn, LLM(conn))),
    ]
    for name, fn in linear:
        g.add_node(name, fn)
    g.add_node("checks", checks_node)
    g.add_node("approve", approve_node)

    g.add_edge(START, "ingest")
    order = [n for n, _ in linear]
    for a, b in zip(order, order[1:]):
        if b in ("verify", "evaluate", "faithfulness"):
            continue                     # conditional, wired below
        g.add_edge(a, b)
    # The spend gate: one human decision at `approve` (an interrupt unless
    # --yes), applied to BOTH paid segments. Without --spend or a key the
    # gate resolves to the free path without pausing, so a default run costs
    # what `pipeline` costs.
    g.add_edge("score", "approve")
    g.add_conditional_edges(
        "approve", lambda s: "verify" if s["approved"] else "evaluate",
        ["verify", "evaluate"])
    g.add_edge("personas", "evaluate")
    g.add_conditional_edges(
        "digest_parity",
        lambda s: "faithfulness" if s["approved"] else "checks",
        ["faithfulness", "checks"])
    g.add_edge("faithfulness", "checks")
    g.add_edge("checks", END)
    # A checkpointer is what makes interrupt() resumable; in-memory is enough
    # because the pause and the resume live in one CLI process.
    return g.compile(checkpointer=InMemorySaver())


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run the whole pipeline as one LangGraph graph.")
    ap.add_argument("--db", default=str(storage.DEFAULT_DB))
    ap.add_argument("--max-extract", type=int, default=60,
                    help="stage-2 docs per run (cost cap)")
    ap.add_argument("--spend", action="store_true",
                    help="offer the paid stages (verify+repair, personas, "
                         "faithfulness); the run pauses for approval unless "
                         "--yes. Default off: costs what `pipeline` costs.")
    ap.add_argument("--yes", action="store_true",
                    help="with --spend: skip the interactive approval pause "
                         "(for schedulers)")
    ap.add_argument("--mermaid", action="store_true",
                    help="print the graph topology and exit; runs nothing")
    args = ap.parse_args()

    conn = storage.connect(Path(args.db))
    storage.init_db(conn)
    graph = build(conn)
    if args.mermaid:
        print(graph.get_graph().draw_mermaid())
        return 0

    if tracing.setup():
        print("tracing: OpenInference spans -> Phoenix (FLI_TRACING on)")
    from langgraph.types import Command
    cfg = {"configurable": {"thread_id": "run"}}
    with tracing.chain_span("graph.run") as root:
        tracing.annotate(root, {tracing.INPUT_VALUE:
                                f"spend={args.spend} max_extract={args.max_extract}"})
        final = graph.invoke({"spend": args.spend, "auto_approve": args.yes,
                              "approved": False,
                              "max_extract": args.max_extract,
                              "report": {}, "verdict": 0}, cfg)
        while "__interrupt__" in final:      # paused at the approve gate
            payload = final["__interrupt__"][0].value
            print(f"\n=== approval required ===")
            for k, v in payload.items():
                print(f"  {k}: {v}")
            try:
                answer = input("approve spend? [y/N] ")
            except EOFError:                 # non-interactive without --yes
                answer = "n"
                print("no tty — declining (use --yes for schedulers)")
            final = graph.invoke(Command(resume=answer), cfg)
        tracing.annotate(root, {tracing.OUTPUT_VALUE: f"verdict={final['verdict']}"})
    print("\n=== run summary ===")
    for stage, summary in final["report"].items():
        print(f"  {stage}: {summary}")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    return final["verdict"]


if __name__ == "__main__":
    sys.exit(main())
