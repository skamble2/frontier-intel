# Design notes

Why the code is shaped the way it is. Every non-obvious decision in `fli/` is
recorded here, keyed by module, so the source can stay short.

Each entry states the decision and the measurement or failure behind it.
Numbers are from the committed database and are reproducible with
`python3 -m fli.cli evaluate`.

- [Layering](#layering)
- [core](#core)
- [ingestion](#ingestion)
- [knowledge](#knowledge)
- [register](#register)
- [intelligence](#intelligence)
- [delivery](#delivery)
- [validation](#validation)
- [ops](#ops)

---

## Layering

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

## core

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

## ingestion

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

## knowledge

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

## register

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

## intelligence

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

## delivery

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

What it refuses to do: `unclear` items are not hidden — 57 of 59 position edges
are `unclear` by design, and showing only the two signed ones would imply the
system knows more than it does. Coverage is stated in the header, including how
many items carry no reading at all.

**`alerts.py` — the push path.** A digest is opened when the reader chooses; an
alert interrupts. So the bar is not "interesting" — the digest already carries
everything interesting — it is "a reader would want to know before the next
report".

**The trigger is the reading, not the rank.** The obvious rule is a score
threshold and it is wrong here: the two events carrying a signed reading score
-0.02 and 1.13 against a p90 of 1.59. OpenAI launching Health in ChatGPT — the
one event in 734 the deterministic layer calls a threat to a named holding —
sits near the *middle* of the ranking, because the rubric rewards specificity and
shipped-ness, not portfolio consequence. A p90 rule would have missed it and
fired on ten model releases instead.

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

## validation

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

## ops

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

## storage

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
