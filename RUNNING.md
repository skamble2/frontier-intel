# Running it

The committed database ships with real data, so everything below reproduces the
reported numbers with **no API key and no network**. Only the LLM stages need a
key; without one they skip and the rest still runs green.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Or skip the venv entirely:

```bash
docker build -t frontier-intel .
docker run --rm frontier-intel                        # runs `checks`
```

Optional, all read from `.env`:

| var | unlocks | without it |
|---|---|---|
| `ANTHROPIC_API_KEY` | classify · extract · repair · judge · persona · verify | those stages skip; `checks` stays green |
| `OPENAI_API_KEY` | the second judge family | Dawid–Skene has one family and refuses to render |
| `GITHUB_TOKEN` | 5,000 req/hr instead of 60 | the resolver self-caps under the anonymous limit |
| `X_BEARER_TOKEN` | the paid X source | skipped; every other source is free |
| `SLACK_WEBHOOK_URL` | `alerts --sink slack` | the default stdout sink needs nothing |
| `FLI_TRACING=1` | OpenInference spans → Phoenix | no-op |

## The six commands worth knowing

```bash
python3 -m fli.cli checks      # C1-C20 invariant battery over the DB; exit 0 = green
python3 -m fli.cli graph       # the whole run, free stages only (--spend adds the paid ones)
python3 -m fli.cli evaluate    # regenerate all 17 figures + docs/evaluation-report.md ($0)
python3 -m fli.cli digest      # the delivered report, Markdown + PDF, per persona
python3 -m fli.cli web         # browse the register, scores and past reports
python3 -m fli.cli mcp         # same intelligence as read-only MCP tools, for an agent
```

**Start with `checks` then `evaluate`** — between them they re-verify every quote
against the stored bytes and rebuild every number in the evaluation report from
the committed database. Nothing is hand-copied and nothing costs anything.

`graph` runs the twenty-two stages in order. By default it stops short of the
paid ones; `--spend` offers them and pauses for approval with the work sized
first. `--mermaid` prints the topology without running anything.

## Everything else

One entry point, one command per layer, and every layer runs standalone because
they communicate only through the database:

```bash
python3 -m fli.cli --help          # the full list
python3 -m fli.cli <command> --help
```

| layer | commands |
|---|---|
| L1 ingest | `ingest` · `x` (paid; `--dry-run` first) |
| L2 knowledge | `filter` · `extract` · `register` · `expand` |
| L3 intelligence | `cluster` · `features` · `label` · `judge` · `channels` · `score` · `contributors` |
| L4 delivery | `positions` · `personas` · `digest` · `alerts` · `mcp` · `web` |
| validation | `checks` · `verify` · `faithfulness` · `drift` · `xbench` · `evaluate` |
| orchestration | `pipeline` · `graph` · `skeleton` |

Two conventions worth knowing: anything that spends money previews the cost and
refuses to start a run it cannot afford, and `--dry-run` exists wherever
spending does.

## Gate to run after a change

```bash
python3 -m fli.cli checks
python3 -m unittest discover -s tests -t .
```

`checks` is a pure function of the database — no network, no LLM, no
randomness. It re-hashes every stored document, re-verifies every evidence row
against the stored bytes, and asserts the register invariants.

## Layout

Packages mirror the data layers, so the code map and the data model are the same
picture. Import direction is enforced at build time: a lower layer importing a
higher one fails the suite.

```
fli/core/           text, http, config, paths, policy, rubrics
fli/storage/        SQLite persistence — no domain logic
fli/ingestion/      L1  raw sources
fli/knowledge/      L2  filtering, extraction, register
fli/intelligence/   L3  clustering, features, labels, scoring
fli/delivery/       L4  positions, personas, digest, alerts, mcp
fli/ops/            LLM client, tracing (cross-cutting)
fli/validation/     C1-C20 battery + drift monitoring
fli/orchestration/  pipeline, graph, skeleton — composition only
```

## Observability (optional)

Uncomment the tracing block in `requirements.txt`, then:

```bash
docker run -p 6006:6006 arizephoenix/phoenix:latest   # viewer, isolated
FLI_TRACING=1 python3 -m fli.cli graph                # spans stream to :6006
```

Each graph node is a span and every LLM call nests inside it, so a run renders
as one tree: `graph.run → node.<stage> → llm.<task>`. Endpoint override:
`PHOENIX_COLLECTOR_ENDPOINT`. Run the Phoenix viewer isolated — it is a heavy
server and will upgrade shared libraries if installed into this environment.

## Metrics harness

```bash
sqlite3 data/fli.db < docs/metrics.sql > docs/metrics-out.txt
```

Regression guards (G1–G5b) sit at the top of the output and answer "did the last
fix land?" against the previous run's numbers.

`data/fli.db` is the only database in the repo. Where evidence could not survive
in it — a truncate+rebuild resets `fetch_log` to all-ok, erasing the failure
history — it is exported as text instead:
[docs/ingestion-robustness-evidence.txt](docs/ingestion-robustness-evidence.txt)
carries the four ingestion failure modes with counts and URLs.
