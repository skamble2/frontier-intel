# Architecture

Frontier Lab Intelligence tracks eight frontier AI labs and the people inside
them, turns their public output into evidence-backed events, ranks those events
for two different readers, and delivers a cited report and an alert path for
each. Every behaviour described here is enforced by a test or an invariant
check, named inline.

## The invariant everything hangs off

**Every downstream number traces back to a byte a lab published.**

An insight cannot exist without an `evidence_id`; an evidence row cannot exist
without a verbatim quote; and every run re-hashes each stored document and
re-checks that each quote is still a byte-for-byte substring of its source
(C1, C2). A page edited under us breaks the check rather than silently keeping a
stale claim. A direction shown to a PM is only ever stated from a mechanism a
classifier established, never from a keyword the text happened to contain.

The default answer everywhere is "the evidence does not establish that", said
out loud rather than guessed.

## Layers

```
core / ops        0   text, http, config, paths, the LLM client
storage           1   SQLite persistence — no domain logic
ingestion         2   feeds, the paid X source
knowledge         2   filtering, extraction, the researcher register
intelligence      3   clustering, features, judge labels, scoring
validation        3   C1–C20 battery + drift (reads every layer)
delivery          4   positions, personas, digest, alerts, mcp, web
orchestration     4   pipeline, graph — composition only
```

Layers communicate **only through the database**, never by calling each other in
memory. `tests/test_architecture.py` walks the import graph and fails the build
on an upward import. Nothing imports `delivery`: how intelligence is *presented*
must never be able to change what was extracted or how it scored.

## The pipeline

| stage | what it does | notable |
|---|---|---|
| `ingest` | poll blogs, newsrooms, arXiv, GitHub, X | every attempt is a `fetch_log` row, including empties — a source going quiet is distinguishable from "no news" |
| `filter` | stage-1 deterministic kill | free; only survivors reach a model |
| `extract` | document → events, each a claim + verbatim quote | quote verified against stored bytes *before* the row persists; a miss is a logged rejection, not a stored row |
| `register` | resolve a researcher across arXiv, X, GitHub | tiered admission: a verbatim self-link beats a name match beats nothing, and every admission carries its evidence row |
| `cluster` | collapse near-duplicate claims | one announcement echoed nine times counts once, the rest as corroboration |
| `features` | numeric training surface | `insight_features` is what the model reads; the reader-facing `score_components` JSON is kept separate on purpose |
| `judge` → `score` | pairwise labels → bake-off → shipped ranking | see below |
| `positions` → `personas` → `digest` / `alerts` | last mile, per audience | see Delivery |

**Ingestion depth is a filtering problem.** An early audit found the classifier
was mostly judging *non-article input*: several blog feeds carry only a
250–430-char teaser, and newsroom pages stored 135–290k of raw HTML while stage 2
capped input at the first 6,000 characters — so the model read nav boilerplate.
Both produced false rejections. HTML→text extraction now runs before stage 2 and
quotes verify against that same cleaned text, so raw HTML stays immutable. What
remains was triaged rather than asserted: **19 JS-walled, 13 GitHub version
bumps, 9 thin marketing** — about half the suppressed items are the noise floor
working correctly.

Three decisions, and the ones *not* taken matter most: no "trust the headline"
(a teaser cannot support a claim), no headless browser (~300 MB for two sites),
and the one high-value event behind the JS wall was captured manually under the
same immutability rules rather than admitted as an exception.

The live `fetch_log` carries **771 ok / 164 error / 20 empty** — dominated by a
transient `export.arxiv.org` certificate failure and by `x.ai` returning 403 to
both a direct fetch and the rendering proxy, which is why xAI has almost no
coverage.

## Model routing

Routing is one dictionary, `MODEL_FOR_TASK` in `fli/ops/llm.py`.

| task | model | why |
|---|---|---|
| `extract`, `repair` | Sonnet 5 | emits the claim *and* the quote that must re-match the source; quote fidelity is where cheap models fail |
| `judge` | **two independent model families** | not two prompts of one model — Dawid–Skene needs conditionally independent labelers, and a judge drawn from the same family that extracted the claim shares its priors |
| `persona` | Sonnet 5 | reader-facing judgment and tone; ~100 calls, so price is irrelevant |
| `classify`, `channel`, `verify`, `faithfulness` | Haiku 4.5 | closed-set classification, no generation |

The rule: **Sonnet where a wrong answer enters the database as a fact; Haiku
where a wrong answer only costs a re-check.**

One measured wrinkle, kept because it contradicts the routing: on the judge task
the cheaper family is at least as good. It costs **4.6× less per stored label**
($0.0022 vs $0.0101), returns fewer low-confidence verdicts (22.0% vs 27.0%),
and its Dawid–Skene reliability is marginally *higher* (0.874 vs 0.864). Working
in [tokenomics.md](tokenomics.md).

**Stack:** Python 3, SQLite, scikit-learn, the Anthropic and OpenAI SDKs,
Pydantic, LangGraph, Flask, the MCP SDK, matplotlib. No vector store, no
embeddings, no second database — scoring is SQL plus sklearn over a few hundred
rows. Everything past the first four is lazily imported, so the daily pipeline
runs with none of it installed.

## Scoring without ground truth

No labelled dataset of "important frontier-lab events" exists, so three things
stand in:

**A rubric, not a gut feeling.** Importance is a versioned YAML file of ordered
tie-breaking rules. The judge applies it to a *pair* and cites the rule that
decided — pairwise sidesteps scoring an event in isolation, and a rule number
makes every verdict auditable. A forced binary with an honest confidence keeps
coin-flips out of training: `low` means "I guessed, exclude this".

**The lab name is withheld.** Presentation order is randomised per pair and
un-swapped on store. A judge primed by lab prestige would invalidate the
fairness metric that checks it.

**Reliability is inferred from disagreement**, via Dawid–Skene across at least
two independent model families. Three prompt variants of *one* model agreed
92–100% and the estimator rated them all ~0.99 — an artifact the system now
refuses to produce.

Results, and the human audit that qualifies them, are in
[final-report.md](final-report.md#how-it-was-validated).

## Two audiences, two rankings

The same corpus, features and clustering feed two rubrics. Each trains its own
model on its own labels, tagged `llm:<model>/<rubric>/r<version>` and never
pooled.

Measured, the two rankings share **0 of their top 10** and correlate at Kendall
**τ = +0.128**. Investment leads with personnel, commercial and infrastructure;
engineering leads with open-weight releases and shippable tooling. One ranking
demonstrably cannot serve both.

## Private labs → public equities

Most frontier labs are private, so "important lab event" and "actionable for a
public-equity investor" are different questions. `positions.py` keeps them apart:

- **Exposure** — does this touch something the fund owns? Topical; keywords are
  good at it.
- **Mechanism** — through which channel does it reach the holding? Semantic;
  keywords are bad at it, so this comes from an LLM classifier (measured against
  a 100-post human-audited reference: lexicon F1 0.267 vs classifier 0.444, and
  the lexicon's failures are confident — it once read a phone codec that
  "increased power usage" as a datacenter signal).

The channel column is nullable and that is the point. A *direction* is only
stated from a mechanism carrying an inherent sign, and most do not: "building a
10 MW datacenter" and "saving 3.28 MW" are the same channel pointing opposite
ways, so the sign is deferred to the persona layer, which reads the sentence. On
the live corpus **52 of 54 exposure edges are `unclear`**, by design.

## Delivery

`personas` is the only delivery stage that spends. It is constrained to the same
evidence a reader can check, is not given the lab name, and the parser rejects a
reading that omits its working or answers the other audience's question.

`digest` is deliberately the dumbest module in the repo: it selects nothing and
computes nothing, asking `scoring.top_events` for the same slate the persona
layer renders against. The two used to diverge, and every paid reading described
an event the reader would never see. One content model feeds both renderers, so
the exported PDF cannot drift from the Markdown.

`alerts` fires on a **signed direction**, never a rank. The one event called a
threat to two holdings ranks 22nd of 954 under the shipped model — but 14th
under logistic, 37th under hand-weights, 506th under the recency baseline. A
rank threshold would make the alert an accident of which model won the last
bake-off. The `alerts` UNIQUE key enforces once-only in the schema.

`mcp` serves four **read-only** tools over stdio for agent clients. Every tool
delegates to the layer function that already owns the answer — `top_insights`
calls the same `top_events` the digest does — so an agent cannot get a different
slate than a human. One deliberate asymmetry: `search_insights` is *not*
slate-filtered, so an agent can find what the slate suppressed. A read surface
that only shows the filtered view cannot audit the filter.

## The run as a graph

`graph.py` packages all twenty-two stages as a LangGraph `StateGraph`. No node
has a body of its own — each is one call into the layer function the CLI already
invokes, so there is no parallel code path to drift.

```bash
python -m fli.cli graph                 # free stages only
python -m fli.cli graph --spend         # offer the paid stages; pauses for approval
python -m fli.cli graph --spend --yes   # skip the pause (schedulers)
python -m fli.cli graph --mermaid       # print the topology, run nothing
```

**The paid stages need three things, and the third is a human.** `--spend` and
an API key are necessary but not sufficient: an `approve` node raises an
`interrupt` that pauses the run and prints what the work would touch
(`unaudited_claims`, from insights with no `claim_checks` row) before asking. A
flag is set before the operator knows what the run will find; this is decided
against the current database. One answer covers both paid segments. Declining is
not an error — the run continues on the free path. No tty and no `--yes` means
decline, not hang. Without `--spend` the gate resolves free **without pausing**.

Delivery runs *after* claim repair — the reverse order shipped digests citing
claims repair had already rewritten. The graph makes that structural.

With `FLI_TRACING=1` each node is a span and LLM calls nest inside it, so
Phoenix renders a run as `graph.run → node.<stage> → llm.<task>`.

## Corpus drift

The filter, the bake-off and the judge labels were all fitted against a corpus
with a particular shape; when that shape moves, the fits degrade with no
invariant breaking. `drift.py` measures it with PSI over categorical mixes and
a two-sample KS over continuous ones, computed from SQL with no scipy. PSI uses
the conventional 0.10/0.25 bands rather than house-tuned ones.

Two decisions: the window is anchored to the **newest document, not the wall
clock**, so the report is reproducible on a static corpus; and drift is
deliberately **not** part of `checks`, because an organic news cycle must not
turn the release gate red. It carries its own exit code so a scheduler can
still alarm.

## Fallbacks

Every external dependency degrades toward *less output*, never toward unverified
output.

| failure | behaviour |
|---|---|
| No API key | every deterministic stage runs; `checks` still green, digest still readable. Only new LLM work is skipped |
| Unknown model price | `cost_usd()` raises rather than defaulting to zero — a silent zero would make an expensive model look free in the table used to pick models |
| HTTP failure | 2 retries, 1s backoff, 20s timeout; after 3 consecutive failures the per-host circuit opens. Every outcome is a `fetch_log` row |
| JS-walled page | rendering proxy, then manual capture under the same immutability rules |
| Missing GBM library | LightGBM → XGBoost → sklearn's `HistGradientBoosting`; the winner names which ran |
| Missing Flask / LangGraph / MCP / matplotlib / OTel | lazily imported; only that command is unavailable. Tracing prints the install hint and continues |
| Provider outage | the second model family is already wired for Dawid–Skene |
| Unparseable model reply | `rejections` row (`classify_parse_error`, 10 in the corpus), never a retry-until-usable loop |

## Decisions worth knowing

- **Every tunable is in `config.py` or `policy.yml`**, never inline. Business
  judgements (what counts as decision-relevant, which holdings exist) are YAML
  with a named owner and a version; every scored event records the policy
  version that produced it.
- **Clustering is Jaccard over normalized claim tokens**, not embeddings. A
  vector store would be infrastructure carrying no measurement at this corpus
  size, and the threshold is measured rather than picked.
- **Lab identity is never a feature.** No `is_openai`. Per-lab precision is the
  fairness check, and labs with too few events are excluded rather than given a
  meaningless score.
- **`top_events` is the editorial boundary.** Scoring produces an ordering;
  everything a reader should not see — outside the window, duplicate cluster,
  same story, no mechanism, not entailed — is applied here and *reported* as a
  suppression count, not silently dropped.
- **`contributors` refuses to invent a person-quality formula.** No
  `papers × w1 + followers × w2` whose weights nobody can defend. A person's
  score aggregates already-validated event scores.
- **The X benchmark is frozen with no generator.** Regenerating would spend
  money to produce a *different* reference and silently invalidate every number
  measured against the old one.
- **The PDF writer has no dependencies.** Two hundred lines of PDF 1.4 against
  reportlab's install cost or weasyprint's system libraries; the three standard
  Helvetica faces are guaranteed present in every conforming viewer.
- **Mobility synthesis is schema-level**: a person observed at two labs in
  succession becomes a `personnel` event dated by its arrival, not by the
  inference.

## Data discipline

`data/fli.db` is the only database in the repo and the clean production state.
Validation experiments write their *results* into the reports rather than extra
rows into the live DB. Where evidence could not survive there — a
truncate-and-rebuild resets `fetch_log` to all-ok — the finding was lifted into
prose and the working binary discarded: a committed claim should rest on
something readable in a diff.

A run checkpoints the WAL back into the main file before committing, so a
committed DB never misses the writes of the run that produced it.
