# Frontier Lab Intelligence

## The system in one paragraph

The system tracks eight frontier AI labs (OpenAI, Anthropic, Google DeepMind,
Meta AI, Mistral, DeepSeek, Qwen, xAI) and the people inside them. It has
ingested 1,675 documents from official channels, extracted **954 evidence-backed
events** from them, resolved **285 researchers across 322 identities** on arXiv,
GitHub, X and lab pages, and ranks every event twice — once for an investment
reader, once for an engineering reader — from 2,735 pairwise labels (2,308 from
two model-judge families, 427 from a human). It then
connects lab events to public-equity holdings, writes a per-audience reading of
each, and delivers a cited digest and an alert path. The same intelligence is
readable four ways — CLI, Flask UI, a read-only MCP server for agent clients,
and a scheduled GitHub Actions run — all calling the same layer functions, so no
surface can show a different answer. The whole run, twenty-two stages with the
paid ones behind an approval the graph pauses on, is packaged as a LangGraph
graph with per-node tracing, and a free PSI/KS drift monitor watches whether the
corpus is still the corpus the models were fitted to. Total LLM spend to date is
**$29.87 across 8,707 calls** — including a $1.38 claim-repair pass that lifted
claim↔quote entailment from 52.0% to 97.5% (930/954 entailed).

## The headline finding: one ranking cannot serve two readers

The most important structural result is not any single event — it is that
"important" is not one thing. The same 954 events, the same features, the same
clustering, ranked under two audience rubrics, produce two orderings that share
**0 of their top 10** (8% of the top 25) and correlate at a Kendall τ of
**+0.128** — near zero. Read the two lists side by side and the reason is
obvious:

| rank | investment ranking | rank | engineering ranking |
|---:|---|---:|---|
| 1 | Mistral: Emmi AI acquihire — 30+ researchers and engineers | 1 | Google: DiffusionGemma, 26B MoE text-diffusion, Apache 2.0 |
| 2 | Mistral: 10 MW Les Ulis datacenter dedicated to inference | 2 | Google: DiffusionGemma weights on Hugging Face, MLX-servable |
| 3 | Meta: 28% ads-retrieval tail-latency cut, 3.28 MW saved | 3 | Qwen: AgentWorld-35B-A3B open-sourced, 256K context |
| 4 | Google: DiffusionGemma, 3.8B active of 26B, fits in 18 GB | 4 | OpenAI: moderation endpoints for responses/chat-completions |
| 5 | Google: Gemini Omni Flash at $0.10/second of video | 5 | OpenAI: Python SDK adds Amazon Bedrock Responses |
| 6 | Google: Grab pilots Gemini for driver–traveler translation | 6 | Google: Fitbit Air, smallest tracker, high-fidelity sensors |

The engineering top 10 opens with open-weight releases — its top three are all
`open_source` — and fills with shippable tooling: SDK support, moderation
endpoints, a robotics suite. The investment top 10 is `personnel`,
`infrastructure` and `commercial` — an acquihire, datacenters, video-token
pricing, enterprise adoption. Neither list would serve the other reader at all.
This is measured, not asserted, and it is the justification for the entire
two-persona design: a single "importance score" would have quietly served one
audience and mis-served the other.

*(Both columns are the ranking a reader actually gets: the persisted rank
collapses each cluster to one representative story and places events inside the
policy's 90-day window first, so the rows above are distinct, current stories.
One story — DiffusionGemma — is visible in both columns, and for different
reasons: the investment ranking carries its consumer-GPU cost profile, the
engineering ranking its Apache 2.0 weights. The overlap count is still 0 because
the two rubrics elevate different events even when they touch the same story.)*

## Insight 1 (investment) — OpenAI's Health launch is a direct threat to two holdings

Of 954 events and 54 holding-exposure edges, exactly two are signed as a
*threat* to a named public position — one edge to Hinge Health, one to Oscar
Health, both from OpenAI shipping Health inside ChatGPT (2026-07-23). The
system's reading, for a PM:

> OpenAI's Health in ChatGPT gives consumers a free, broad AI health companion
> that connects personal health data, competing for the same "personalized
> health guidance" use case that Hinge Health (HNGE) and Oscar Health (OSCR)
> monetize.

The reading rests on the lab's own words — "connect Apple Health and supported
medical records for personalized health" — not on a keyword. It is rated
*medium* confidence over *quarters*, which is the honest calibration: the feature
has shipped, but its effect on either holding's funnel is a plausible mechanism,
not a measured one. This is exactly the kind of private-lab-to-public-equity
connection the system exists to surface.

It also fired the alert path twice (`signed_position` and `signed_reading`, both
on 2026-07-30), and **neither trigger was the score.** That is deliberate, and
this event shows why: its rank on the investment ranking is not stable across
the bake-off contenders — 22nd under the shipped GBM, 14th under logistic, 37th
under hand-weights, 506th under the recency baseline. A rank-threshold alert
rule would have fired or not fired depending on which model won a bake-off, which is
no basis for waking a PM. So an alert fires on a *signed direction* — a
classifier-established position edge, or a persona reading at medium-or-better
confidence — and the `alerts` table's UNIQUE key means it fires exactly once.

## Insight 2 (investment) — the same channel points in opposite directions, and the system refuses to average them

Three datacenter-power events illustrate why direction is decided by *reading the
sentence*, not by the channel:

- **OpenAI's Project Camellia** — a 3.2 GW Georgia datacenter contracted through
  2028–2032 — is read as a **tailwind** for IREN (power/datacenter capacity as
  the AI bottleneck): "a large new commitment reinforces that scarcity narrative
  and demand growth."
- **Meta's kernel-scheduler optimization** — saving 3.28 megawatts of its own
  datacenter power through software — is read as a **threat** to the same thesis:
  large AI workloads can be served with *less* power per unit of output than
  assumed.
- **Gemma 4 12B**, matching a 26B model on under half the memory, is read as a
  **threat** to Micron (MU): "undercutting the 'more memory per model' demand
  driver."

All three share the `energy_datacenter` / `compute_memory` mechanism and point
opposite ways. The deterministic layer deliberately leaves every one of them
`unclear` and defers the sign to a reader, because averaging "building capacity"
and "saving capacity" into a channel-level direction would be confidently wrong.
This is the design decision that most distinguishes the system from a keyword
dashboard.

## Insight 3 (engineering) — the actionable frontier is efficiency and open weights, not benchmark records

For the engineering reader, the top of the ranking is dominated by *usable* and
*reproducible* work, exactly as the technical rubric intends — the **top 3 are
all `open_source` events** (DiffusionGemma's 26B MoE text-diffusion release
under Apache 2.0, its weights live on Hugging Face and servable via MLX, and
Qwen-AgentWorld-35B-A3B with a 256K context), and the rest of the top 10 is
shippable tooling: OpenAI's moderation endpoints and Bedrock-capable Python
SDK, Qwen-Robot Suite, Muse Spark 1.1 on OpenRouter. Corpus-wide, `open_source`
is only 50 of 954 events (5.2%), so putting all three of them at ranks 1–3 means
the technical ranking is concentrating on a thin slice of the corpus rather than
following volume. The system's readings are correspondingly
restrained: the dominant verdict is *investigate* ("spike it in a sandbox,
benchmark against what we run"), with *adopt* reserved for the rare case of
released weights plus a clear reason to switch. This restraint is the honest
shape of a frontier-lab feed — most of what is published is not something a team
can pick up this quarter — and the digest says so in as many words rather than
inflating every release into an action.

## Insight 4 (corpus) — lab coverage is 10× skewed by channel style, and the ranking corrects rather than amplifies it

DeepSeek publishes almost nothing on official channels (37 events in the
corpus); Google DeepMind publishes constantly (392). A volume-following score
would read that 10× coverage gap as a 10× importance gap. The measured result
is the opposite: in the investment top-50 by score, **DeepSeek is
over-represented at 3.1× its corpus share** (4% of events, 12% of the top 50) —
the ranking treats scarcity of publication as orthogonal to importance, so the
quiet lab's rare disclosures rank on their content. Two design choices produce
this: scoring is pairwise (an event competes against events, never against a
lab's volume), and the digest caps per-lab slate seats. The per-lab fairness
figure closes the loop — normalized by the headroom its high base rate leaves,
DeepSeek's lift-over-ceiling is 100%, tied with Google DeepMind at the top of
the table, despite the thinnest coverage in the corpus.

## Insight 5 (monitoring) — the drift monitor's first run found the engineering product's supply drying up

Drift monitoring (`python -m fli.cli drift`, PSI over categorical mixes and KS
over continuous ones) exists because a filter tuned on last month's corpus and a
ranker trained on last month's labels degrade *silently* when the input
distribution moves — no invariant breaks, the rankings just get worse. On the
committed corpus it reports **4 MAJOR of 4 metrics**, and one of them matters
commercially:

> **`open_source` events: 6.8% of the corpus historically, 0.0% in the last 14
> days.** Zero, against roughly 14 expected at the historical rate.

That is the event type supplying the **top 3 of the engineering ranking**. The
channel that usually carries open-weight announcements was not under-covered in
that window either — `blog` contributed 48 documents and 104 events, and blog is
historically the largest source of `open_source` events (26 of 50). So the drop
is not obviously a coverage artifact: the places these announcements normally
appear were being read, and carried none. The newest `open_source` event in the
corpus is Gemma 4 12B on 2026-07-01.

Two readings are available and **the system cannot currently distinguish them**:
either the labs genuinely paused open-weight releases in that window, or the
mix shifted in a way the extractor is sensitive to. Stating both is the honest
position, and it is a concrete open question rather than a shrug — what would
settle it is in future scope, §3.

What is not ambiguous is that the monitor earned its place. No
invariant check would ever have flagged this — the database is perfectly
consistent, C1–C20 are green, and the engineering digest still renders a
confident slate. The document-level metrics are scoped to content documents —
register pages fetched as identity evidence are excluded, exactly as the funnel
figure excludes them — so all four MAJOR readings are about the corpus the
extractor actually eats, not an artifact of the register mining that runs
alongside it. What they say is that the most recent ingest wave genuinely moved
the corpus mix, which is precisely the condition under which a ranker fitted
earlier deserves scrutiny — and the bake-off can be re-run in one command when
it does.

## The honest negatives

A system that only reported its wins would be untrustworthy for exactly the
readers it serves. Three results are worth stating plainly.

**GitHub and arXiv track disjoint populations.** After resolving 146 GitHub
identities and 112 arXiv identities and mining 40+ research repositories, the
overlap between the two populations is **zero**. The people who ship a lab's
client SDK are not the people who write its papers. This is a real finding about
how labs are structured, not a bug — but it means the "same person across three
platforms" link the register was built to make is rarer than hoped, and the
report says so.

**Most events reach no public position.** Only about **5%** of events (54
holding-exposure edges across 50 of 954 events) connect to a tracked holding
through any identifiable mechanism, and of those, **52 of 54 are `unclear`** —
exposure without an established direction. This is the correct default for a fund that
cannot trade the labs themselves, but it means the investment product is a small
number of high-conviction connections surrounded by an honest majority of "we
see it, we can't sign it", not a dense signal.

**The investment digest can be honestly empty, and this week it is.** The most
recent committed investment digest delivers **zero items**: no in-window event
both establishes a market mechanism and reaches a tracked holding. The slate
ledger says exactly where the corpus went instead — *"839 no mechanism, 42 thin
quote, 40 outside window, 16 no holding link, 14 not entailed, 3 undated"* — and
the digest renders that ledger rather than padding the slate with items the
policy would reject. A system tuned to look busy would have shipped ten
mechanism-free items; this one reports an empty week as an empty week. The
engineering digest for the same date carries 6 items, which is the two-persona
design behaving as measured: the same corpus week can be news for one reader and
noise for the other.

**The two model judges agree with each other more than with the human.** On the
363 investment pairs a human has now labelled, the two models (Claude and GPT)
agree with *each other* at Cohen's **κ 0.55** (77% raw), but each agrees with
the **human at only κ ≈ 0.29** (62% raw). Part of that gap is engineered: the
labeling CLI deliberately routes human sittings to the pairs where the judges
disagree or the model's posterior sits nearest 0.5, so over half the human
reference now lives on the hardest pairs in the corpus — on those queue-routed
pairs alone κ ≈ 0.21, on the broader earlier sittings κ ≈ 0.35. The trap this
exposes was measured earlier at the 89-pair mark: the Dawid–Skene estimate —
which infers accuracy from disagreement — rated the human at 0.843 and both
models at 0.870, i.e. it ranked the two models *above* the human, precisely
because they agree with each other. Two models sharing a training prior
corroborate each other, and a disagreement-based reliability score reads that
corroboration as accuracy. Only an independent human reference exposes it. The
system now refuses to compute Dawid–Skene without at least two independent model
*families*, and reports the human agreement alongside the model estimate rather
than trusting the models' mutual agreement. The current corpus makes the trap
starker still: with the human reference now concentrated on judge-disagreement
pairs, Dawid–Skene rates the human at 0.778 — the more the human works exactly
where the models fail, the less reliable a disagreement-based score believes
the human to be. The practical reading: the models
are a usable ranking signal (κ 0.35 with a human on typical pairs is real
agreement, not noise), but they are not ground truth, and the highest-value
labels to buy next are human ones. The winner model's ranking decisions now
agree with **0.684 of the 323 decided human investment pairs** (and 0.849 of the
53 technical ones) — reported in the bake-off as a fully out-of-sample audit,
since human labels never train the model. That it sits below the 0.768 measured
against the LLM reference is the expected and correct direction: agreeing with
the judge that trained you is easier than agreeing with a person.

**The talent-mobility path is built but unwitnessed.** The system can synthesize
a personnel event from a researcher observed at two labs in succession, and the
test suite plants such a move and shows it reach the digest; the live corpus has
simply not yet witnessed one on the weekly re-observation cadence. All 10
`personnel` events in the corpus came from document extraction, none from
affiliation history.

What the corpus cannot yet show, a rank probe can. Feeding a synthetic move —
median feature shape of the 10 extracted personnel events, dated two days ago —
through the same scoring path the bake-off ships puts it at **investment #46 of
954 (top 5%) and technical #953 of 954**. Nothing is written; it is an
in-memory probe reported in the mobility figure. So the mechanism is real, the ranking would
surface it to the reader who wants it, and the two rubrics disagree exactly as
they should: a researcher move is an investment signal, not a technical one.
Only the observation is missing.

**The lab with the weakest ranking lift was investigated, and the gap is
composition, not bias.** An earlier bake-off showed Meta AI with the lowest
per-lab lift (+0.05 on a 0.55 base rate), which looks like the ranking
treating one lab worse. Three measured checks said otherwise. First, judge
quality is *better* on Meta pairs, not worse: on pairs a human also labelled,
human–judge agreement is 0.761 when the pair touches a Meta event versus
0.696 when it does not — the labels are not the problem. Second, the gap is
composition: 44% of Meta's events are `infrastructure` engineering posts
(against 7% corpus-wide), exactly the feature shape — official channel, high
specificity, no market mechanism — that the investment rubric tells judges to
rank low but that the feature surface used to reward. Third, the fix helped:
after adding a `mechanism_channel` feature (does the event's own quote
establish a market mechanism?) and several hundred more judge labels, Meta's
lift recovered from +0.05 to **+0.30 (on a 0.40 base, n=40)**.

On the refreshed corpus Meta is nonetheless the weakest lab again, and the
diagnosis reads the same as before the fix: its top-10 misses are
`infrastructure` ×2 and `release` ×1 — the identical
official-channel-engineering-post shape. That the recovery held (+0.05 → +0.30)
while the *relative* position regressed as the corpus grew is the useful
result. It says this is a *feature surface* limitation that moves with corpus
composition, not a lab the model dislikes — and lab identity is never a
feature, so it structurally cannot be. Most of the remaining spread is ceiling
arithmetic: lift is bounded by 1−base, so the figure reports both lift and
lift-over-ceiling (Google DeepMind 100%, DeepSeek 100%, Qwen 69%, Mistral 68%,
OpenAI 58%, Anthropic 53%, Meta AI 50%) and this class of question can be
answered by reading rather than re-investigating.

## Where the signal is strongest

The learning curve has flattened — the last step, 160→320 pairs, moved held-out
accuracy just +0.004 — so the next unit of accuracy will not come from buying
more of the same LLM labels; it will come from the feature surface and from
human labels, which measure a different thing than the judges do. One honest
wrinkle: headline held-out accuracy has not risen monotonically as labels were
added. It has been as high as 0.879 on an earlier, smaller label set and
currently sits at 0.768 on 760 — not because the model got worse, but because
newer labels deliberately cover harder, more diverse pairs; the early number was
flattered by an easier test set. Cost still points one way for whatever labels
*are* bought: GPT-5.2 produces a usable label for $0.0022 against Sonnet's
$0.0101 with marginally *higher* estimated reliability (0.874 vs 0.864). The
investment product is sharpest where a lab enters a market a holding occupies
(displacement, which carries an inherent sign) and weakest on demand channels
(where the sign needs a per-event reading). The engineering product looks dense,
because "usable and reproducible" is a property the corpus carries on its face —
but the drift monitor is the caveat on that sentence: its supply of
`open_source` events went to zero in the most recent window, and because the
ranking is windowed, that drought shows up directly in what the digest can
serve.

Everything still open is collected in the next section rather than scattered
through the document.

## How it was validated

Four questions, in order: how extraction quality was measured, how hallucination
was controlled, how the scoring was validated, and what stands in for ground
truth when none exists.

The figures live in `docs/evaluation-report.md`, regenerated by
`python3 -m fli.cli evaluate` — database only, no API key, $0. Two consecutive
runs on the committed database are byte-identical, so every number below is
reproducible from a clone.

### Metric tiers

The single most common way an evaluation lies is by reporting "accuracy" against
a reference that is itself the thing being tested. Every figure in this system
therefore carries a tier, and the tier is printed in the report next to the
number:

| tier | meaning | example |
|---|---|---|
| **MECHANICAL** | arithmetic over the database; no human, no judge | funnel, cost, event-type mix |
| **SYNTHETIC** | ground truth known by construction | planted-policy recovery |
| **vs HUMAN REFERENCE** | agreement with a stated, frozen human-audited set | channel classifier, slate precision |
| **JUDGED** | against an unaudited LLM reference — provisional | bake-off, ablation, fairness |

F1, precision and recall appear **only** in the SYNTHETIC and HUMAN tiers. On
real data with no gold standard the report says *agreement*, never *accuracy*.
That distinction is the reason the numbers below can be trusted at all.

---

### 1. Extraction quality

Extraction turns one document into one or more events, each a `claim` (the
model's sentence) plus a `quote` (the source's own contiguous words). The quote
is what makes the claim checkable, so quote fidelity is the metric.

**Quote verification — 94.8%.** Every proposed quote is matched against the
stored bytes of its source document before the insight is allowed to persist.

| kept insights | rejected quotes | verified |
|---:|---:|---:|
| 954 | 52 | **94.8%** |

**This number used to read 99.8%, and that was a bug, not an achievement.**
Failed quotes were silently `continue`d in the extraction loop, so they were
never written to `rejections` — the rate was high because failures had stopped
being *counted*, not because they had stopped happening. Per-insight
verification logging was added (`quote_unverified`, 52 rows in `rejections`) and
the honest number fell below 95%. It is reported that way deliberately: a
verification metric that cannot go down is not measuring anything.

**Verification tier — 954/954 exact.** Every insight quote verifies at the
strictest tier, byte-verbatim under the shared normalization. Check **C19**
fails the build if this is ever below 100%, so "exact" is an enforced invariant
rather than an observed statistic. (The corpus's 1,459 `structural`-tier
evidence rows carry no claims — they are the register's author-name spans in
structured documents, where structural *is* the designed tier.)

**Quote-length compliance — 93.4%.** The prompt specifies a 10–60 word quote;
891 of 954 land in spec. The 51 under-length quotes are the interesting tail:
short quotes are where a claim most often outruns its evidence, which is exactly
what the entailment check below is for.

**The rejection ledger.** The 52 rejected quotes were characterized rather than
just counted, because "5% failed" says nothing about whether the model was
lying or the matcher was fussy:

| kind | n | reading |
|---:|---:|---|
| ≥80%-contiguous near-miss | 25 | the verifier being stricter than the normalization — not model failure |
| paraphrase | 25 | the model rewrote instead of copying — a real prompt failure |
| fabrication (no anchor in source) | **2** | the actual hallucination floor |

Two fabrications in 1,006 proposed insights is the number that matters, and none
of the 52 were ever shown to a reader — they were rejected at the gate.

### 2. Hallucination control

Byte-verification proves the quote is real. It does not prove the *claim*
follows from the quote — a model can copy a sentence correctly and still
summarize it wrongly. So there is a second, independent check.

**Claim↔quote entailment — 97.5%.** Every one of the 954 insights was
re-judged with the claim and its quote *alone*, no document context:

| verdict | n | % |
|---|---:|---:|
| entailed | 930 | 97.5% |
| partial | 10 | 1.0% |
| not entailed | 14 | 1.5% |

`partial` is the actionable class: the claim names a load-bearing fact — a
number, a date, an actor — that the quote does not carry. That is a prompt
defect, and it was treated as one.

**The prompt fix was measured, not assumed.** A quote-first instruction block
was added to the extraction prompt and A/B tested on the 30 hardest documents
(sampled with seed 7 from documents that already had ≥1 partial verdict, judged
by the same entailment model): **partial rate 65.6% → 47.9%**. The revision
applies to new extractions, so the corpus figures above are the *pre-revision*
audit and are reported as such rather than being quietly re-run.

**The repair pass.** Claim faithfulness was the weakest number in the whole
evaluation before it was fixed. `verify --repair` rewrites every `partial` claim
constrained to its quote alone, logs the superseded claim to `rejections`, and
re-verifies in the same pass:

| | entailed | partial | not entailed |
|---|---:|---:|---:|
| before repair | 382 (52.0%) | 341 | 11 |
| after repair ($1.38 across the corpus) | **930 (97.5%)** | 10 | 14 |

The important property is the direction: repair only ever makes a claim
*weaker*. It cannot add a fact, because it is given nothing but the quote. The
14 `not_entailed` claims were not repaired away — they are gated out of every
slate, so no reader sees them.

**Structural controls, not just measurement.** `insights.evidence_id` is
`NOT NULL`; there is no code path that writes an insight without evidence.
Checks **C1** and **C2** re-hash every stored document and re-verify every quote
on *every* run, so a source that silently edits a page surfaces as a failure
rather than leaving a stale claim in place. This is the difference between
verifying at write time and staying verified.

### 3. Scoring validation

#### The bake-off

Five contenders train on the same pairwise labels and are compared on the same
seeded held-out split. The hand-weighted sum is included as a **baseline to
beat, never as the shipped scorer**: an arbitrary weighted sum dressed up as a
score is not a defensible ranking, and the measurement below is why.

**Investment rubric** — 760 usable labels, 224 held-out pairs, 323 human-labeled
pairs available as an out-of-sample audit:

| model | held-out acc (LLM ref) | human acc (out-of-sample) | p@10 held-out | nDCG@20 held-out |
|---|---:|---:|---:|---:|
| **gbm_sklearn** (winner) | **0.768** | **0.684** | 0.90 | 0.232 |
| logistic | 0.763 | — | 1.00 | 0.325 |
| baseline_corroboration | 0.491 | — | 0.20 | 0.074 |
| baseline_recency | 0.478 | — | 0.30 | 0.049 |
| hand_weights | 0.424 | — | 0.30 | 0.111 |

**Technical rubric** — 963 usable labels, 294 held-out pairs, 53 human pairs:

| model | held-out acc | human acc | p@10 held-out | nDCG@20 held-out |
|---|---:|---:|---:|---:|
| **gbm_sklearn** (winner) | **0.827** | **0.849** | 1.00 | 0.675 |
| logistic | 0.806 | — | 1.00 | 0.590 |
| hand_weights | 0.412 | — | 0.40 | 0.109 |
| baseline_corroboration | 0.408 | — | 0.50 | 0.197 |
| baseline_recency | 0.347 | — | 0.30 | 0.063 |

**How this result would be reported was also fixed in advance.** The underlying
question is uncomfortable: *how much of a written editorial policy is
recoverable from a handful of cheap numeric features?* The labeler reads the
full document; the ranker sees twenty numbers. There was no guarantee the gap
was closable, so the reporting rule was written before the bake-off ran:

| held-out accuracy vs best baseline | how it gets reported |
|---|---|
| clearly better (≥ +0.10) | ship it; lead with the coefficients and what they say |
| marginal (+0.02 to +0.10) | ship it, but state plainly the margin is within what this many labels can resolve |
| no better (< +0.02) | **lead with the negative result**, ship the simplest baseline that matches it |

The measured margin is **+0.277** (0.768 against the best baseline's 0.491), so
the first row applies and the coefficients are reported below. Had the third row
happened, the headline of the evaluation would have been "twenty features do not
capture this policy" — and that was committed to before the number existed.

Three things worth reading off these tables:

**The hand-weighted sum is worse than chance.** 0.424 and 0.412 held-out, on a
task where chance is 0.500. A plausible-looking set of weights chosen by an
engineer does not merely underperform a fitted model — it fails to rank at all.
Shipping one and calling it a score would have been worse than useless, and the
only reason that is known is that it was benchmarked instead of assumed.

**`human_acc` is the non-circular number.** Human labels never enter training,
so this column is a fully out-of-sample audit against a different labeler
population. The winner scores **0.684** (investment) and **0.849** (technical)
against a human. Both are well above the baselines, and the investment number is
lower than the LLM-reference accuracy — which is the correct and expected
direction: agreeing with the judge that trained you is easier than agreeing with
a person.

**Each rubric selects its winner independently, and the selection has moved.**
An earlier bake-off picked logistic for the technical rubric while investment
picked the GBM; on the current label set the GBM wins both. Nothing forces one
model family on both audiences — the point of the bake-off is that the choice
is re-earned on every re-fit rather than fixed by preference.

#### Does it generalize?

**Overfitting** — largest train/held-out gap is `gbm_sklearn` at +0.109,
and the figure flags it as **memorising**: the training accuracy is not evidence
of anything, and only the held-out number above should be read. The gap grew as
the label set grew harder, which is the expected trade — the GBM buys its
held-out edge with capacity, and the figure exists so that trade stays visible
rather than flattering.

**Learning curve** — the last step, 160→320 pairs, moved accuracy +0.004:
**the curve has plateaued.** More labels from the same judges will not move the
number; the features are the limit. This is also the honest
counterweight to the headline: as the label set grew, accuracy did not rise
monotonically, because newer labels deliberately cover harder pairs. An early
number computed on an easier test set flattered the model.

**Ablation, reported with its limits.** Leave-one-feature-out moves
held-out accuracy by at most +0.022 (`mechanism_channel`), i.e. **five pairs out
of 224**, with `source_type_github` next at +0.018. The honest conclusion is not
"two features matter" but "**at 760 labels the ablation is coarse**" — most
features move nothing detectable at this resolution. The fitted
logistic coefficients are more informative than the ablation deltas:

| feature | coefficient |
|---|---:|
| `mechanism_channel` | +0.798 |
| `specificity` | +0.456 |
| `corroboration` | +0.368 |
| `event_type_release` | +0.364 |
| `source_type_github` | −0.328 |
| `event_type_benchmark` | −0.488 |

The largest positive weight is "does this event's own quote establish a market
transmission mechanism?" — which is what an investment reader should reward, and
it was added *because* a fairness investigation said the feature surface was
missing it.

#### Fairness

Lab identity is never a feature. Per-lab precision@10 is the check, but raw
p@10 tracks each lab's base rate, so **lift over base rate** is the fairness
number — and lift is itself ceilinged at `1 − base`, so lift/ceiling is the only
comparable figure across labs:

| lab | base | p@10 | lift | lift/ceiling |
|---|---:|---:|---:|---:|
| Google DeepMind | 0.40 | 1.00 | +0.60 | 100% |
| DeepSeek | 0.66 | 1.00 | +0.34 | 100% |
| Qwen | 0.35 | 0.80 | +0.45 | 69% |
| Mistral | 0.37 | 0.80 | +0.43 | 68% |
| OpenAI | 0.28 | 0.70 | +0.42 | 58% |
| Anthropic | 0.37 | 0.70 | +0.33 | 53% |
| Meta AI | 0.40 | 0.70 | +0.30 | 50% |

xAI is excluded rather than scored: 2 events, and a ratio on 2 events is noise.
Excluding a lab is more honest than printing a number for it.

The weakest lift is Meta AI (+0.30 on a 0.40 base, n=40), and its top-10 misses
are `infrastructure` ×2 and `release` ×1 — official-channel engineering posts
whose feature shape (official source, high specificity) the score rewards but
the judges call irrelevant. That is a feature-shape gap, not lab bias. The same
diagnosis applied to Meta before `mechanism_channel` was added, when its lift
was +0.05; the fix moved it to +0.30, and that recovery held even as the corpus
grew and returned Meta to the bottom of the (now tighter) table — evidence the
diagnosis was right about the mechanism even though composition still drives the
rank.

#### Does the machinery work at all?

Every number above is measured against a judge that is not ground truth. So one
figure removes that dependency entirely.

**Planted-policy recovery — SYNTHETIC.** Four known weight vectors are
planted over the *real* standardized feature matrix (real, so recovery must
survive the actual feature correlations), 400 pairwise labels are generated from
each with **10% of verdicts flipped** — the same order of unreliability measured
in the real judge — and the bake-off's own training path is asked for the
planted ranking back:

| planted policy | AUC | F1 |
|---|---:|---:|
| single_feature | 1.00 | 0.95 |
| hand_shape | 0.97 | 0.92 |
| dense_mixed | 0.96 | 0.88 |
| anti_prior | 0.98 | 0.92 |

`anti_prior` puts a **negative** weight on recency, so no baseline shape
recovers it by luck; its recovery shows the machinery follows the labels rather
than the priors.

This validates the *learner* — not the features, and not the policy. It says
that *if* a policy is expressible in this feature space, the pipeline will
recover it from a few hundred noisy judgements. It says nothing about whether
twenty surface features can express what a real PM means by "important"; that
question belongs to the ablation above and to the human references below.
Reaching that boundary from evidence is a better outcome than a ranker with an
unfalsifiable accuracy number.

### 4. The ground-truth approach

No labelled dataset of "important frontier-lab events" exists, and the two
obvious substitutes are both weak:

- **Hand-labelling a few hundred pairs.** Labels supplied by whoever built the
  system are an engineer's opinion, not the fund's editorial judgement, and
  nothing about the ranking can then be defended by pointing at anything except
  that opinion.
- **Using an LLM as judge and stopping there.** It moves the unanswered
  question up one level rather than answering it: how good is the judge? For a
  fund already running >1bn tokens a month across 30+ LLM processes, an
  unvalidated judge is not evidence of anything.

So the position taken is:

> **Do not claim the policy. Prove the instrument.**

"Importance" is a *stated editorial policy*, owned by a named domain expert and
versioned as configuration (`config/policy.yml`, `config/rubrics/*.yml`). What
an engineer can legitimately validate is the machinery that turns a person's
judgements into a ranker — that it is identifiable, sample-efficient and
noise-tolerant. That validation needs no finance knowledge at all, because the
ground truth is planted. Four independent references stand in for gold labels.

#### (a) The pairwise judge, and why it is not trusted

Importance is elicited **pairwise** — "which of these two ranks higher, and
which rubric rule decided it?" — because scoring an event in isolation is not a
question anyone can answer consistently. The judge must cite a rule number, so
every verdict is auditable, and it must return a confidence: `low` means "I
effectively guessed", and those verdicts are **excluded from training** (585
low-confidence LLM verdicts dropped from the investment pool, leaving 760 usable
labels). Keeping coin-flips out is worth more than the label
volume they would add.

The lab name is withheld from the judge, presentation order is randomised per
pair and un-swapped on store, and labels are namespaced
`llm:<model>/<rubric>/r<version>` so judgements made under different rubrics are
never pooled.

#### (b) Reliability from disagreement — and the trap it hides

Dawid–Skene estimates each labeler's accuracy from their disagreement alone, on
462 investment pairs across three independent families — restricted to pairs
voted by at least two families, since a pair only one family saw carries no
disagreement to learn from:

| labeler | estimated accuracy |
|---|---:|
| `llm:gpt-5.2/investment/r1` | 0.874 |
| `llm:claude-sonnet-5/investment/r1` | 0.864 |
| `human:soham/investment/r1` | 0.778 |

**The human scoring lowest is the most important result in the evaluation, and it
is not a finding about the human.** Two things produce it:

*Adverse selection.* The labeling CLI deliberately routes human sittings to the
pairs where the judges disagree or the model's posterior sits nearest 0.5. Over
half the human reference therefore lives on the hardest pairs in the corpus,
while the LLM labelers vote on the whole random sample. The two numbers are not
on the same difficulty scale.

*Correlated priors.* Two models sharing a training prior agree with each other,
and a disagreement-based estimator reads that mutual corroboration as accuracy.
Measured directly with Cohen's κ:

| pair | pairs | raw agreement | Cohen's κ |
|---|---:|---:|---:|
| Claude ↔ GPT-5.2 | 462 | 0.773 | **0.546** (moderate) |
| Claude ↔ human | 363 | 0.617 | 0.288 (fair) |
| GPT-5.2 ↔ human | 363 | 0.623 | 0.290 (fair) |

The two models agree with *each other* roughly twice as strongly as either
agrees with the person. An earlier run at the 89-pair mark made the trap
explicit: Dawid–Skene rated both models (0.870) *above* the human (0.843).
The system now **refuses to compute Dawid–Skene from a single model family**
(three prompt variants of one model agreed 92–100% and the estimator rated them
all ~0.99 — an artifact it will no longer produce), and always reports human
agreement alongside the model estimate rather than in place of it.

The practical reading: the models are a usable ranking signal — κ 0.29 with a
human on the *hardest* pairs is real agreement, not noise — but they are not
ground truth, and the highest-value labels to buy next are human ones.

#### (c) A frozen human-audited benchmark, with the verdict pre-registered

The mechanism classifier that connects events to holdings is scored against a
frozen, human-audited reference set of 100 X posts.

**The threshold was fixed before the classifier existed.** The first live X call
returned 29 real posts for $0.175; those were frozen to
`fixtures/x-benchmark-29-frozen.json` — a JSON fixture rather than a database,
so the whole experiment re-runs offline at zero cost forever. There is
deliberately no generator to rebuild it: regenerating would spend money to
produce a *different* reference and silently invalidate every number measured
against the old one. Four hypotheses were then
written down *before any code was changed*, on the principle that a result you
can interpret after seeing it is not a result. The decisive one was:

> Even after the lexicon is repaired, channel-assignment F1 stays below
> **0.80**, because channel membership is semantic ("does this move a number in
> a thesis?") and not lexical.
> **If F1 ≥ 0.80 the lexicon is sufficient — ship it and build no classifier**,
> and write that up as the result. If F1 < 0.80 the bottleneck is
> representational; build the classifier and report the lexicon's F1 as the
> baseline it must beat. Cost: $0.

That commitment is what makes the eventual classifier defensible rather than
self-serving: a cheap deterministic matcher beating an LLM would have been the
*better* outcome, and it was pre-committed that the classifier would not get
built regardless of how much more interesting it would be to discuss. The
lexicon lost on its own pre-registered terms.

Human auditing is why the benchmark exists rather than being a cache of LLM
verdicts. Measured against its own kind — LLM-judged labels, self-consistent —
the classifier looked healthy; audited against a person it did not, and the gap
was only visible because a human reference existed. The benchmark was grown to
100 labels **behind an audit gate**: new posts enter classifier-seeded but are
excluded from scoring until a human reviews them, so the model can never grade
its own homework.

On the current reference, under policy v3: **classifier F1 0.444 vs lexicon
0.267**. The classifier's remaining errors are the safe kind (missed mechanisms);
the lexicon's are the dangerous kind — it once flagged a phone codec that
"increased power usage" as a datacenter signal.

#### (d) Precision@k on the delivered product

The final reference is the only one that measures what a reader actually
receives. Every item in a delivered digest was marked keep or cut by a human
(`digest --review`):

| persona | kept | precision@k |
|---|---|---:|
| ai_team | 12/14 | **86%** |
| investment | 10/19 | **53%** |

The investment number is the system's weakest honest result, and it corroborates
the fairness figure from an independent human read: the cuts are the same
failure class — vendor case studies and official-channel engineering posts whose
feature shape the score rewards but the reader rejects.

### 5. Corpus drift

Everything above measures the models against labels. None of it notices if the
*input distribution* moves — and when it does, a filter tuned on last month's
mix and a ranker trained on last month's labels degrade silently. No invariant
breaks; the rankings just get quietly worse.

`python -m fli.cli drift` measures the movement between a recent window and the
corpus history, using PSI over categorical mixes and a two-sample KS test over
continuous distributions. Both are computed directly (no scipy), both are
unit-tested against hand-computed values, and PSI uses the conventional
0.10/0.25 banking bands rather than house-tuned thresholds so the numbers mean
what they mean in the literature. The window is anchored to the **newest
document rather than the wall clock**, so the report is reproducible on a static
corpus.

On the committed database — last 14 days (from 2026-07-18) vs everything before,
both scoped to content documents (register identity-evidence pages are
excluded, exactly as the funnel figure excludes them):

| metric | kind | value | threshold | n_ref | n_cur | verdict |
|---|---|---:|---:|---:|---:|---|
| doc `source_type` mix | PSI | 0.679 | 0.250 | 937 | 373 | **MAJOR** |
| insight `event_type` mix | PSI | 0.563 | 0.250 | 735 | 206 | **MAJOR** |
| doc length | KS | 0.257 | 0.083 | 937 | 373 | **MAJOR** |
| insight score | KS | 0.187 | 0.107 | 735 | 206 | **MAJOR** |

**4 MAJOR of 4**, and the causes are legible rather than mysterious:

| | history | last 14d |
|---|---:|---:|
| `social` documents | 61.4% | 34.6% |
| `arxiv` documents | 11.2% | **43.4%** |
| `open_source` events | 6.8% | **0.0%** |
| `commercial` events | 10.9% | 18.9% |
| `release` events | 31.2% | 28.2% |

arXiv nearly quadrupled its share of content documents — the most recent ingest
wave leaned on the author-query feeds — which also explains the document-length
shift, since an arXiv abstract page is a different size from a tweet. Neither is
a defect; both are exactly what the monitor is for, because a filter tuned on
the old mix is now running on a different one.

The `open_source` collapse is a genuine finding and is discussed in the final
report: **the event type that supplies the top of the engineering ranking
produced zero events in the most recent window.** The drift monitor caught a
real problem that no invariant check would ever have flagged.

That all four metrics read MAJOR — including the score distribution — is the
consistent story: the newest ingest wave genuinely moved the corpus, and the
monitor says so instead of averaging it away. This is the condition under which
the bake-off deserves a re-run, and re-running it is one free command.

Drift is deliberately **not** part of the `checks` battery. An organic news
cycle must not turn the release gate red. It exits with the count of MAJOR
drifts so a scheduler can alarm on it, and it runs as a free, informational node
inside the graph.

### What the evaluation does not establish

Stated here rather than left for a reader to discover.

- **The judged tier is provisional.** Bake-off, ablation and fairness are all
  measured against an unaudited LLM reference. `human_acc` is the corrective,
  and on the investment rubric it is lower.
- **The ablation is coarse** at 760 labels — the largest delta is five pairs.
- **Per-lab n is small** (dozens of events per lab). A p@10 on a few dozen
  events is a noisy statistic even after the lift correction.
- **The winner memorises.** The GBM's train/held-out gap is +0.109; its held-out
  edge over logistic is real but small, and the training numbers should never be
  quoted.
- **The back corpus pre-dates the quote-first prompt fix** that was measured to
  improve extraction; the repair pass compensates downstream (97.5% entailed),
  but the extraction-side fix applies only to new documents.
- **The channel benchmark is 100 posts**, one annotator, one policy version.
- **No gold labels exist for extraction.** Quote verification is mechanical and
  entailment is LLM-judged; neither is a human-audited extraction reference.
  That is the single largest gap in the evaluation.

What it would take to close each of these is collected under
[future scope](#future-scope) rather than repeated here.

## Future scope

Every known gap, deferred decision and unimplemented capability in the system,
in one place. Nothing here is a surprise found late: each item is the direct
consequence of a measurement reported above, and each names what it would take.

### 1. Labels — where the remaining accuracy is

| | |
|---|---|
| **Spend on features and human labels, not more LLM labels** | The learning curve has plateaued: the last doubling of pairs moved held-out accuracy +0.004. More labels from the same judges will buy noise; the remaining accuracy lives in the feature surface (the per-lab misses are all one feature shape) and in human labels, which measure reader preference rather than judge consensus. When labels are bought, GPT-5.2 costs $0.0022 per usable label against Sonnet's $0.0101, returns fewer low-confidence verdicts (22.0% vs 27.0%), and its estimated reliability is marginally *higher* (0.874 vs 0.864). |
| **Then buy human labels on the investment rubric** | Where the two models' shared blind spot is largest and slate precision is weakest (53% kept, against 86% for `ai_team`). |
| **Human labels on the technical rubric** | 64 human sittings against 363 for investment, so the technical `human_acc` of 0.849 rests on a much thinner reference (53 decided pairs) than its investment counterpart. |
| **A human-audited extraction reference** | The single largest evaluation gap. Quote verification is mechanical and entailment is LLM-judged; there is no gold extraction set. Everything in the JUDGED tier stays provisional until there is one. |

### 2. Cost

| | |
|---|---|
| **Batch the two lines that matter** | The `--batch` path (flat 50% off) has carried 462 of 8,707 calls — `verify`, `faithfulness` and `channel` — saving $0.24. `judge` ($17.28) and `extract` ($6.14) are 78% of all spend and have never been through it. Routing them through it on any re-run is worth roughly $11; the paid stages are all embarrassingly parallel. |

### 3. Corpus and coverage

| | |
|---|---|
| **Resolve the `open_source` question** | 6.8% of the corpus historically, 0.0% in the last 14 days — and it supplies the top 3 of the engineering ranking. Genuine pause or extraction-side artifact is not yet decidable; it needs a longer window and a per-lab release-feed check. The one open question here with a direct product consequence. |
| **Witness a live talent move** | The synthesis path is verified end to end in the suite, but re-observation runs on a 7-day cadence and the first observation landed 2026-07-30, so the earliest a real move can be witnessed is 2026-08-06. Waiting, not building. |
| **Deepen person attribution** | 61 of 954 events reach a person (17 attributed to a person as the event's subject). The register, expansion and approval machinery works; official channels simply rarely name individuals outside paper bylines. GitHub and arXiv also track disjoint populations — the measured overlap is zero. |
| **Re-extract the back corpus** | The quote-first prompt revision cut the partial-entailment rate on the 30 hardest documents from 65.6% to 47.9%, but it applies only to new extractions, so part of the corpus predates it; the repair pass has since brought corpus-wide entailment to 97.5%. |
| **Grow the channel benchmark** | 100 posts, one annotator, one policy version. |

### 4. Deployment — unblocked, not done

The system runs four ways today — CLI, Flask UI, MCP server, and a scheduled
GitHub Actions job — and ships a `Dockerfile`, so it runs without a Python
environment at all. What remains is unimplemented but
unblocked: state is one SQLite file, the runtime is plain Python, and model
routing is a single dictionary in `fli/ops/llm.py`.

| | |
|---|---|
| **Vertex AI / Bedrock inference** | A provider entry in `fli/ops/llm.py` — `KEY_ENV`, `provider_for` and `PRICES` — rather than a rewrite. The OpenAI path already proves the second-provider shape works. |
| **Durable checkpointing** | The graph compiles with an `InMemorySaver`, which is sufficient because the approval pause and its resume happen in one CLI process. A long-running or distributed runner would need a real checkpointer. |

### 5. Governance

| | |
|---|---|
| **The policy has no domain owner** | `config/policy.yml` reads `owner: "BIT PM — unassigned"`, and the system prints `[NOT REVIEWED]` on every run rather than hiding it. Every weight in it is provisional until a portfolio manager signs off. This is not an engineering task, and it is the item that most limits what the scoring can claim. |
