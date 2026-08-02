# Architecture

Frontier Lab Intelligence tracks eight frontier AI labs and the people inside
them, turns their public output into evidence-backed events, ranks those events
for two different readers, and delivers a cited report and an alert path for
each. Every claim of behaviour below is enforced somewhere by a test or an
invariant check, and the relevant name is given.

## The one idea the whole system is organised around

Every downstream number traces back to a byte a lab published. There is no step
where the system asserts something the source does not say. That single
constraint — call it *evidence-first* — is what makes an intelligence product
about frontier labs trustworthy rather than a plausible-sounding summary, and it
drives almost every structural decision below.

Concretely: an insight cannot exist without an `evidence_id`, an evidence row
cannot exist without a verbatim quote, and every run re-hashes each stored
document and re-checks that each quote is still a byte-for-byte substring of its
source (checks C1 and C2). If a page changed under us, the quote stops
verifying and the row is surfaced, not silently kept. A direction shown to a
portfolio manager is only ever stated from a mechanism a classifier established,
never from a keyword the text happened to contain. The system's default answer,
everywhere, is "the evidence does not establish that" — and it says so out loud
rather than guessing.

## Layered design, with the database as the interface

The code is organised into five layers, and the dependency arrows only ever
point downward. A test (`tests/test_architecture.py`) walks the import graph
with the AST and fails the build if any module imports a layer above it:

```
core / ops        0   text, http, config, paths, the LLM client
storage           1   SQLite persistence — no domain logic
ingestion         2   raw sources: feeds, the paid X source
knowledge         2   filtering, extraction, the researcher register
intelligence      3   clustering, features, judge labels, scoring
validation        3   the C1–C20 invariant battery (reads every layer)
delivery          4   positions, personas, digest, alerts
orchestration     4   the pipeline and the graph (composition only)
```

Layers do not call each other's functions in memory. They communicate through
the database: extraction writes `insights`, scoring reads `insights` and writes
`event_scores`, delivery reads both. This is deliberate. It means every stage is
independently runnable and independently inspectable — `python3 -m fli.cli
score` operates on whatever is in the DB regardless of how it got there — and it
means the DB schema *is* the contract between stages. A stage cannot smuggle a
side channel past its neighbour, because the only thing its neighbour reads is a
table.

The delivery layer sits at the top and nothing imports it. That is enforced,
not conventional: a change to how intelligence is *presented* must never be able
to change what was extracted or how it scored. The architecture test asserts
this edge specifically.

## Layer by layer

### Ingestion (layer 2)

Feeds are polled from official lab channels — blogs, newsrooms, arXiv listings,
GitHub release feeds. Every fetch is logged to `fetch_log` with its outcome
(`ok`, `empty`, `error`, `rate_limited`), so coverage is a queryable fact and a
source that has never been fetched fails a check (C7) rather than quietly
producing nothing. Re-fetched pages whose bytes changed become new immutable
rows keyed by content hash; a `latest_documents` view answers "which version is
current" without ever mutating history. The one paid source (the X API) is
gated behind a `--dry-run` cost estimate and a hard cap.

**Ingestion depth, and why it is a filtering problem.** An early audit of
`low_substance` rejections found the classifier was mostly judging *non-article
input* rather than genuine noise, from two distinct causes. Several blog RSS
feeds carry only a 250–430-character teaser, so the stored document was a
marketing tagline — 31 of 40 rejections at the time traced to four such thin
feeds. Separately, newsroom pages stored the full 135–290k of raw HTML, but
stage 2 capped its input at the first 6,000 characters *before* extracting text,
so the model read `<head>` and nav boilerplate instead of the article. Both
produced false rejections of substantive articles that were never actually shown
to the model.

The fix keeps the evidence invariant intact: HTML→text extraction runs on stored
pages *before* stage 2, and quotes verify against that same cleaned text, so raw
HTML stays immutable and text extraction is a pure function applied at read
time. For teaser-only feeds the linked article body is fetched and stored as a
new immutable version rather than the teaser being trusted.

**The residual boundary is triaged rather than asserted.** After those fixes the
remaining `low_substance` rejections at that audit were characterised one by
one:

| bucket | n | verdict |
|---|---:|---|
| JS-rendered (OpenAI blog, Mistral newsroom) | 19 | hard wall — static fetch returns an empty client-side shell |
| GitHub release notes | 13 | legitimate noise floor (version bumps) — correct rejection |
| other blog / newsroom | 9 | genuine thin marketing — correct rejection |

So roughly half the suppressed items are the noise floor working as designed and
half are one documented limitation. Three decisions follow, and the ones *not*
taken matter most:

- **No "trust the headline."** Admitting an insight from a 243-character teaser
  because the headline names a product would rest a claim on evidence that only
  asserts it, breaking the verbatim-quote invariant that the whole hallucination
  story depends on. Coverage is not traded for the evidence guarantee.
- **No headless browser.** A ~300 MB dependency for two sites would damage the
  runnable demo for a small coverage gain. Playwright behind a flag is the
  scoped next step, and `fetch_log` already names exactly which sources need
  it — an evidence-driven extension rather than infrastructure added on spec.
- **The one high-value event behind the wall was captured manually** and stored
  under the same immutability rules, so it flows through the normal
  filter → extract → verify path and is fully cited rather than being a teaser
  exception.

**Four failure modes, all handled, none fatal.** Rate limits and source
messiness are recorded rather than described. Measured over one run of 487
fetch attempts — 458 ok, 20 error, 9 empty, a 6.0% failure rate:

| failure mode | where | n | how the system behaved |
|---|---|---:|---|
| read timeout | arXiv API queries | 9 | logged `error`, run continued, other sources unaffected |
| blocked / bad request | `ai.meta.com/research/` → HTTP 400 | 6 | logged `error`; a substitute channel was configured |
| empty feed (valid response, zero items) | `QwenLM/Qwen3/releases.atom` | 5 | logged `empty` with `items_found=0` — absence recorded, not a silent gap |
| HTTP 429 rate limit | arXiv API queries | 5 | logged `error`, run continued |

Every fetch attempt is a row, including the ones that returned nothing, so an
empty feed and a failed feed are *different* recorded states and a source going
quiet is visible rather than indistinguishable from "no news". Rate limits are
handled by politeness — a fixed inter-request delay on the arXiv API — rather
than a retry storm.

Those counts come from an earlier run, and deliberately so: a truncate-and-
rebuild resets `fetch_log`, so a corpus rebuilt since then cannot show the
failures that shaped these design decisions. The live database carries its own
current tally (771 ok / 164 error / 20 empty), dominated by a transient
`export.arxiv.org` certificate failure and by `x.ai` returning 403 to both a
direct fetch and the rendering proxy — the latter being why xAI has almost no
coverage in the corpus.

### Knowledge (layer 2)

**Filtering** is a two-stage funnel. A cheap Haiku classifier decides whether a
document is substantive at all (killing roughly a quarter of the corpus —
marketing, event promotion, job posts) before the expensive Sonnet extraction
ever sees it. The classifier's prefix is shared with the extractor so the
6k-token read is not paid twice.

**Extraction** turns a document into one or more events, each a `claim` (the
model's one-sentence paraphrase) plus a `quote` (the source's own contiguous
words, copied character-for-character, that must fully support the claim). The
prompt explicitly forbids padding the list to the maximum and inventing events
the text does not support. Every extracted quote is verified against the stored
document before it is allowed to persist; a quote that does not match is a
rejection, logged, not a stored row.

**The register** is the people-tracking half. It resolves a researcher across
three platforms — an arXiv author string, an X handle, a GitHub login — into one
person, and it only makes a link it can evidence. Co-author expansion discovers
new candidates from papers written with known seeds; each candidate is admitted
only through a tiered rule (a verbatim self-link beats a name match beats
nothing), and every admission carries the evidence row it rests on. The GitHub
resolver closes the loop the release feeds could not: `releases.atom` has no
author field, so contributor and org-membership endpoints are mined instead,
and a login that carries no person's name is rejected rather than entered as a
"person" the register cannot actually name.

### Intelligence (layer 3)

**Clustering** groups near-duplicate claims (Jaccard over claim tokens at a
measured threshold) so that one announcement echoed across nine posts counts
once, with the rest recorded as corroboration rather than as nine separate
events.

**Features** are the model's training surface: one numeric row per
(event, feature) in `insight_features`. The reader-facing `score_components`
JSON is a separate, human-readable decomposition — a model never parses JSON, it
reads the numeric rows, and the two are kept apart on purpose.

**The judge** is where ground truth would normally be, and it is the part that
required the most care, so it has its own section below.

**Scoring** runs a bake-off: several contenders (two baselines, a
hand-weighted sum, a gradient-boosted model, and a logistic model) are trained
on the judge's pairwise labels and compared on held-out pairs, and the winner is
shipped. A hand-weighted sum is always included as a baseline to beat, never as
the shipped scorer, because an arbitrary weighted sum is not a defensible
ranking. Lab identity is never a feature — the model must not be able to learn
that a particular lab is inherently more interesting — and per-lab precision is
reported as a fairness check.

### Validation (layer 3)

A battery of twenty invariant checks (C1–C20) reads every layer and asserts
the properties the rest of the system depends on: quotes re-verify (C1/C2),
every person is evidenced (C3) and passes name hygiene (C9), affiliations are
dated and evidenced (C5), every source was fetched (C7), no evidence is orphaned
(C10), scores cite the current policy version and every event type is ranked
(C17), published dates are the page's own rather than a sitemap timestamp (C18),
every insight quote verifies at the strictest `exact` tier (C19), and one name
resolves to one person (C20).

Alongside the battery, and deliberately outside it, sits **corpus drift**
(`drift.py`): PSI and KS between a recent window and the corpus history. The
battery answers "is the database internally consistent?"; drift answers "has the
world moved out from under the models fitted to it?" — a different question with
a different consequence, so it never gates the build. Both are covered in their
own sections below.
The battery is a pure function of the database, so a green run is a strong
statement: the committed DB is internally consistent, end to end.

### Delivery (layer 4)

The last mile takes one shared, audience-neutral corpus and produces two
tailored outputs. `positions` draws event→holding edges; `personas` writes the
per-audience reading; `digest` assembles the periodic report; `alerts` is the
push path. All four are covered below under "Delivery".

## Scoring without ground truth

There is no labelled dataset of "important frontier-lab events". Building the
system honestly meant confronting that rather than inventing a proxy, and the
design has three parts.

**A rubric, not a gut feeling.** Importance is defined in a versioned YAML file
(`config/rubrics/*.yml`), as an ordered list of tie-breaking rules. The judge
applies that rubric to a *pair* of events and says which ranks higher and which
rule decided it. Pairwise comparison sidesteps the impossible task of scoring an
event in isolation; a rule number makes every verdict auditable; and forcing a
binary choice with an honest confidence — where "low" means "I effectively
guessed, exclude this from training" — keeps coin-flips out of the training set
rather than letting them masquerade as signal.

**The lab name is withheld from the judge.** The rubric bans lab identity as a
reason, per-lab precision is the fairness metric, and presentation order is
randomised per pair and un-swapped on store. A judge primed by lab prestige
would invalidate all three, so it sees only claim, type, date and the verified
quote.

**Reliability is measured, not assumed.** The judge is not ground truth, so its
accuracy is *inferred from disagreement* with other labelers using Dawid–Skene —
which requires at least two independent model *families*, not two prompt
variants of one model (three variants of one model agreed 92–100% and the
estimator rated them all ~0.99, an artifact the system now refuses to produce).
A second family (a different vendor's model) and a human audit provide the
disagreement the estimate needs. That audit produced the single most important
finding in the project, recorded in the final report: two models sharing a prior
agree with each other and can be confidently wrong together.

## Two audiences, two rankings

The same corpus, the same features and the same clustering feed two rubrics: an
investment reader asking "what does this mean for our positions?" and an
engineering reader asking "what should we adopt or investigate?". Each rubric
trains its own model on its own labels; labels are tagged
`llm:<model>/<rubric>/r<version>` and never pooled, so a methodology change
lands as a new labeler rather than mixing into the old one.

This is not decoration, and the system measures whether it is real. The two
rankings share **0 of their top 10** (8% of the top 25) and correlate at a
Kendall τ of **+0.128** — near zero, i.e. close to unrelated orderings.
Same events, same features — only the definition of "important" differs, and it
differs enough that one ranking demonstrably cannot serve both readers. The
investment top 10 is personnel, commercial and infrastructure — an acquihire,
datacenter buildouts, token pricing, enterprise adoption. The engineering top
10 opens with open-weight releases and fills with shippable tooling. Neither
list would serve the other reader at all.

## Connecting private labs to public equities

Most frontier labs are private, so "important lab event" and "actionable for a
public-equity investor" are different questions, and the system keeps them
apart. `positions.py` answers two independent sub-questions:

- **Exposure** — does this text touch something the fund owns? Topical, and
  keyword matching is genuinely good at it.
- **Mechanism** — through which transmission channel does it reach the holding?
  Semantic, and keywords are bad at it, which is why the channel comes from an
  LLM classifier rather than a lexicon (measured against a 100-post
  human-audited reference: keyword F1 0.267 vs classifier F1 0.444, and the
  lexicon's failures are confident ones — it once returned a phone codec that
  "increased power usage" as a datacenter signal).

The channel column is nullable and that is the point: "exposure found, mechanism
not established" is a real and common state, and forcing a channel would
manufacture a causal story the evidence does not support. A *direction* (threat
or tailwind) is only ever stated from a classifier-established mechanism that
carries an inherent sign — and most channels do not. "Building a 10 MW
datacenter" and "saving 3.28 megawatts" are the same channel pointing opposite
ways; reading which way is a judgement about the sentence, so it is deferred to
the persona layer, which reads the claim. On the live corpus, 52 of 54 exposure
edges are `unclear` by design.

## Delivery

`personas` is the one delivery stage that spends: it asks a model, per audience,
what an event *means and what to do about it*, constrained to the same evidence
a reader can check and told it is a failure to rest a reading on anything the
quote does not say. It is not given the lab name, for the same reason the judge
is not. The parser rejects a reading that omits its working (`reasoning` is
`NOT NULL` because the reader is shown it verbatim) or that answers the wrong
audience's question (an `adopt` verdict on the investment persona means the
model answered the engineering question).

`digest` is deliberately the dumbest module in the repo: it computes nothing and
selects nothing on its own. It asks scoring for the same slate the persona layer
renders against — the two used to diverge, and every paid reading described an
event the reader would never see — joins whatever readings exist, and lays the
result out. One content model feeds two renderers (Markdown and a
dependency-free PDF), so the exported PDF cannot drift from the Markdown a
reader sees in the repo. Items without a reading are published *as* uncovered
rather than dropped, so the coverage gap is visible in the committed artifact.

`alerts` is the push path, and its trigger is deliberately not the score. The
one event the system calls a threat to two holdings ranks 22nd of 954 under the
shipped model — but 14th under logistic, 37th under hand-weights and 506th under
the recency baseline. A rank-threshold rule would therefore fire or not fire
depending on which model won a bake-off, which is no basis for waking a PM. So
an alert fires on a *signed direction* (a classifier-established position edge,
or a persona reading at medium-or-better confidence), bounded by the reporting
period, and the `alerts` table's UNIQUE key means each fires exactly once. A
channel that repeats itself every run trains its reader to ignore it, so
once-only is enforced in the schema, not left to the caller.

`mcp` is the fourth surface, and the only one built for a non-human reader.
`fli/delivery/mcp_server.py` exposes four **strictly read-only** tools over
stdio — `top_insights` (the slate), `search_insights` (substring over claims and
their verbatim quotes), `corpus_drift`, and `get_latest_digest` — so a Claude
Desktop or IDE agent can query the corpus directly. No tool writes to the
database, spends a token, or touches the network.

Two design choices keep it from becoming a second implementation. Every tool
body is a plain function taking a connection, so the whole surface is testable
without the MCP SDK installed and the SDK import happens only inside
`build_server()`. And each one delegates to the layer function that already owns
the logic — `top_insights` calls `top_events`, which is the same call the digest
and web UI make, so an agent cannot get a slate that differs from the one a
human reads. The wire format is deliberately narrower than the internal row:
`score_components` and `cluster_id` are ranking internals and stay out of it,
while `url` and `quote` stay in, because an agent that cannot cite is worse than
useless.

One asymmetry is intentional. `top_insights` is slate-filtered (deduped,
entailment-checked, mechanism-gated) but `search_insights` is not — it returns
raw corpus matches, so an agent can deliberately go looking for what the slate
*suppressed*. A read surface that can only show the filtered view cannot be used
to audit the filter.

## The stack, and model selection per task

The stack is deliberately small: **Python 3, SQLite, scikit-learn, the Anthropic
and OpenAI SDKs, Pydantic for typed model I/O, LangGraph for run packaging,
Flask for the web surface, the MCP SDK for the agent surface,
matplotlib/seaborn for figures**, with optional OpenInference/Phoenix tracing.
There is no vector store, no embeddings and no second database. Scoring is SQL
plus scikit-learn over a few hundred rows; a vector store would be
infrastructure carrying no measurement. SQLite is the right size for a
single-writer daily pipeline, and it makes the whole corpus a single file you
can open.

Every dependency past the first four is **optional and lazily imported** —
LangGraph, Flask, the MCP SDK, matplotlib and the tracing stack each live behind
a function-local import, so the daily pipeline runs on a machine with none of
them installed.

Model routing is one dictionary — `MODEL_FOR_TASK` in `fli/ops/llm.py` — so the
cost-quality trade-off for the whole system is legible in eight lines.

| task | model | why |
|---|---|---|
| `classify` | Haiku 4.5 | Binary substantive/not over a 6k-char prefix. A cheap gate in front of an expensive step; a wrong answer costs one skipped document, not a wrong claim. |
| `extract` | Sonnet 5 | The one step that must not be wrong — it emits the claim *and* the verbatim quote that has to re-match the source. Quote fidelity is where cheap models fail. |
| `repair` | Sonnet 5 | Rewrites a claim down to what its quote supports. Same fidelity requirement. |
| `judge` | Sonnet 5 **+ GPT-5.2** | Pairwise ranking under a rubric. Two independent *families*, not two prompts of one model — Dawid–Skene needs conditionally independent labelers. |
| `persona` | Sonnet 5 | The reader-facing "what this means / what to do". Judgment and tone; 92 calls, so price is irrelevant. |
| `channel` | Haiku 4.5 | Closed-set pick from five transmission channels. |
| `verify` | Haiku 4.5 | Claim↔quote entailment, three-way verdict. Closed set. |
| `faithfulness` | Haiku 4.5 | Same shape, over persona notes. |

The rule underneath: **Sonnet where a wrong answer enters the database as a
fact; Haiku where a wrong answer only costs a re-check.** Every closed-set
classification runs on Haiku; every open-ended generation that produces a stored
claim runs on Sonnet.

One measured wrinkle, recorded because it contradicts the routing: on the judge
task the *cheaper* model is at least as good. GPT-5.2 costs 6.5× less per usable
label than Sonnet (\$0.0028 vs \$0.0182), returns fewer low-confidence verdicts
(22.0% vs 27.5%), and its Dawid–Skene reliability is marginally *higher* (0.874
vs 0.864). The next tranche of labels should be bought from it. Full working in
[tokenomics.md](tokenomics.md).

## The run as a graph

`python -m fli.cli pipeline` chains the free stages and leaves the paid ones
(`verify --repair`, `personas`, `faithfulness`) as manual CLI steps.
`fli/orchestration/graph.py` packages **all twenty-two stages** as a LangGraph
`StateGraph` behind one entry point, with one explicit gate:

```bash
python -m fli.cli graph                 # free stages only — costs what `pipeline` costs
python -m fli.cli graph --spend         # offer the paid stages; pauses for approval
python -m fli.cli graph --spend --yes   # skip the pause (schedulers)
python -m fli.cli graph --mermaid       # print the topology, run nothing
```

Two properties make this packaging rather than a second implementation:

**No node has a body of its own.** Every node is one call into the layer
function the corresponding CLI command already invokes, so there is no parallel
code path to drift out of sync. The graph owns *ordering and gating*, nothing
else.

**The paid stages need three things, and the third is a human.** `--spend`
states intent at launch and `_spend_ready` also requires an API key, but neither
is the gate. A dedicated `approve` node sits between `score` and the paid
segment, and when spend is possible it raises a LangGraph `interrupt` that
**pauses the run** and prints what the paid work would actually touch:

```
=== approval required ===
  question: run the paid stages (verify+repair, personas, faithfulness)?
  unaudited_claims: 0
  existing_notes: 65
```

That sizing comes from `spend_estimate`, which counts insights with no row in
`claim_checks`. The point is that approval is an informed decision made against
the current state of the database, not a flag someone set hours earlier — and on
the committed corpus it correctly reports **0 unaudited claims**, i.e. there is
nothing for the paid audit to do.

One decision applies to *both* paid segments: `approved` is written once into
state and read by the conditional edges at `approve` and at `digest_parity`, so
an operator cannot approve the repair pass and then be asked again about
faithfulness. Declining is not an error — the run continues down the free path
exactly as if `--spend` had been absent, and `checks` still decides the exit
code.

Non-interactive callers are handled explicitly rather than left to hang:
`--yes` skips the pause for schedulers, and an `EOFError` on a missing tty is
caught and treated as a decline, printing the `--yes` hint. Compiling with an
`InMemorySaver` checkpointer is what makes `interrupt` resumable at all;
in-memory is sufficient because the pause and the resume live in the same CLI
process.

Without `--spend` or without a key the gate resolves to the free path **without
pausing**, so an unattended default run never blocks. Five tests pin the
behaviour: a default run skips every paid stage; `--spend` without a key still
skips them; `--spend` pauses at the interrupt and a decline stays on the free
path; `--yes` skips the pause; and a spend run orders repair and persona notes
*before* delivery.

That last ordering is a real fix, not a preference. Running delivery before
claim repair produced digests citing claims that repair had already rewritten —
**2 stale claims in `docs/digests/2026-07-30-ai_team.md`**. The graph makes the
correct order structural instead of a thing the operator has to remember.

State is a `TypedDict` of `spend`, `max_extract`, a merge-annotated `report`
dict and the checks `verdict`; the DB connection is deliberately *not* state but
bound into the nodes by closure, because state should stay printable and the
database is the actual shared medium between stages anyway. The graph's exit
code is the checks battery's verdict, so it is still the release gate.

**Tracing.** With `FLI_TRACING=1`, `tracing.chain_span` wraps each node and the
existing `llm_span` nests inside it via OTel context, so Phoenix renders one run
as `graph.run → node.<stage> → llm.<task>` — per-node latency and per-call token
counts in one tree. Tracing is off by default and no-ops entirely when the
OpenTelemetry packages are absent (a commented-out block in `requirements.txt`, kept opt-in
from `requirements.txt` for that reason).

## Corpus drift monitoring

The stage-1 filter, the scoring bake-off and the judge labels were all fitted
against a corpus with a particular shape. When that shape moves, those fits
degrade quietly — no invariant breaks, nothing turns red, the rankings just get
worse. `fli/validation/drift.py` measures the movement:

- **PSI** (population stability index) over categorical mixes — document
  `source_type`, insight `event_type`
- **KS** (two-sample Kolmogorov–Smirnov) over continuous distributions —
  document length, insight score

Both are computed directly, with no scipy dependency, and both are unit-tested
against hand-computed values. PSI uses the conventional 0.10 / 0.25 banking
bands rather than house-tuned thresholds, so the numbers are comparable to the
literature; KS uses the α = 0.05 critical value. Empty bins are smoothed to
1e-4 rather than dropped, because *the appearance of a new category is itself
drift* and must register instead of dividing by zero.

Two design decisions worth stating:

**The window is anchored to the newest document, not the wall clock**, so the
report is reproducible on a static corpus — running it next month against an
unchanged database gives the same numbers.

**Drift is deliberately not part of `checks`.** An organic news cycle must not
turn the release gate red. It is a monitoring signal with its own exit code (the
count of MAJOR drifts) so a scheduler can still alarm on it, and it runs as a
free node inside the graph, informational only. Making drift an invariant would
mean the build fails because the world changed, which is not a defect.

On the committed corpus it reports **4 MAJOR of 4 metrics**, and what it caught
is discussed in [final-report.md](final-report.md).

## Fallback strategies

Every external dependency has a defined degradation path, and each one fails
toward *less output*, never toward unverified output.

**No API key → the system still runs, and still passes.** `have_api_key()` is
checked at each paid entry point (`fli/orchestration/pipeline.py`,
`fli/knowledge/extraction.py`, `fli/knowledge/channels.py`,
`fli/validation/faithfulness.py`, `fli/validation/entailment.py`). Without one,
ingestion, filtering, the register, clustering, features, scoring, the check
battery, the digest and the web UI all run normally on the committed corpus —
only new LLM extraction is skipped. With no key at all you still get a green
`checks` run and a readable digest.

**Unknown model price → refuse, do not guess.** `cost_usd()` raises `KeyError`
for a model absent from `PRICES`. A silent default of zero would make an
expensive model look free in the very table used to make routing decisions, so
the system declines to price what it has not been told the price of.
`preflight` checks the key *and* the price before any paid run starts.

**HTTP failure → bounded retry, then an open circuit.** `fli/core/http.py`
retries twice with a 1-second backoff on a 20-second timeout, and counts
consecutive failures per host. After three, the circuit opens for that host and
the remaining fetches skip it rather than spending the run's budget on a dead
endpoint. Every outcome is written to `fetch_log` (`ok` / `empty` / `error` /
`rate_limited`), so a degraded source is a queryable fact — the committed log
carries 733 `ok`, 148 `error` and 16 `empty`, and check C7 fails if a
registered source has never been fetched at all.

**JavaScript-walled pages → a rendering proxy, then manual capture.** When a
feed entry's own HTML yields less text than a rendering proxy does
(`http_get_rendered`, `fli/ingestion/feeds.py`), the rendered text wins. Pages
that defeat both are captured manually and stored under the *same* immutability
and verification rules — the evidence invariant is never weakened to accommodate
a hard source.

**Missing GBM library → a three-step ladder.** `_fit_gbm` tries LightGBM, then
XGBoost, then falls back to scikit-learn's `HistGradientBoostingClassifier`,
which is already a hard dependency. The bake-off therefore always has a
gradient-boosted contender, on any install. The shipped winner names which one
ran (`gbm_sklearn` in the committed results), so the fallback is visible in the
output rather than hidden.

**Missing plotting, web, graph, agent or tracing libraries → the pipeline is
unaffected.** matplotlib/seaborn/pandas are imported only inside
`fli/validation/evaluation.py`, Flask only inside `fli/web/app.py`, LangGraph
only inside `build()` in `fli/orchestration/graph.py`, the MCP SDK only inside
`build_server()` in `fli/delivery/mcp_server.py`, and the OpenTelemetry stack
only inside `tracing.setup()` — all lazily. `python -m fli.cli pipeline` runs
with none of them installed; only the corresponding command is unavailable. The
MCP tool *bodies* are plain functions taking a connection, so the agent
surface's behaviour stays under test even where the SDK is absent. Tracing goes further and degrades rather than fails: with
`FLI_TRACING` set but OpenTelemetry absent it prints the install hint and
continues with tracing off, because an observability dependency must never be
able to break the run it observes.

**Provider outage → the second family.** The OpenAI SDK is an optional
dependency imported lazily, and only when a task is routed to an OpenAI model.
It exists for Dawid–Skene's independence requirement, but it doubles as the
fallback path if one vendor is unavailable.

**Parse failure → a rejection row, not a guess.** `validate_json` strips code
fences and recovers a JSON object from surrounding prose before giving up; a
reply that still will not parse is written to `rejections`
(`classify_parse_error`, 10 rows in the committed corpus) rather than being
retried until it says something usable. A reply that omits its working, or
answers the wrong audience's question, is rejected by the parser for the same
reason.

## What runs automatically, and what costs money

The daily pipeline runs every deterministic, free stage — ingest, filter,
register, cluster, features, drift, score, evaluate, positions, digest, alerts —
and exits on the validation battery's verdict. `judge` (new pairwise labels) and
`x` (the paid source) stay manual and explicit under both runners. The
difference between the two entry points is what happens to the remaining paid
stages: `pipeline` leaves `verify --repair`, `personas` and `faithfulness` as
separate commands, while `graph --spend` puts them on the path behind a single
gate that requires both the flag and an API key. **Default `graph` costs exactly
what `pipeline` costs** — the paid nodes are not merely skipped at runtime, they
are not on the graph's path at all.

Each paid entry point previews its projected spend before sending, checks
the API key and the price table, and refuses to start a run it cannot afford.
Cost is logged per call to `llm_calls`; the whole system to date has spent
**$29.87 across 8,707 calls**. Extraction — the part that produces what a reader
sees — is 20.5% of that; judging and labeling, which exist only to validate the
ranking, are 63.8%. The full breakdown, the unit economics and the three places
cost changed a design decision are in [tokenomics.md](tokenomics.md); the raw
per-task split is `docs/metrics-out.txt`, section M5a.

## Data discipline

The committed database is the clean production state, and it is the *only*
database in the repository. Validation experiments — the second-model judge, the
human audit, the bake-off comparison of non-winning contenders — have their
*results* written into the evaluation report and the final report rather than
accumulating as extra rows in the live DB.

Where an experiment's evidence could not survive in the live database, the
finding is written into prose and the working binary is discarded. The
ingestion-robustness history above is the worked example: a truncate-and-rebuild
resets `fetch_log` to all-ok, so the failure counts were lifted out of a 34 MB
working snapshot into the table in "Ingestion" and the snapshot deleted. A
committed claim should rest on something readable in a diff, not on a binary
that has to be sent separately.

A run checkpoints the write-ahead log back into the main file before committing,
so a committed DB never silently misses the writes of the run that produced it.

---

## Design notes, by module

One entry per module: the non-obvious decision behind it, and the measurement or
failure that produced it. Keeping these here is what lets the source stay short.

[Layering](#layering) · [core](#core) · [ingestion](#ingestion) · [knowledge](#knowledge) ·
[register](#register) · [intelligence](#intelligence) · [delivery](#delivery) ·
[validation](#validation) · [orchestration](#orchestration) · [ops](#ops) ·
[storage](#storage)

### Layering

Packages mirror the data model: `core` → `storage` → `ingestion` → `knowledge`
→ `intelligence` → `delivery`, with `validation` and `orchestration` on top.
Layers communicate **only through the database**, which is what makes each one
runnable and testable alone. `tests/test_architecture.py` fails the build if a
lower layer imports a higher one.

Nothing in the pipeline imports `fli/delivery`. That is deliberate and
enforced: a change to how intelligence is *presented* must never be able to
change what was extracted or how it scored.

`fli/cli.py` is a thin dispatcher. Each layer keeps its own `main()` and flags
and stays runnable on its own; a facade that re-declared every flag would
become a second source of truth. Command order in `COMMANDS` is pipeline
order, so `--help` doubles as the data flow.

### core

**`config.py` — one file for every tunable.** Anyone asking "what threshold
produced this number?" should have exactly one file to open. Protocol
constants (XML namespaces, span attribute names, API URLs) deliberately stay
next to the code that speaks that protocol; they are not tuning knobs.

- `BLOG_BODY_MIN = 1500` — below this the feed served a teaser, so the body is
  hydrated from the article page. Measured: OpenAI/DeepMind blogs ~250–430
  chars against Meta's full 27k.
- `MIN_CHARS` is per source type — arXiv abstracts are short but dense, GitHub
  releases long but often boilerplate. `social` needs its own entry: a post
  caps at 280 characters, so the 400-char fallback floor would reject every
  tweet as `too_short`.
- `JS_WALLED_DOMAINS = {"openai.com"}` — domains that render article bodies
  client-side, so a direct fetch returns a shell. Measured 2026-07-29: 27
  openai.com blog docs averaged 286 visible chars against Anthropic's 228KB,
  and 14 died in stage 2 as `low_substance`. Mistral is *not* listed; its
  newsroom arrives full via sitemap (avg 292KB, same day).
- `HTTP_RETRIES` / backoff — transient transport failures (timeouts, resets,
  5xx) retry with exponential backoff. Client errors including 429 are **not**
  retried: a 404 will not heal in two seconds, and X's rate windows are 15
  minutes, so a short backoff would just spend the budget guard's patience.
- `BREAKER_THRESHOLD` — after this many consecutive transport failures against
  one host, the circuit opens and further requests to that host fail fast
  instead of eating a 20s timeout each. A dead sitemap host costs 3 timeouts
  rather than `MAX_SITEMAP_PAGES` of them. Process-scoped: the next run
  retries.
- X cost caps are a **spending control, not a tuning knob** — the run stops
  when it hits them, so a pagination bug cannot drain the balance. Raised from
  400/$3.00 when researcher handles took the account list from 8 to ~50: at the
  old ceiling the post cap bound after roughly 20 accounts, silently skipping
  every researcher after that.
- `X_BIO_REOBSERVE_DAYS = 7` — bios are where moves are announced. Seven days
  bounds steady-state spend at ~41 identities × $0.010/week = $0.41 while
  keeping detection latency inside the mobility pairing window.
- `CLUSTER_THETA = 0.4` — read off the similarity distribution, not guessed.
  See clustering below.

**`policy.py` — business decisions live in YAML, not Python.**

| | |
|---|---|
| `fli/core/config.py` | engineering constants — timeouts, seeds, cost caps. Changing one is a code change. |
| `config/policy.yml` | business decisions — what counts as decision-relevant. Changing one changes the ranking, and is owned by a domain expert. |

Validation is strict on purpose: an unknown key raises. A policy file is edited
by a non-programmer, so a typo (`slate-k` for `slate_k`) must fail loudly rather
than silently fall back to a default — a silent default would put the business
decision back in the code and defeat the point of the file. Every key is read by
something; config nobody reads is hardcoding with extra steps.

`positions`, `primary_rubric` and the slate keys are *optional* so new code
reads an older policy file. That is what makes the rollout safe: the code can
ship before the policy is swapped.

`positions_for()` and `channel_for()` answer two different questions and are
kept apart deliberately:

- `positions_for` — does this touch something the fund owns? Topical, and
  keyword matching is genuinely good at it.
- `channel_for` — through what *mechanism* does it transmit? Semantic, and
  keywords are bad at it.

Fusing them put sector nouns like `health` and `broker` in the
`competitive_displacement` lexicon, so any post mentioning health scored as
displacement whether or not anything was displaced. Exposure without a
mechanism is a candidate, not a signal.

`term_pattern` applies `\b` only at ends that are word characters. `\b` is a
transition between a word and a non-word character, so `\b@handle` would demand
a word character before the `@` and could never match "scientist @AnthropicAI".

**`text.py` — pure, deterministic, no I/O or clock.** `norm` is shared by
extraction and by the checks battery; a split-brain between those two once
caused a false C2 failure.

`fold_accents` is deliberately **not** part of `norm`. `norm` backs the
verbatim-quote invariant, where folding would let a quote "match" bytes it does
not equal. Names are the opposite problem: the same person is written both ways
depending on the source, and treating those as two people is an
entity-resolution failure.

`page_published` — a sitemap's `<lastmod>` says when a page last *changed*, not
when its story happened. One site-wide template rerender re-dated an
eight-month-old release announcement to "last week" and put it back on the
digest. The page's own declaration of its date outranks the sitemap's
bookkeeping. Enforced by check C18.

**`rubric.py` — "important" is a file, not a constant.** A rubric was once a
string in `judge.py`, which meant changing what the system considers important
required editing Python, and that there could only ever be one ranking.

The second consequence is the one that mattered. Measured on a single rubric,
`commercial` events took 60% of the top 50 at a 6.2× lift while `research` and
`benchmark` — 34% of the corpus — took none at all. That is the investment
rubric working correctly and the engineering audience being served nothing.

The rubric name is part of the labeler id (`llm:<model>/<rubric>/<version>`),
so judgements made under different definitions can never be pooled by accident.

### ingestion

**`feeds.py`.** All URLs verified fetchable 2026-07-23.

- Known gaps: Anthropic, Mistral, Qwen and Meta serve no blog RSS at standard
  paths, so a sitemap or substitute feed is used. Meta AI's own blog offers only
  a gzipped sitemap and is substituted by the Engineering feed.
- Two arXiv streams. `abs:` (mention) queries are the recall net — 126 of 127
  results were third parties writing *about* the labs, which is why stage 1
  gates arXiv docs on authorship. `au:` (authorship) queries anchor the stream
  on the labs' own output; only collectives that actually return papers are
  listed, since Anthropic, Llama Team and Meta AI publish under individual
  names that the register matches instead.
- xAI is deliberately absent from `ARXIV_QUERY_LABS`: `abs:"xAI"` is a homograph
  trap, since XAI is the standard acronym for eXplainable AI. It is covered by
  the `au:` query, which cannot collide.

**`x_api.py`.** Labs and researchers announce things on X hours to days before a
blog post exists, and personnel moves ("excited to share I'm joining…") often
appear only there.

Pricing (read 2026-07-26): pay-per-use billed per resource **returned**, not per
request — $0.005/post read, $0.010/user read. Resources dedupe within a 24h UTC
window, so re-running the same day is free for posts already seen.

Attribution rule, and it matters: lab accounts are `channel='official'` **with**
a `lab_id` — the lab speaking, so a `source_inferred` attribution is legitimate
(C12). Researcher accounts are `channel='third_party'` with `lab_id NULL`. A
person tweeting is not their employer announcing.

`_budget_guard` runs before any call: a cap that only triggers mid-run has
already spent the money it was meant to protect.

### knowledge

**`filtering.py` — stage 1, deterministic and free.** Only survivors reach the
LLM, which makes this the primary token-cost control. Kill-list patterns grow
only from observed noise, never speculation.

The social thread rule exists because a lab account's own posts pass the
"mentions a tracked lab" gate on the header this repo generated, so that gate is
vacuous there and thread fragments would reach the paid classifier. The
continuation-opener list is generic closed-class English — subordinators,
additive adverbs, fronted adjuncts — deliberately *not* read off the benchmark,
which would fit the rule to the set it is measured on.

The arXiv authorship gate: mentioning a lab is not being the lab.

**`extraction.py` — classify → extract → verify.** Every extracted claim carries
a verbatim quote matched back into the stored document. Claims whose quote does
not verify are discarded *and logged* — that counter is the hallucination-control
number, and it must be logged rather than silently skipped because it is the
denominator of the quote-verification rate.

**`channels.py` — the mechanism classifier.** The keyword lexicon it replaces
reaches F1 0.195 on the labelled X benchmark (3 true positives, 20 false).

Why the lexicon fails, which shapes the design: in a corpus entirely about AI,
AI vocabulary carries almost no information. The top false-positive triggers
were `energy`, `license`, `gpus`, `open weights` and `cluster` — the ambient
language of the domain. Channel membership is a semantic question ("does this
move a number in a thesis?"); keywords answer a topical one.

Caching is the cost story: a verdict is keyed on
`(sha256(text), policy_version, model)`, so re-running is free and a policy edit
correctly invalidates. The cache is committed, so "cached" is reproducible
rather than machine-local.

**`labs.py`.** Stage 1 asks "does this text mention a tracked lab" (substring);
stage 2 asks "resolve the model's lab string to an id". Different operations,
shared alias data, one owner. Tokenizing splits on any non-alphanumeric so
`DeepSeek-AI` → `{deepseek}`; whitespace-only splitting left these as one opaque
token that never matched.

**`expansion.py`.** Anchored on research seeds only: a founder co-signing a broad
institutional paper is an org signature, not a research collaboration, and its
huge author list swamps the queue with one lab's cluster. Founders stay tracked
entities; they are simply not co-authorship anchors.

### register

**`seeding.py`.** Seeding is gated on a verbatim name match in a fetched lab
page — a person enters the register only with evidence behind them. The seed
data itself lives in `config/register_seeds.yml`: the tracked universe is a
judgement call, not code.

**`x_identities.py` — the admission rule.** `identities.evidence_id` is NOT
NULL, so a handle cannot be asserted; it must be proven by a document. The proof
is the X profile itself — a bio reading "Research scientist @AnthropicAI" is a
verbatim, self-declared affiliation, stored immutably and quoted like any other
evidence.

| bio names a tracked lab | `verbatim` (self_link) |
|---|---|
| bio silent, name matches register | `name_match_only` (exact) |
| neither | rejected, logged as `x_handle_unverified` |

The second tier is `name_match_only`, not `corroborated`: agreeing with a name
already in the register is a name match, and `corroborated` is reserved for an
independent second source. C4 enforces the pairing, importing `TIER_FOR_METHOD`
from this module so the rule cannot drift between code and check.

Why a curated list at all: X has no "researchers at lab X" endpoint, and
guessing handles from names matches the wrong person often enough to poison the
register — silently, with attributed posts as the damage.

The live profile always wins over the candidate list, and disagreement is
recorded in the evidence locator rather than just printed: it measures how stale
a curated list goes.

`discovered_via='seed'`, not a new `x_profile` enum value — the column records
the discovery *mechanism*, which here is the same as every other seed. The
X-specific detail lives on `identities.platform='x'` and the evidence row.

Cleanup is explicit rather than a SAVEPOINT because `storage.insert_evidence`
commits internally, and a COMMIT releases every open savepoint — the rollback
would be a no-op that looks like protection.

**`gh_identities.py` — the third platform.** Ingestion reads `releases.atom`,
which carries a tag, a URL and a changelog and **no author field**, so there was
nothing in the stored bytes to correlate a person against. The fix is a
different endpoint, not more parsing: `/repos/{owner}/{repo}/contributors` plus
`/users/{login}`. Contributors are a better population than release notes — they
are people the lab's own repository says wrote its code.

Discovery runs in two passes. The configured feeds are all SDK/inference repos
and produced engineers with **zero overlap** against the arXiv population: the
people who ship a client library are not the people who write the papers. So the
org is mined too (`/orgs/{org}/public_members`, `/orgs/{org}/repos`).

Admission, ordered by how much inference each signal needs: public org member →
`verbatim`; `company` names the lab → `verbatim`; name in the register →
`name_match_only`; otherwise rejected and logged. Most contributors fail —
GitHub's `company` field is optional and often blank. The rejections are the
measurement, not a failure of the method.

Two traps that cost real rows:

- A `company` field is usually the **org handle**, not the company name, and the
  handle is often not the lab's name — Anthropic's org is `anthropics`, plural.
  `\banthropic\b` cannot match "anthropics", so three people whose company field
  literally named their employer were rejected. Org handles are listed
  explicitly.
- **A login is not a name.** Org membership was strong enough to admit profiles
  whose `name` is blank, so the register gained "people" called `dcarr622` and
  `liann-oai` — 26 of which failed C9. GitHub asserting an account belongs to the
  org does not say *who* it belongs to. The membership is still recorded as a
  rejection, so the count stays visible.

Pruning sweeps orphaned evidence and sources: deleting an identity strands its
evidence row, and C10 flags stranded rows forever.

Cost: nothing. The GitHub REST API is free — 60 req/hour unauthenticated, 5,000
with any token.

**`observation.py`.** "Never on a lab page" and "was on a page, no longer there"
are two different states, and conflating them manufactures attrition. Only the
second is a candidate mobility signal.

**`mobility.py` — affiliation history into personnel events.** The schema has
promised since day one that a person with rows at two labs inside a window is a
mobility event; this is the code that keeps it. It runs right after `observe()`,
so a move is emitted in the *same* run whose re-observation saw it — the window
bounds which observations may be **paired**, it never delays detection.

- `page_verbatim` only — a co-author inference is a mention, not presence.
  Pairing an inferred link with a page-verbatim one manufactures moves out of
  collaborations.
- Strict succession — A → B fires only when B's *first* observation is after A's
  *last*. Overlapping observations mean a dual affiliation, which is common and
  not a move.
- Idempotent — one insight per `(person, from_lab, to_lab)`, keyed by the
  evidence locator, so daily re-runs append nothing.

Synthesized insights are tagged in their evidence locator
(`kind='mobility_synthesis'`) rather than by a new schema value, so the live DB
needs no migration. Their entities carry `basis='source_inferred'`, the tier
scoring already downweights — a synthesized claim must never outrank a cited
verbatim one.

**`approval.py`.** Three things in precedence order: the human override file
(always wins), the per-lab slate that keeps the register balanced, and the
deterministic auto-approve rule. Approval is asynchronous and never blocks a
pipeline run.

### intelligence

**`clustering.py`.** No embeddings, no vector DB — Jaccard overlap on normalized
claim tokens. Clusters never span `event_type`, so within-document splits and
cross-document restatements of one event merge, while distinct events of the
same type that merely share vocabulary stay apart.

theta is read off the distribution: over same-type pairs it collapses after 0.2
— 14,320 pairs at ≤0.1, then 408 (0.2), 29 (0.3), 11 (0.4), at most 1 above. The
~10 pairs ≥0.4 are the genuine near-duplicates. 0.4 is the conservative cut,
since under-clustering beats merging distinct events.

**`features.py`.** Every feature is derivable from the current schema, and every
insight gets the same fixed feature set (one-hots are 0/1), so there are no
NULLs. Absent inputs get an explicit, documented neutral value.

**Lab identity is never a feature.** No `is_openai`. A DeepMind release and an
OpenAI release compete on merits.

Pure function of the DB except `recency`, which decays from the wall clock at
compute time: re-running on the same day reproduces identical values, and across
days only recency moves, by design.

`mechanism_channel` is the investment rubric's rule 1 ("channel over no
channel") given a column to land in — the f16 slate review showed the score
rewarding official-channel engineering posts and vendor case studies the reader
cuts, because the judges encode the rule in labels but no feature could express
it. Read from the committed verdict cache only, so the feature builder stays
free, offline and deterministic.

`basis` comes from a scalar subquery, not a LEFT JOIN: `event_entities` is 0..N
per event, so a join fans one insight into several feature-row inserts the moment
an event carries two lab entities.

**`judge.py`.** Three properties of the prompt are load-bearing:

1. **The lab name is withheld.** Rubrics ban lab identity as a reason and per-lab
   precision@10 is the fairness check, so a judge primed by lab prestige would
   invalidate it.
2. **A rule number is required.** A verdict that cannot cite the ordering rule
   that decided it is rejected and retried once.
3. **Presentation order is randomised** per pair (deterministically) and
   un-swapped on store, so position and content are not confounded. `sample_pairs`
   stores pairs as `(min(id), max(id))`, so without this the lower insight id is
   always event A, and ids follow extraction order.

The prompt is always binary with a mandatory confidence. Of 615 investment
verdicts, 274 came back `low` and were excluded from training, which raised
held-out accuracy — a silent coin flip could not have been excluded at all.

A per-pair cache breakpoint was trialled and rolled back: sampled pairs almost
never share a shown-first event (0/6, 0/15, 2/30 measured), so the 1.25× cache
write premium was paid and never repaid.

Batch mode sends everything through the Batch API at 50%, then shares the
parse/record loop. Anything the batch fails falls through to the synchronous
path — a batch problem degrades to full price, never to a lost verdict.

**`labeling.py`.** Sampling is stratified — ~50% cross-lab, ~30% cross-type, ~20%
random — so the ranker cannot just learn one lab's writing style. Deterministic
(seeded) and resumable: a labeled pair is never re-asked. The rubric belongs in
the human labeler id for the same reason it does on LLM labels: comparing a
human's investment judgements against a technical judge measures disagreement
about the *question*, not about the events.

**`scoring.py`.** Ranking becomes binary classification on pairwise feature
differences (`x_a - x_b → a wins`). Every model scores the identical event set,
and a hand-weighted sum is included as the baseline to beat. Whichever model wins
on held-out pairs ships, even if it is the simple one.

Exclusions in `load_pairs`, and why each:

- `human:%` — the audit sample, the one label set that is out-of-sample for
  *every* model including the judges. Training on it would contaminate the one
  number that answers "does the ranking agree with a person".
- `conf=low` — a forced choice on an equal pair is a coin flip that would enter
  training looking like signal.
- `lf:%` — circular. The labeling functions are deterministic functions of the
  features the models train on, so training on their votes partly fits an
  identity function. Including them scored gbm 0.697 / logistic 0.672; excluding
  them, gbm 0.663 / logistic 0.684, which flips the winner to the interpretable
  model.
- `rubric` — audiences disagree about which event matters, and pooling the label
  sets would average that into a ranking serving neither.

The split is at the **pair** level, not the row level: the same pair judged by
several labelers is several rows, and a row-level split puts the identical pair
on both sides, so "held-out" accuracy would partly measure memorisation.

Two golds are reported and each is labeled with what it is. `gold` uses all
labels and is in-sample by construction — kept because per-lab fairness needs its
coverage, and test labels alone leave most labs with no positives. `gold_te` is
built from held-out pairs only, so its p@10/ndcg are honest ranking metrics.

`event_scores.model` carries the rubric as a prefix (`investment:gbm_sklearn`)
rather than gaining a column, because the table is UNIQUE `(event_id, model)` and
SQLite cannot alter a UNIQUE constraint without rebuilding a table that checks
C15–C17 depend on and that holds every scored event.

`persist=False` is reporting mode: a figure must never mutate the thing it
describes. The evaluation figures call `bakeoff()` to read numbers, and writing
there would drop the per-rubric rankings and replace them with one trained on
both rubrics' labels pooled.

`MIN_FAIRNESS_N = 10` — below it, p@10 is arithmetic on too few events to mean
anything: a lab with 2 good events scores 1.000, indistinguishable in the figure
from a lab with 152. Small-n labs are counted, not scored.

**`top_events` — the editorial boundary.** Scoring produces an ordering; this
applies what to *show*, all configured in policy.yml, and none of it is a scoring
change.

1. Window — recent events only.
2. Undated out — an event we cannot date cannot be presented as recent. A
   synthesized mobility event's document is a lab page with no `published_at`;
   its honest date is the arrival observation in its locator, without which the
   undated rule would silently hide every detected move.
3. One per cluster — keep the highest-scoring member, count the rest as
   corroboration.
4. One per story — clusters are too fine to be news. One model launch produced 12
   events across 9 clusters at peak pairwise Jaccard 0.158 against a 0.4
   threshold, so no clustering setting merges them. Grouped here rather than in
   `insights`, where it would corrupt the corroboration feature.
5. Lab cap — without it one lab took half the top 10.
6. Mechanism gate — for rubrics named in `require_mechanism`, a quote the
   classifier **positively** verdicted `none` is dropped. A quote it has never
   seen passes: absence of a cache entry is an infrastructure fact about the run,
   not evidence about the event, and a gate must only act on evidence.
7. Faithfulness gate — insights whose claim the entailment check called
   `not_entailed` never render, for any persona. A slate citing the quote as
   support for the claim would be lying. This applies even under `dedupe=False`:
   it is a correctness bound, not a composition rule, and a "raw ordering"
   baseline including unfaithful claims would flatter every model measured
   against it.

Rules 1–2 are per-event; 3–5 depend on what has already been chosen, which is why
they live in `SlateFilter` rather than in SQL. `_same_story` compares only against
the handful already selected, so there is no transitive chaining — a union-find
version merged 41 unrelated events into one "story" by hopping A–B–C.

**`weak_supervision.py` — Dawid-Skene.** There is no ground truth for "which of
these two events matters more to a portfolio manager", so a judge cannot be
scored directly. DS estimates each labeler's accuracy and the latent true label
from the disagreement structure alone.

DS assumes labelers are conditionally independent given the true label. Two
prompt variants of one model are not — they share weights, training data and
failure modes, so they agree for reasons unrelated to being right. Measured:
three variants of one model agreed 92–100%, and DS duly rated all three ~0.99.
That number was an artifact of asking one model three times.

The figure therefore requires at least two independent **model families** and
refuses to render otherwise. With Claude, GPT and a human auditor on identical
pairs it reports 0.876, 0.864 and 0.842.

**`contributors.py`.** The trap this refuses is inventing a person-quality
formula (`papers × w1 + followers × w2 …`) whose weights nobody can defend —
the same arbitrary-weighted-sum red flag the event bake-off exists to kill, and
strictly worse here because there are no pairwise labels over people to validate
against.

Instead a person's score aggregates already-validated numbers: the sum over
their linked events of the event's percentile under the rubric's winning model,
times the same recency decay features use. Every term is inherited, so the module
adds **zero** new tunable parameters, and each score decomposes into the exact
events that produced it.

### delivery

One shared core, two tailored outputs. Everything below this package is
audience-neutral; this is where the two readers diverge.

**`positions.py` — event → holding edges.** Two independent questions, kept
apart: *exposure* (does this touch something the fund owns — topical, keywords
are good at it) and *mechanism* (through which channel does it reach the position
— semantic, keywords are bad at it, so the channel comes from the classifier).

`event_positions.channel` is NULLABLE and that is the point: "exposure found,
mechanism not established" is a real and common state. Forcing a channel would
manufacture a causal story the evidence does not support.

**A channel establishes the mechanism, not the sign.** That distinction cost two
wrong calls before it was made explicit:

| event | demand | |
|---|---|---|
| "Mistral is building a 10 MW data center" | up | tailwind |
| "Meta's scheduler saved 3.28 megawatts" | down | headwind |
| "Gemma 4 12B matches a 26B model while smaller" | down | headwind |

All three are `energy_datacenter` or `compute_memory`. The channel is right every
time; the direction is opposite. Reading it correctly means knowing whether the
quantity went up or down, which is a judgement about the sentence — so it is left
to the persona layer. Only `competitive_displacement` carries an inherent sign.

A classifier verdict of `none` is an **answer, not a gap**. Falling through to the
lexicon there overrode 16 explicit negatives with keyword guesses, which is the
precise failure this module claims to avoid.

**`personas.py` — what an event means.** A ranked claim with a citation is still
a summary; this is where the system commits to a reading. The sign is decided
here, by something that reads the claim, and `reasoning` is NOT NULL in the
schema so the reader can always check the working.

Two audiences, two questions, and the fields mean different things:
`investment` → threat | tailwind | unclear; `ai_team` → adopt | investigate |
monitor.

The prompt is constrained to the evidence — claim, verified quote, date, the same
material a reader can check — and is not given the lab name, for the same reason
the judge is not.

Candidate selection takes **every** event with position exposure, not only the
ones already signed. Filtering on `direction != 'unclear'` fed the model only the
events the deterministic layer had already decided (1 of 54), excluding precisely
the three cases that justified building the layer.

`max_tokens` is generous because a hypothesis plus reasoning is several times
longer than a judge verdict, and adaptive thinking spends part of the budget
before writing a character. At 500 the two most nuanced events were cut off
mid-JSON.

**`digest.py` — the periodic report.** Deliberately the dumbest thing in the
repo: it selects nothing and computes no scores. It asks `scoring.top_events` for
the slate — the same call the persona layer makes — joins whatever readings
exist, and lays it out.

That shared call is the point. The two used to diverge: the persona layer read the
raw ranking straight from `event_scores` while the digest applied the editorial
rules, so **zero** of the ten events rendered for the engineering audience
appeared in the technical digest at any window. Every reading was paid for and
never shown.

One content model, two renderers: `blocks()` returns `(style, payload)` pairs and
markdown and PDF are two functions over that list. Writing the report twice would
let the exported PDF drift silently from the markdown.

What it refuses to do: `unclear` items are not hidden — 52 of 54 position edges
are `unclear` by design, and showing only the two signed ones would imply the
system knows more than it does. Coverage is stated in the header, including how
many items carry no reading at all.

**`alerts.py` — the push path.** A digest is opened when the reader chooses; an
alert interrupts. So the bar is not "interesting" — the digest already carries
everything interesting — it is "a reader would want to know before the next
report".

**The trigger is the reading, not the rank.** The obvious rule is a score
threshold and it is wrong here, because rank is not stable across the bake-off
contenders. OpenAI launching Health in ChatGPT — the one event in 954 the
deterministic layer calls a threat to a named holding — ranks 22nd under the
shipped GBM but 14th under logistic, 37th under hand-weights and 506th under
the recency baseline. Any rank threshold makes the alert's firing an accident
of which model won the last bake-off, which is no basis for interrupting a PM;
a signed reading means a reader-facing judgment was actually made about a
position.

Low confidence is excluded: it means the reader flagged the evidence as thin, and
interrupting on it is how a channel gets muted. One event is one alert even when
it moves several holdings — the Health launch signs edges to both HNGE and OSCR,
and delivering that twice is two interruptions carrying one fact. Enforced by a
UNIQUE constraint in the schema rather than left to the caller.

Over the whole corpus these rules fire 3 times. That is the intended order of
magnitude: rare enough to be read.

**`pdf.py` — no dependencies on purpose.** Every obvious library adds an install
step to a repo whose runtime is the standard library plus what scoring needs:
reportlab is a new dependency, weasyprint pulls in system libraries, a
pandoc/LaTeX route works on one machine and nowhere else.

What the digest needs from a PDF is small and fixed — left-aligned text in two
weights, wrapped to a measured width, page breaks, clickable links. That is about
two hundred lines of PDF 1.4, stable since 2001.

Deliberately unsupported: images, tables, colour beyond a foreground, embedded
fonts, any encoding beyond WinAnsi. The three standard Helvetica faces are
guaranteed present in every conforming viewer, which is why no font is embedded
and why the metrics are hard-coded — those are Adobe's published core-font
metrics, fixed by the spec, not values sampled from one machine.

Text with no WinAnsi form (CJK, emoji) degrades to `?` rather than corrupting the
file. Typographic punctuation is mapped exactly, because lab posts are full of
curly quotes and em dashes and dropping them would mangle every second quotation.

**`mcp_server.py` — a surface, not a second system.** Every tool delegates to
the layer function that already owns the answer: `top_insights` calls
`top_events`, the same call the digest and the web UI make. An agent that got a
different slate from the human reading the digest would be worse than no agent,
so the surface is forbidden from deriving anything itself.

Tool bodies are plain functions taking a connection, and the SDK import lives
inside `build_server()`. That is what keeps the behaviour under test on a
machine without the MCP SDK — the wiring is the only part that needs it.

Strictly read-only: no tool writes, spends a token, or touches the network. A
long-running server also opens a fresh connection per call, so a pipeline run
replacing the database underneath it cannot leave the server holding a stale
handle.

The wire format is narrower than the internal row on purpose. `score_components`
and `cluster_id` are ranking internals and would invite an agent to reason about
mechanics it cannot validate; `url` and `quote` stay, because an agent that
cannot cite its source is the failure mode this whole system exists to prevent.

`search_insights` is deliberately **not** slate-filtered while `top_insights`
is. Raw corpus matches let an agent find precisely what the slate suppressed —
duplicates, not-entailed claims, events with no mechanism. A read surface that
can only show the filtered view cannot be used to audit the filter.

### validation

**`checks.py` — C1–C20.** A pure function of the database: no network, no LLM, no
randomness. Exit 0 = green, and the exit code *is* the verdict.

Register balance is **printed, not checked**: it has no failing condition, so it
is reported as evidence rather than enforced as a gate.

C15–C17 are vacuous while `event_scores` is empty, so a green C17 is not evidence
that the bake-off has been run.

C18 allows `DATE_DRIFT_MAX_DAYS` of slack — a page's byline and its stored
`published_at` may legitimately differ by a little (timezones, edits shortly after
publication). Beyond that the stored date is describing a different event than
the page is.

**`evaluation.py` — figures and tables.** One command regenerates every number
and chart, so nothing in the report is hand-copied. Figures **read** the database
and never write to it.

Two rules the module enforces:

1. **Metrics are tiered by what ground truth exists.** F1/precision/recall are
   only reported where truth is known by construction or against a stated human
   reference. On real data with no gold standard it reports *agreement*, never
   "accuracy". Every caption carries its tier.
2. **A missing figure says why it is missing** — it states the command that would
   produce it rather than crashing or drawing an empty axis that looks like a
   result.

A funnel must stay a funnel: the last bar has to be a subset of the first. One
document yields several events, so plotting the insight count makes the output
exceed the input; surviving *documents* are counted and the event count carried
as an annotation.

**`x_benchmark.py`.** The labels are frozen and there is deliberately no
generator to rebuild them: regenerating would spend money to produce a
*different* reference, silently invalidating every number measured against the
old one.

A wrong-channel prediction counts once as fp and once as fn. That strict reading
is deliberate — naming the wrong transmission channel is not a partial success,
since the channel exists to say which position it touches.

**`drift.py`.** Deliberately outside the battery. `checks` asks "is the database
internally consistent?"; drift asks "has the world moved out from under the
models fitted to it?" — a different question with a different consequence, so it
must not gate the build. An organic news cycle is not a defect. It carries its
own exit code (the count of MAJOR drifts) so a scheduler can still alarm.

PSI bands are the conventional 0.10 / 0.25 from banking scorecard practice,
kept standard on purpose so the numbers are comparable to the literature rather
than house-tuned. KS is the two-sample statistic against the α = 0.05 critical
value, implemented directly: scipy would be a large dependency for two
functions, and the tie-handling has to be explicit anyway — both sides advance
past the smaller value *entirely, ties included*, before measuring, or tied
samples register a spurious gap.

Empty bins are smoothed to 1e-4 rather than dropped, because the appearance of a
new category **is** drift and must register instead of dividing by zero.

The window is anchored to the newest document rather than the wall clock, so the
report is reproducible on a static corpus — running it a month later gives the
same numbers rather than a slowly emptying window.

Known limitation: `_DOC_MIX` and `_DOC_LEN` count every `raw_documents` row,
including register/identity pages that never enter extraction. A co-author
expansion run therefore reads as corpus drift (measured: 121 arXiv documents in
one window yielding zero insights, driving PSI to 0.356). The funnel figure
excludes register documents from every bar for exactly this reason; these two
metrics should too. `_EVENT_MIX` and `_SCORE` are computed over insights and are
unaffected.

### orchestration

**`pipeline.py` vs `graph.py`.** Two runners over one set of stages, kept apart
on purpose. `pipeline` is the plain sequential composition and has no optional
dependency; `graph` packages the same stages — plus the paid ones — as a
LangGraph `StateGraph`, and is imported lazily so its absence costs nothing.

No node has a body of its own: every one is a single call into the layer
function the corresponding CLI command already invokes. The graph owns
**ordering and gating only**, so there is no second implementation of any stage
to drift out of sync with the first.

The DB connection is bound into nodes by closure rather than carried in
`RunState`. State should stay printable, and the database is the actual shared
medium between stages anyway — putting the connection in state would make the
run summary unprintable and imply the graph passes data it does not.

`_spend_ready` requires `--spend` *and* an API key, and the conditional edges
route around `verify`/`personas`/`faithfulness` when it is false — the paid
nodes are not on the path rather than skipped inside it, so a default run cannot
spend even if a node body were wrong.

**The `approve` gate.** `--spend` states intent at launch; the flag alone is a
bad gate, because the operator sets it before knowing what the run will find.
So a dedicated node raises a LangGraph `interrupt` mid-run with the work sized
from the current database (`spend_estimate`: insights with no `claim_checks`
row, plus existing note count), and the decision is made against that.

`approved` is written into state once and read by *both* conditional edges
(`approve` and `digest_parity`). One decision covers both paid segments — being
asked twice in one run would train the operator to stop reading the question.

Declining is deliberately not an error. The run continues on the free path
exactly as if `--spend` had been absent, and `checks` still sets the exit code;
an operator saying "not today" should not produce a red build.

Non-interactive callers are handled rather than left to hang: `--yes` skips the
pause for schedulers, and `EOFError` on a missing tty is caught and read as a
decline with the `--yes` hint printed. Without `--spend` or a key the gate
resolves to the free path *without pausing*, so unattended default runs never
block.

The `InMemorySaver` checkpointer exists only because `interrupt` needs one to be
resumable. In-memory is sufficient and deliberately not more: the pause and the
resume happen in the same CLI process, so durable checkpointing would be
infrastructure for a scenario that does not exist here.

One ordering fix over the CLI habit is deliberate: delivery runs **after** claim
repair and persona notes. The reverse order shipped digests citing claims repair
had already rewritten (2 stale claims in
`docs/digests/2026-07-30-ai_team.md`), so the correct order is structural
instead of something the operator has to remember.

`SystemExit` from a layer is caught and recorded as a summary line, not a crash:
a stage refusing (too few labels for a rubric, no API key) is information, not
failure. `tolerant=True` mirrors `pipeline.py` for the network-facing stages —
an API outage in an optional stage must not kill the deterministic run.

### ops

**`llm.py` — the single choke point.** Every call is cost-logged to `llm_calls`
from call #1.

A second provider exists for one reason: Dawid-Skene needs conditionally
independent labelers, and prompt variants of a single model are not. It doubles
as the fallback path when one provider is down.

One model per task, picked on measured cost-quality. Haiku takes the
high-volume, bounded-output jobs; Sonnet takes the ones needing faithful quoting
or audited reasoning. The Haiku work costs $0.65 against $1.95 on Sonnet, and the
classify gate stops ~93 documents before extraction.

`PRICES` has **no default entry and no fallback rate**: a guessed price produces a
confident number nobody checked, so an unknown model raises. `preflight()` checks
SDK, key and price *before* the first paid call — `cost_usd` also raises on an
unpriced model, but only after the API has answered, so the call is paid for and
then discarded by the exception.

Reasoning tokens are billed as **output** and are already inside
`completion_tokens`, so the rate is correct — but the visible JSON verdict is
~120 tokens while billed output can be many times that. `reasoning_tokens` is
logged separately so the split stays reportable.

Prompt-cache multipliers: a cache write costs a 25% premium once, every read of
that prefix costs 10%, and the Batch API halves everything in exchange for
asynchronous delivery. **Caveat measured 2026-08-01:** every system prompt in
this repo is 200–620 tokens, below Anthropic's cache minimum (1024 Sonnet / 2048
Haiku), so a `cache_control` mark on the system block alone caches nothing today.
It is still always sent — below-minimum marks are free and ignored, and the mark
starts working the day a prompt grows past the line.

`temperature=0` wherever the model accepts it, since every task is structured
extraction or classification. Models running adaptive thinking reject an explicit
temperature; that is detected once per process and remembered, rather than
costing a rejected round trip on every call. On those models reproducibility
cannot be asserted from a parameter and has to be **measured** instead — see
`judge --consistency N`.

OpenAI's chat API differs in three ways, each handled by learning the model's
capability once rather than hardcoding a model list that goes stale: the system
prompt is a message rather than a top-level argument, reasoning models want
`max_completion_tokens`, and reasoning models reject `temperature`.

`_strict_schema` tightens only the wire copy of a pydantic schema — Anthropic
structured outputs require `additionalProperties: false` on every object
(measured 2026-08-01: 400 without it), while the models keep their default
`extra='ignore'` so the client-side fallback validates exactly as before.

**`tracing.py`.** Off by default. When disabled, or when opentelemetry is not
installed, every function is a no-op and the pipeline runs exactly as before.
Phoenix is prompt-iteration tooling only: `checks.py` stays the source of truth,
and reported numbers always come from there.

### storage

Documents are insert-only and deduped by hash.

`store_page` dedupes on extracted **text**, not raw bytes: dynamic sites change
build hashes and nonces on every fetch, so raw-hash dedup never fires and
versions pile up.

Migrations are additive columns only — `CREATE TABLE IF NOT EXISTS` cannot add a
column to an existing table. No framework: single-user project, one database.
Tables whose *shape* changed are rebuilt only while empty, so no collected data
is ever destroyed; a non-empty one is left alone and reported. Migration notes
are printed rather than silent, because a schema change to a database holding
measured results is something the operator should see happen.

`insert_insight` writes the insight and its `event_entities` mirror in one
transaction, because C11 spans both — a crash must not leave an attributed
insight without its cited mirror.
