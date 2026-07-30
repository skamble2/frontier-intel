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
validation        3   the C1–C18 invariant battery (reads every layer)
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

A battery of eighteen invariant checks (C1–C18) reads every layer and asserts
the properties the rest of the system depends on: quotes re-verify (C1/C2),
every person is evidenced (C3) and passes name hygiene (C9), affiliations are
dated and evidenced (C5), every source was fetched (C7), no evidence is orphaned
(C10), scores cite the current policy version and every event type is ranked
(C17), published dates are the page's own rather than a sitemap timestamp (C18).
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
rankings share **2 of their top 10** and correlate at a Kendall τ near zero
(≈ +0.04).
Same events, same features — only the definition of "important" differs, and it
differs enough that one ranking demonstrably cannot serve both readers. A paper
with released weights and a reproducible method is the top of the engineering
ranking and near the bottom of the investment one, exactly as it should be.

## Connecting private labs to public equities

Most frontier labs are private, so "important lab event" and "actionable for a
public-equity investor" are different questions, and the system keeps them
apart. `positions.py` answers two independent sub-questions:

- **Exposure** — does this text touch something the fund owns? Topical, and
  keyword matching is genuinely good at it.
- **Mechanism** — through which transmission channel does it reach the holding?
  Semantic, and keywords are bad at it, which is why the channel comes from an
  LLM classifier rather than a lexicon (measured: keyword F1 0.33 vs classifier
  F1 0.57, and the lexicon's failures are confident ones — it once returned a
  phone codec that "increased power usage" as a datacenter signal).

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

## What runs automatically, and what costs money

The daily pipeline runs every deterministic, free stage — ingest, filter,
register, cluster, features, score, evaluate, positions, digest, alerts — and
exits on the validation battery's verdict. The three paid steps stay manual and
explicit: `judge` (new pairwise labels), `x` (the paid source), and `personas`
(the written reading). Each previews its projected spend before sending, checks
the API key and the price table, and refuses to start a run it cannot afford.
Cost is logged per call to `llm_calls`; the whole system to date has spent under
$20.

## Data discipline

The committed database is the clean production state. Validation experiments —
the second-model judge, the human audit, the bake-off comparison of non-winning
contenders — have their *results* written into the evaluation report and the
final report, and the raw experimental runs are snapshotted under
`data/snapshots/` rather than accumulating in the live DB. A run checkpoints the
write-ahead log back into the main file before committing, so a committed DB
never silently misses the writes of the run that produced it.
