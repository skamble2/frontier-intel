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
orchestration     4   the pipeline (composition only)
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
one event the system calls a threat to a holding scores below the median,
because the rubric rewards specificity and shipped-ness rather than portfolio
consequence — a top-decile rule would have missed it and fired on ten model
releases instead. So an alert fires on a *signed direction* (a
classifier-established position edge, or a persona reading at medium-or-better
confidence), bounded by the reporting period, and the `alerts` table's UNIQUE
key means each fires exactly once. A channel that repeats itself every run
trains its reader to ignore it, so once-only is enforced in the schema, not left
to the caller.

## The stack, and model selection per task

The stack is deliberately small: **Python 3, SQLite, scikit-learn, the Anthropic
and OpenAI SDKs, Flask for the web surface, matplotlib/seaborn for figures.**
There is no vector store, no embeddings, no second database, no orchestration
framework. Scoring is SQL plus scikit-learn over a few hundred rows; a vector
store would be infrastructure carrying no measurement. SQLite is the right size
for a single-writer daily pipeline, and it makes the deliverable a file a
reviewer can open.

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

**Missing plotting or web libraries → the pipeline is unaffected.**
matplotlib/seaborn/pandas are imported only inside `fli/validation/evaluation.py`
and Flask only inside `fli/web/app.py`, both lazily. The daily pipeline runs
without any of them installed.

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
register, cluster, features, score, evaluate, positions, digest, alerts — and
exits on the validation battery's verdict. The three paid steps stay manual and
explicit: `judge` (new pairwise labels), `x` (the paid source), and `personas`
(the written reading). Each previews its projected spend before sending, checks
the API key and the price table, and refuses to start a run it cannot afford.
Cost is logged per call to `llm_calls`; the whole system to date has spent
**$25.18 across 7,423 calls**. Extraction — the part that produces what a reader
sees — is 19.7% of that; judging and labeling, which exist only to validate the
ranking, are 64.8%. The full breakdown, the unit economics and the three places
cost changed a design decision are in [tokenomics.md](tokenomics.md); the raw
per-task split is `docs/metrics-out.txt`, section M5a.

## Data discipline

The committed database is the clean production state. Validation experiments —
the second-model judge, the human audit, the bake-off comparison of non-winning
contenders — have their *results* written into the evaluation report and the
final report, and the raw experimental runs are snapshotted under
`data/snapshots/` rather than accumulating in the live DB. A run checkpoints the
write-ahead log back into the main file before committing, so a committed DB
never silently misses the writes of the run that produced it.
