# Architecture

Frontier Lab Intelligence tracks eight frontier AI labs and the people inside
them, turns their public output into evidence-backed events, ranks those events
for two different readers, and delivers a cited report and an alert path for
each. This document explains how the system is built and why it is built that
way. It is written to be read alongside the code; every claim of behaviour here
is enforced somewhere by a test or an invariant check, and the relevant name is
given.

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
messiness are recorded rather than described. From the preserved robustness
snapshot (`docs/ingestion-robustness-evidence.txt`; `fetch_log` 458 ok / 20
error / 9 empty):

| failure mode | where | how the system behaved |
|---|---|---|
| HTTP 429 rate limit | arXiv API queries | logged `error`, run continued, other sources unaffected |
| read timeout | arXiv API queries | logged `error`, run continued |
| blocked / bad request | `ai.meta.com/research/` → HTTP 400 | logged `error`; a substitute channel was configured |
| empty feed (valid response, zero items) | a GitHub `releases.atom` | logged `empty` with `items_found=0` — absence recorded, not a silent gap |

Every fetch attempt is a row, including the ones that returned nothing, so an
empty feed and a failed feed are *different* recorded states and a source going
quiet is visible rather than indistinguishable from "no news". Rate limits are
handled by politeness — a fixed inter-request delay on the arXiv API — rather
than a retry storm. Because a truncate-and-rebuild resets `fetch_log`, that
evidence is preserved as a text export of a named snapshot instead of being
re-manufactured; the live database shows its own current tally (733 ok / 148
error / 16 empty).

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
Kendall τ of **+0.064** — near zero, i.e. close to unrelated orderings.
Same events, same features — only the definition of "important" differs, and it
differs enough that one ranking demonstrably cannot serve both readers. The
investment top 10 is commercial and infrastructure — government cloud
commitments, token pricing, datacenter power. The engineering top 10 is eight
open-weight releases. Neither list would serve the other reader at all.

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
the persona layer, which reads the claim. On the live corpus, 57 of 59 exposure
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
reviewer reads in the repo. Items without a reading are published *as* uncovered
rather than dropped, so the coverage gap is visible in the committed artifact.

`alerts` is the push path, and its trigger is deliberately not the score. The
one event the system calls a threat to a holding ranks 10th of 734 under the
shipped model — but 15th under logistic, 26th under hand-weights and 84th under
the recency baseline. A top-decile rule would therefore fire or not fire
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
single-writer daily pipeline, and it makes the deliverable a file a reviewer can
open.

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
OpenTelemetry packages are absent (`requirements-tracing.txt`, kept separate
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
report is reproducible on a static corpus — a reviewer running it next month
gets the same numbers.

**Drift is deliberately not part of `checks`.** An organic news cycle must not
turn the release gate red. It is a monitoring signal with its own exit code (the
count of MAJOR drifts) so a scheduler can still alarm on it, and it runs as a
free node inside the graph, informational only. Making drift an invariant would
mean the build fails because the world changed, which is not a defect.

On the committed corpus it reports **3 MAJOR of 4 metrics**, and what it caught
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
only new LLM extraction is skipped. A reviewer with no key gets a green
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
**$25.18 across 7,423 calls**. Extraction — the part that produces what a reader
sees — is 19.7% of that; judging and labeling, which exist only to validate the
ranking, are 64.8%. The full breakdown, the unit economics and the three places
cost changed a design decision are in [tokenomics.md](tokenomics.md); the raw
per-task split is `docs/metrics-out.txt`, section M5a.

## Data discipline

The committed database is the clean production state, and it is the *only*
database in the repository. Validation experiments — the second-model judge, the
human audit, the bake-off comparison of non-winning contenders — have their
*results* written into the evaluation report and the final report rather than
accumulating as extra rows in the live DB.

Where an experiment's evidence could not survive in the live database, it is
exported as **text** rather than kept as a binary. The ingestion-robustness
history is the worked example: a truncate-and-rebuild resets `fetch_log` to
all-ok, so the failure history was exported to
[ingestion-robustness-evidence.txt](ingestion-robustness-evidence.txt) — every
failure mode, count and URL — and the multi-megabyte working snapshot it came
from was discarded. A committed claim should rest on something a reviewer can
read in a diff, not on a 34 MB file they would have to be sent separately.

A run checkpoints the write-ahead log back into the main file before committing,
so a committed DB never silently misses the writes of the run that produced it.
