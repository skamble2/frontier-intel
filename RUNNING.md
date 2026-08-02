# Running the pipeline

## Repository layout

Packages mirror the four data layers, so the code map and the data model are the
same picture.

```
fli/core/           shared primitives: text, http, config, paths
fli/storage/        persistence (SQLite) - no domain logic
fli/ingestion/      LAYER 1  raw sources
fli/knowledge/      LAYER 2  filtering, extraction, register
fli/intelligence/   LAYER 3  clustering, features, labels, scoring
fli/ops/            LLM client, tracing (cross-cutting)
fli/validation/     C1-C20 invariant battery + drift monitoring (reads every layer)
fli/delivery/       LAYER 4  positions, personas, digest, alerts (nothing imports it)
fli/orchestration/  pipeline, graph, skeleton (composition only)
```

Import direction is enforced by `tests/test_architecture.py`, which fails the
build if a lower layer imports a higher one.

Every layer runs on its own — both of these work, and both accept the same flags:

```bash
python3 -m fli.cli checks           # unified dispatcher; `--help` lists all layers
python3 -m fli.validation.checks    # the layer directly
```

Everything deterministic — schema, ingestion replay, Stage-1 filter,
verification, scoring, rendering — runs with no credentials at all. Only
Stage-2 extraction and the other LLM stages need an API key, and only live
fetching needs network.

## One-time setup

```bash
git clone <this-repo> && cd frontier-intel

python3 -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env        # then fill in the keys you actually need
```

Every variable in `.env.example` is optional except `ANTHROPIC_API_KEY`, and
even that is only required for the stages that call a model. Without it the
deterministic pipeline still runs end to end against the committed database and
`checks` still exits green.

## Walking skeleton (one doc → one cited insight)

```bash
source .venv/bin/activate
python3 -m fli.cli skeleton
```

Expected output: ingested doc id, then an `=== INSIGHT ===` block with claim,
verbatim evidence quote, source URL, and the token/cost line from `llm_calls`.
The DB lands in `data/fli.db` — every table inspectable with `sqlite3 data/fli.db`.

## Register — labs, people, entity resolution

All deterministic; no API key needed. Every command is idempotent —
re-running never double-counts.

```bash
python3 -m fli.cli register seed     # 7 labs + seed people (fetch + verbatim-name gate)
python3 -m fli.cli register report   # register counts
```

The tracked universe (labs, their pages, seed people, X-handle candidates)
lives in `config/register_seeds.yml`, next to `register_overrides.yml` —
editing who is tracked is a config change, not a code change.

**Co-author expansion** lives in `fli/knowledge/expansion.py` (`python3 -m fli.cli expand`).
It anchors on RESEARCH seeds only (founders are tracked but are not
co-authorship anchors), so one CEO's broad institutional paper no longer swamps
the queue. Idempotent.

**Approval is automated and re-asserted every run:**

```bash
python3 -m fli.cli register auto_approve   # deterministic: corroborated + valid name
                                       # + top-K per lab slate, minus vetoes
python3 -m fli.cli register queue          # the per-lab slates (what auto_approve will take)
```

`auto_approve` runs inside `fli.orchestration.pipeline` too, so per-lab balance is re-asserted
daily, never by hand. `config/register_overrides.yml` (`approve:` / `reject:` by
canonical name) is read every run and always wins over the rule; a human can add
or veto a name at any time without blocking the pipeline. Manual CLI still works
and writes into that file, so decisions survive DB rebuilds:

```bash
python3 -m fli.cli register approve <id> [<id>]  # force-promote + record in overrides
python3 -m fli.cli register reject  <id> [<id>]  # veto + record in overrides
```

**Register balance** (candidates / approved / insights per lab) prints every run
(check C13) — the honest de-skew evidence, stated not assumed.

> Schema and discovery-method changes take effect on the next **truncate +
> rebuild** (`rm data/fli.db` → `register seed` → `pipeline`), which is the
> normal refresh flow. Rebuilding re-discovers candidates research-anchored.

**Schema changes:** `storage/schema.sql` is authoritative; the existing
`data/fli.db` is migrated by hand when it changes (no migration framework —
single-user project, one database).

## Ingestion pipeline

One command runs the whole daily cycle (ingest 370 sources across 5 types →
stage-1 filter → stage-2 extraction → re-observe affiliations → clustering →
features → per-rubric scoring → evaluation report → validation battery;
exit 0 = green):

```bash
python3 -m fli.cli pipeline                    # extraction capped at 60 docs/run
python3 -m fli.cli pipeline --max-extract 200  # raise the cost cap explicitly
```

Stage 2 needs `ANTHROPIC_API_KEY` (from `.env`); without it that stage is
skipped and everything deterministic still runs and stays green. Scoring
trains on the judge labels already in the DB — the paid stages (`judge`, `x`)
are never run by the pipeline and stay manual. Each run
prints the quote-verification rate, the event-type distribution, and
cumulative LLM cost.

Idempotent: re-runs hash-dedup everything already stored, never re-extract a
document that has an insight or a stage-2 verdict, only extract the latest
version of each URL, skip already-rejected docs, and observe at most one
affiliation per person/lab per day.

**Scheduled run (GitHub Actions).** `.github/workflows/pipeline.yml` runs the
pipeline daily at 06:00 UTC (or on demand via *Run workflow*) and, when the
validation battery is green, commits `data/fli.db`, the regenerated
`docs/evaluation-report.md`, `docs/figures/` and `docs/metrics-out.txt` back
to the repo — so the published numbers always match the committed DB. Set the
`ANTHROPIC_API_KEY` repository secret (*Settings → Secrets and variables →
Actions*) to enable stage-2 extraction; without it the scheduled run still
executes every deterministic stage. A red run commits nothing.

Individual stages, if needed: `python3 -m fli.cli ingest`, `python3 -m fli.cli filter`,
`python3 -m fli.cli register observe`.

## X (social) — the only paid source

Pay-per-use, billed **per resource returned** (rates read from
docs.x.com/x-api/getting-started/pricing on 2026-07-26):

| resource | cost |
|---|---|
| Posts: Read | $0.005 each |
| User: Read | $0.010 each |

Resources are **deduplicated within a 24h UTC window**, so re-running the same
day costs nothing for posts already seen. There is no subscription and no
minimum spend — which is why this source is affordable now and was not under
the old $200/month Basic tier.

**Add the token to `.env`** (same file as `ANTHROPIC_API_KEY`):

```
X_BEARER_TOKEN=AAAAAAAAAA...
```

Get it from [console.x.com](https://console.x.com) → your app → *Keys and
tokens* → Bearer Token. Also set a **spending limit** in the console; the caps
below are the second line of defence, not the first.

**Always dry-run first.** It prints the worst-case cost and spends nothing:

```bash
python3 -m fli.cli x --dry-run     # cost estimate only
python3 -m fli.cli x               # fetch, hard-capped
```

Spending controls live in `fli/core/config.py` and are checked *before* the
first request, so a pagination bug cannot drain the balance:

```
X_MAX_POSTS_PER_ACCOUNT = 20
X_MAX_POSTS_PER_RUN     = 400     # ceiling of $2.00 of posts per run
X_RUN_BUDGET_USD        = 3.00    # refuses to start if worst case exceeds this
```

Every run prints a cost ledger (`N posts x $0.005 + M users x $0.010 = $X`) and
writes the running spend into `fetch_log.detail`.

**Attribution rule.** Lab accounts (`@OpenAI`, `@AnthropicAI`, …) are
`channel='official'` with a `lab_id` — those are the lab speaking, so a
`source_inferred` attribution is legitimate. Researcher accounts come from
`identities` (`platform='x'`) and carry **no lab** and `channel='third_party'`:
a person tweeting is not their employer announcing, and check C12 would
otherwise let every personal post be attributed to the lab as if it were
official.

Researcher handles are not yet populated — `identities` has 0 rows with
`platform='x'`, so a run currently covers the 7 lab accounts only ($0.77 worst
case). Adding handles is what unlocks personnel-move coverage.

## Observability (optional)

Off by default. When on, every `LLM.call` emits an OpenInference span (prompt,
completion, model, token counts, tagged `fli.task` = classify|extract|persona)
to a local Phoenix — so a classifier verdict shows the exact input it judged.
This is dev tooling for the prompt-iteration loop only; `checks.py` stays the
source of truth for measured numbers.

Run Phoenix (the viewer) **isolated** — it is a heavy server and should not
share this project's environment, or it will upgrade shared libraries and break
other tools. Docker is the cleanest option, since it installs nothing into
Python:

```bash
docker run -p 6006:6006 arizephoenix/phoenix:latest   # UI + collector at :6006
```

Then, in this project's `.venv`, install the lightweight client only:

```bash
source .venv/bin/activate
pip install -r requirements-tracing.txt   # opentelemetry client libs, small
FLI_TRACING=1 python3 -m fli.cli pipeline      # spans stream to Phoenix at :6006
FLI_TRACING=1 python3 -m fli.cli graph         # + one CHAIN span per graph node
```

Under `graph`, each node is wrapped in a `chain_span` and the `llm_span` of any
call it makes nests inside it, so Phoenix renders a run as a single tree —
`graph.run → node.<stage> → llm.<task>` — giving per-node latency next to
per-call token counts.

Without the extras (or without `FLI_TRACING`), tracing is a no-op and the
deterministic pipeline is unaffected. If `FLI_TRACING` is set but the
OpenTelemetry client is missing, it prints the install hint and continues with
tracing off — an observability dependency must never break the run it observes.
Endpoint override: `PHOENIX_COLLECTOR_ENDPOINT`.
(If you prefer not to use Docker, run `pip install arize-phoenix && phoenix serve`
in a **separate** dedicated virtualenv — never this project's.)

## Scoring & validation

`python3 -m fli.cli pipeline` already runs all of the deterministic stages
below (cluster → features → score → evaluate → checks) on every run — the
individual commands remain for debugging one stage at a time. Only `judge`
(new pairwise labels, SPENDS) is a separate, deliberate step.

Order matters — clusters gate the corroboration feature, features gate training.

```bash
python3 -m fli.cli cluster                 # populate cluster_id (Jaccard, measured θ)
python3 -m fli.cli features                # build insight_features
python3 -m fli.cli judge --rubric investment --n 300   # SPENDS ~$6
python3 -m fli.cli judge --rubric technical  --n 300   # SPENDS ~$6
python3 -m fli.cli score --all-rubrics      # one bake-off per audience
python3 -m fli.cli evaluate                 # 12 figures + docs/evaluation-report.md
python3 -m fli.cli checks                   # expect C14-C17 green
sqlite3 data/fli.db < docs/metrics.sql > docs/metrics-out.txt
```

Read either ranking, and note they are genuinely different documents:

```bash
python3 -m fli.cli score --top 10 --rubric investment
python3 -m fli.cli score --top 10 --rubric technical
```

`judge --dry-run` prints the prompt and the projected spend without sending
anything. A second provider judges the identical pairs and lands as its own
labeler, which is what makes the reliability estimate meaningful:

```bash
python3 -m fli.cli judge --model gpt-5.2 --rubric investment --n 300
python3 -m fli.cli judge --agreement \
    llm:claude-sonnet-5/investment/r1 llm:gpt-5.2/investment/r1
```

Key constraints, all measured:

- **Lab identity is never a feature**; pairwise labels are lab-stratified; per-lab
  precision@10 is reported as a fairness check.
- A **hand-weighted sum is a baseline to beat**, never the shipped scorer: an
  arbitrary weighted sum is not a defensible ranking.
- **Rubrics are never pooled.** Labels carry `llm:<model>/<rubric>/r<version>`
  and each ranking trains only on its own audience's judgements. The two
  rankings share 0 of their top 10 (Kendall tau -0.06), which is the point.
- **Reliability needs two model families.** Dawid-Skene infers accuracy from
  disagreement, so the figure refuses to render unless at least two independent
  families judged the same rubric.
- The **contributor feature is a lab-level proxy** because person attribution
  resolves on 11 of 556 events; the ablation shows it contributes little, and
  that negative result is reported rather than hidden.
- No embeddings, no vector store, no second database — SQL plus scikit-learn
  over 556 rows.

## Delivery (what the reader actually receives)

Three of the four delivery stages are deterministic, free, and part of every
`pipeline` run. Only `personas` — the written reading — spends, so it stays a
deliberate step.

```bash
python3 -m fli.cli positions                 # event -> holding edges (free)
python3 -m fli.cli personas --k 10 --dry-run # prompt + projected spend, sends nothing
python3 -m fli.cli personas --k 10           # SPENDS ~$0.12; idempotent
python3 -m fli.cli digest --all --days 7     # docs/digests/<date>-<persona>.{md,pdf}
python3 -m fli.cli alerts --days 7 --dry-run # what would be pushed, recording nothing
python3 -m fli.cli alerts --days 7           # push + record, once per event
python3 -m fli.cli web                       # browse UI at http://127.0.0.1:5000 (register queue is clickable)
```

- **The digest and the persona layer select from the same slate.** Both call
  `scoring.top_events`, so a paid reading is always attached to something the
  reader is shown. They used to diverge — the persona layer read the raw
  ranking and the digest applied the editorial rules, so zero of the ten
  engineering readings appeared in the engineering digest.
- **Uncovered items are published as uncovered.** The digest names how many of
  its items carry no reading rather than dropping them, so the coverage gap is
  visible in the committed artifact.
- **The PDF has no dependency.** `fli/delivery/pdf.py` writes PDF 1.4 directly
  (~200 lines, Helvetica core metrics, clickable source links) and
  `tests/delivery/test_pdf.py` reads the output back with an independent parser.
- **Alerts do not fire on score.** The trigger is a signed direction — a
  classifier-established position edge, or a persona reading at medium-or-better
  confidence — bounded by the period. A top-decile rule would have missed the
  one event the system calls a threat to a holding, which scores below the
  median. The `alerts` table's UNIQUE key means an alert fires exactly once.

## Metrics harness (reproducible from the committed DB)

```bash
sqlite3 data/fli.db < docs/metrics.sql > docs/metrics-out.txt
```

Regression guards (G1–G5b) sit at the top of the output and answer "did the last
fix land?" against the previous run's numbers, inline. Snapshots live in
`data/snapshots/` — `fli-robustness-evidence.db` is the artifact behind the
ingestion-robustness claim (4 failure modes incl. HTTP 429), since a
truncate+rebuild resets `fetch_log` to all-ok.

## The whole run in one command

`pipeline` chains the free stages and leaves the paid ones as separate commands.
`graph` packages all twenty-one stages as a LangGraph graph behind one gate:

```bash
python3 -m fli.cli graph                # free stages only — costs what `pipeline` costs
python3 -m fli.cli graph --spend        # + verify/repair, personas, faithfulness
python3 -m fli.cli graph --mermaid      # print the topology and exit; runs nothing
python3 -m fli.cli graph --max-extract 30
```

The paid nodes require `--spend` **and** an API key; without both they are not
on the graph's path at all, so a default run cannot spend. Node bodies are the
same layer functions the CLI commands call — the graph owns ordering and gating
only. It exits on the checks battery's verdict, exactly as `pipeline` does.

`langgraph` is an optional dependency imported lazily, so everything else runs
without it (`pip install -r requirements.txt` includes it).

## Corpus drift (free, monitoring only)

```bash
python3 -m fli.cli drift                # last 14 days vs history
python3 -m fli.cli drift --days 30
```

PSI over categorical mixes (document `source_type`, insight `event_type`) and a
two-sample KS test over continuous ones (document length, insight score),
computed directly from SQL — no scipy, no model call, $0. The window is anchored
to the newest document rather than the wall clock, so the report is reproducible
on a static corpus.

Exit code is the number of MAJOR drifts, so a scheduler can alarm on it. It is
deliberately **not** part of `checks`: an organic news cycle must not turn the
release gate red.

## Pipeline-green gate (run after any change)

```bash
python3 -m fli.cli checks                    # DB invariants; exit 0 = green
python3 -m unittest discover -s tests -t .       # unit tests for the pure functions
```

Re-hashes every stored document, re-verifies every evidence row against the
stored bytes, and asserts all register invariants. Pure function of the DB —
no network, no LLM, no randomness.
