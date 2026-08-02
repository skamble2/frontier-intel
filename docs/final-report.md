# Frontier Lab Intelligence — final report

This is the reader-facing summary of what the system found. It is written for
the two audiences the system serves — an investment team and an engineering team
— and it is deliberately honest about the shape of the signal, including where
there is less of it than a demo would imply. Every figure below is reproducible
from the committed database; every event named carries a verbatim quote and a
source link in the system's own output.

## What was built, in one paragraph

The system tracks eight frontier AI labs (OpenAI, Anthropic, Google DeepMind,
Meta AI, Mistral, DeepSeek, Qwen, xAI) and the people inside them. It has
ingested 1,541 documents from official channels, extracted **734 evidence-backed
events** from them, resolved **285 researchers across 322 identities** on arXiv,
GitHub, X and lab pages, and ranks every event twice — once for an investment
reader, once for an engineering reader — from 2,165 pairwise labels (1,738 from
two model-judge families, 427 from a human). It then
connects lab events to public-equity holdings, writes a per-audience reading of
each, and delivers a cited digest and an alert path. The same intelligence is
readable four ways — CLI, Flask UI, a read-only MCP server for agent clients,
and a scheduled GitHub Actions run — all calling the same layer functions, so no
surface can show a different answer. The whole run, twenty-two stages with the
paid ones behind an approval the graph pauses on, is packaged as a LangGraph
graph with per-node tracing, and a free PSI/KS drift monitor watches whether the
corpus is still the corpus the models were fitted to. Total LLM spend to date is
**$25.18 across 7,423 calls** — including a $1.05 claim-repair pass that lifted
claim↔quote entailment from 52.0% to 95.2% (699/734 entailed).

## The headline finding: one ranking cannot serve two readers

The most important structural result is not any single event — it is that
"important" is not one thing. The same 734 events, the same features, the same
clustering, ranked under two audience rubrics, produce two orderings that share
**0 of their top 10** (8% of the top 25) and correlate at a Kendall τ of
**+0.064** — near zero. Read the two lists side by side and the reason is
obvious:

| rank | investment ranking | rank | engineering ranking |
|---:|---|---:|---|
| 1 | Meta: 3.28 MW datacenter power saved via kernel scheduler | 1 | Mistral: Devstral 2 (123B) + Small 2 (24B) open weights |
| 2 | Google: Gemini for Government, tens of thousands of seats | 2 | DeepSeek: V3.2 + V3.2-Speciale open source |
| 3 | Google: $40M in tokens/credits to the DOE Genesis Mission | 3 | Qwen: six dense models open-weighted (32B→1.7B) |
| 6 | Google: DiffusionGemma, 3.8B active of 26B, fits in 18 GB | 4 | Mistral: paper + public weights + live playground |
| 8 | Google: Gemini Omni Flash at $0.10/second of video | 5 | Mistral: text LLM powers Kyutai's open-source Unmute |
| 10 | OpenAI: Health in ChatGPT | 6 | Qwen3: multilingual support from 29 → 119 languages |

Eight of the engineering top 10 are `open_source` events; the investment top 10
is `commercial` and `infrastructure` — government cloud commitments, token
pricing, datacenter power. Neither list would serve the other reader at all.
This is measured, not asserted, and it is the justification for the entire
two-persona design: a single "importance score" would have quietly served one
audience and mis-served the other.

*(Two caveats on the table, both about the raw ranking rather than the delivered
product. The investment ranks skip numbers because the raw ranking does not
collapse clusters: ranks 3/4/5 are three near-identical reports of the same $40M
commitment, 6/7 one DiffusionGemma story, 8/9 one pricing story — distinct
stories are shown here. And the raw ranking is not windowed, so the engineering
column includes work published as far back as 2024-05. The delivered slate
applies both corrections — one item per cluster, inside the policy's 90-day
window — so a reader sees neither problem. But the divergence figure is computed
on raw ranks, which makes these examples illustrative of the two rubrics'
priorities rather than of what actually ships. Both are discussed under the
honest negatives.)*

## Insight 1 (investment) — OpenAI's Health launch is a direct threat to two holdings

Of 734 events and 59 holding-exposure edges, exactly two are signed as a
*threat* to a named public position, and the clearest is OpenAI shipping Health
inside ChatGPT (2026-07-23). The system's reading, for a PM:

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
the bake-off contenders — 10th under the shipped GBM, 15th under logistic, 26th
under hand-weights, 84th under the recency baseline. A top-decile alert rule
would have fired or not fired depending on which model won a bake-off, which is
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
*reproducible* work, exactly as the technical rubric intends — **8 of the top 10
are `open_source` events**: Mistral's Devstral 2 (123B) and Devstral Small 2
(24B) open-weight release, DeepSeek V3.2 and V3.2-Speciale with a technical
report, Qwen open-weighting six dense models from 32B down to 1.7B, and
DeepSeek-V4 Preview at a 1M-token context. Corpus-wide, `open_source` is only 44
of 734 events (6%), so the technical ranking is concentrating on a thin slice of
the corpus rather than following volume. The system's readings are correspondingly
restrained: the dominant verdict is *investigate* ("spike it in a sandbox,
benchmark against what we run"), with *adopt* reserved for the rare case of
released weights plus a clear reason to switch. This restraint is the honest
shape of a frontier-lab feed — most of what is published is not something a team
can pick up this quarter — and the digest says so in as many words rather than
inflating every release into an action.

## Insight 4 (corpus) — lab coverage is 6× skewed by channel style, and the ranking corrects rather than amplifies it

DeepSeek publishes almost nothing on official channels (37 events in the
corpus); Google DeepMind publishes constantly (232). A volume-following score
would read that 6× coverage gap as a 6× importance gap. The measured result
is the opposite: in the investment top-50, **DeepSeek is over-represented at
2.4× its corpus share** (5% of events, 12% of the top 50) — the ranking
treats scarcity of publication as orthogonal to importance, so the quiet
lab's rare disclosures rank on their content. Two design choices produce
this: scoring is pairwise (an event competes against events, never against a
lab's volume), and the digest caps per-lab slate seats. The per-lab fairness
figure closes the loop — DeepSeek reaches p@10 = 0.90 on 29 scored events, and
normalized by the headroom its high base rate leaves (lift/ceiling 76%) it is
second only to Google DeepMind, despite the thinnest coverage in the corpus.

## Insight 5 (monitoring) — the drift monitor's first run found the engineering product's supply drying up

Drift monitoring (`python -m fli.cli drift`, PSI over categorical mixes and KS
over continuous ones) exists because a filter tuned on last month's corpus and a
ranker trained on last month's labels degrade *silently* when the input
distribution moves — no invariant breaks, the rankings just get worse. On the
committed corpus it reports **3 MAJOR of 4 metrics**, and one of them matters
commercially:

> **`open_source` events: 8.3% of the corpus historically, 0.0% in the last 14
> days.** Zero, against roughly 16 expected at the historical rate.

That is the event type supplying **8 of the engineering ranking's top 10**. The
channel that usually carries open-weight announcements was not under-covered in
that window either — `blog` contributed 52 documents and 103 events, and blog is
historically the largest source of `open_source` events (21 of 44). So the drop
is not obviously a coverage artifact: the places these announcements normally
appear were being read, and carried none. The newest `open_source` event in the
corpus is Gemma 4 12B on 2026-07-01.

Two readings are available and **the system cannot currently distinguish them**:
either the labs genuinely paused open-weight releases in that window, or the
mix shifted in a way the extractor is sensitive to. Stating both is the honest
position, and it is a concrete open question rather than a shrug — what would
settle it is in future scope, §4.

What is not ambiguous is that the monitor earned its place on its first run. No
invariant check would ever have flagged this — the database is perfectly
consistent, C1–C20 are green, and the engineering digest still renders a
confident top 10 built entirely from older material.

The reassuring half of the same table: `insight score` is the one **stable**
metric (KS 0.078 against a 0.114 critical value). The inputs moved substantially;
the distribution of scores the ranking produces did not. That is weak evidence
the scorer responds to event content rather than to corpus mix.

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

**Most events reach no public position.** Only about **6%** of events (and 59 of
734 as holding-exposure edges) connect to a tracked holding through any
identifiable mechanism, and of those, **57 of 59 are `unclear`** — exposure
without an established direction. This is the correct default for a fund that
cannot trade the labs themselves, but it means the investment product is a small
number of high-conviction connections surrounded by an honest majority of "we
see it, we can't sign it", not a dense signal.

**One of the three MAJOR drift readings is partly an artifact of the drift
metric itself.** The `doc source_type mix` PSI of 0.356 is driven mostly by
arXiv tripling its share of fetched documents (11.1% → 32.5%) — but those are
co-author *expansion* documents, fetched to evidence who someone is, and they
almost never yield events: 121 arXiv documents landed in the window and produced
**zero** insights (all-time, 256 arXiv documents yield 22 with events). The
funnel figure excludes register documents from every bar for exactly this
reason; `drift.py`'s `_DOC_MIX` does not, so it counts a register run as corpus
drift. The `doc length` KS follows from the same cause — an arXiv abstract page
is not the size of a tweet. The `event_type` PSI is computed over insights and
is unaffected, so the finding above stands; but the document-level metrics
should not be alarmed on until they are scoped (future scope, §1).

**The corpus ranking is neither de-duplicated nor windowed; only the delivered
slate is.**
Clustering correctly groups near-duplicate claims — the three reports of Google's
$40M Genesis commitment all carry `cluster_id` 32 — but `event_scores.rank`
orders all 734 events without consulting that grouping, so the raw investment
top 10 contains only **6 distinct stories**. A reader never sees this: the slate
builder (`fli/intelligence/scoring.py`, `seen_clusters`) enforces one item per
cluster and reports what it suppressed, and the committed investment digest says
so in as many words: *"669 no mechanism, 21 lab cap, 14 outside window, 11 not
entailed, 4 duplicate cluster, 4 same story, 3 undated."*

The same gap applies to recency. The policy window is 90 days and the slate
enforces it ("14 outside window" above, and 104 in the ai_team digest), but the
raw ranking has no window at all — which is why the engineering top 10 contains
work from 2024-05 and its newest item predates the last two weeks of the corpus
entirely. The `recency` feature carries a coefficient of just +0.091, so score
alone barely encodes it.

So the product is correct and the diagnostic is not: the divergence figure is
computed on raw ranks, which makes its "top 10" really a top 6, drawn from
outside the reporting window. Kendall τ and the overlap curve remain valid — they
measure how differently the two rubrics order the same corpus — but the
illustrative items are not what ships. This is the first item in future scope,
§1.

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
agree with **0.703 of the 323 decided human investment pairs** (and 0.811 of the
53 technical ones) — reported in the bake-off as a fully out-of-sample audit,
since human labels never train the model. That it sits below the 0.835 measured
against the LLM reference is the expected and correct direction: agreeing with
the judge that trained you is easier than agreeing with a person.

**The talent-mobility path is built but unwitnessed.** The system can synthesize
a personnel event from a researcher observed at two labs in succession, and the
test suite plants such a move and shows it reach the digest; the live corpus has
simply not yet witnessed one on the weekly re-observation cadence. The mechanism
is real; the data is not there yet, and the report distinguishes the two.

**The lab with the weakest ranking lift was investigated, and the gap is
composition, not bias.** An earlier bake-off showed Meta AI with the lowest
per-lab lift (+0.05 on a 0.55 base rate), which looks like the ranking
treating one lab worse. Three measured checks said otherwise. First, judge
quality is *better* on Meta pairs, not worse: on pairs a human also labelled,
human–judge agreement is 0.761 when the pair touches a Meta event versus
0.696 when it does not — the labels are not the problem. Second, the gap is
composition: 44% of Meta's events are `infrastructure` engineering posts
(against 8% corpus-wide), exactly the feature shape — official channel, high
specificity, no market mechanism — that the investment rubric tells judges to
rank low but that the feature surface used to reward. Third, the fix worked:
after adding a `mechanism_channel` feature (does the event's own quote
establish a market mechanism?) — now the largest positive weight in the fitted
model at +0.989 — and ~320 more judge labels, **Meta's lift recovered from +0.05
to +0.257** and it is no longer the weakest lab.

The current weakest is Mistral (+0.243 on a 0.357 base, n=28), and the diagnosis
reads the same: its top-10 misses are `release` ×2 and `infrastructure` ×2 —
the identical official-channel-engineering-post shape. That the same diagnosis
now lands on a different lab is the useful result. It says this is a *feature
surface* limitation that moves around with corpus composition, not a lab the
model dislikes — and lab identity is never a feature, so it structurally cannot
be. Most of the remaining spread is ceiling arithmetic: lift is bounded by
1−base, so the figure reports both lift and lift-over-ceiling (Google DeepMind
100%, DeepSeek 76%, Qwen 71%, OpenAI 68%, Meta AI 56%, Anthropic 43%, Mistral
38%) and this class of question can be answered by reading rather than
re-investigating.

## Where the signal is strongest

The learning curve is still climbing — the last step, 160→320 pairs, moved
held-out accuracy +0.025 — so more labels are worth buying, and the human audit
shows the highest-value labels are human ones. One honest wrinkle: headline
held-out accuracy has not risen monotonically as labels were added. It has been
as high as 0.879 on an earlier, smaller label set and currently sits at 0.835 on
581 — not because the model got worse, but because newer labels deliberately
cover harder, more diverse pairs; the early number was flattered by an easier
test set. Cost points the same way: GPT-5.2 produces a usable label for
$0.0028 against Sonnet's $0.0182 with marginally *higher* estimated reliability,
so the same budget already spent on judging would buy roughly 4,600 more labels
instead of 708. The investment
product is sharpest where a lab enters a market a holding occupies
(displacement, which carries an inherent sign) and weakest on demand channels
(where the sign needs a per-event reading). The engineering product looks dense,
because "usable and reproducible" is a property the corpus carries on its face —
but the drift monitor is the caveat on that sentence: its supply of
`open_source` events went to zero in the most recent window, and the ranking
does not notice because it is not windowed.

Everything still open is collected in the next section rather than scattered
through the document.

## How all of that was validated

Everything above is a claim about what the system found. This section is the
evidence for it, and it answers the four questions the brief asks: how
extraction quality was measured, how hallucination was controlled, how the
scoring was validated, and what the ground-truth approach is.

`docs/evaluation-report.md` is the machine-generated companion — 17 figures,
regenerated by `python3 -m fli.cli evaluate`, which reads only the database and
costs $0. This section is the argument; that file is the output. Two consecutive
runs of `evaluate` on the committed database are byte-identical, so every number
below is reproducible from a clone with no API key.

### The metric tiers, stated first

The single most common way an evaluation lies is by reporting "accuracy" against
a reference that is itself the thing being tested. Every figure in this system
therefore carries a tier, and the tier is printed in the report next to the
number:

| tier | meaning | example |
|---|---|---|
| **MECHANICAL** | arithmetic over the database; no human, no judge | funnel, cost, event-type mix |
| **SYNTHETIC** | ground truth known by construction | planted-policy recovery (f17) |
| **vs HUMAN REFERENCE** | agreement with a stated, frozen human-audited set | channel classifier (f5), slate precision (f16) |
| **JUDGED** | against an unaudited LLM reference — provisional | bake-off, ablation, fairness |

F1, precision and recall appear **only** in the SYNTHETIC and HUMAN tiers. On
real data with no gold standard the report says *agreement*, never *accuracy*.
That distinction is the reason the numbers below can be trusted at all.

---

### 1. Extraction quality

Extraction turns one document into one or more events, each a `claim` (the
model's sentence) plus a `quote` (the source's own contiguous words). The quote
is what makes the claim checkable, so quote fidelity is the metric.

**Quote verification (D1) — 94.5%.** Every proposed quote is matched against the
stored bytes of its source document before the insight is allowed to persist.

| kept insights | per-insight failures | whole-document failures | verified |
|---:|---:|---:|---:|
| 734 | 43 | 4 | **94.5%** |

**This number used to read 99.8%, and that was a bug, not an achievement.**
Failed quotes were silently `continue`d in the extraction loop, so they were
never written to `rejections` — the rate was high because failures had stopped
being *counted*, not because they had stopped happening. Per-insight
verification logging was added (`quote_unverified`, 43 rows in `rejections`) and
the honest number fell to 94.5%. It is reported that way deliberately: a
verification metric that cannot go down is not measuring anything.

**Verification tier — 734/734 exact.** Every insight quote verifies at the
strictest tier, byte-verbatim under the shared normalization. Check **C19**
fails the build if this is ever below 100%, so "exact" is an enforced invariant
rather than an observed statistic. (The corpus's 1,255 `structural`-tier
evidence rows carry no claims — they are the register's author-name spans in
structured documents, where structural *is* the designed tier.)

**Quote-length compliance — 92.6%.** The prompt specifies a 10–60 word quote;
680 of 734 land in spec. The 48 under-length quotes are the interesting tail:
short quotes are where a claim most often outruns its evidence, which is exactly
what the entailment check below is for.

**The rejection ledger.** The 43 rejected quotes were characterized rather than
just counted, because "5.5% failed" says nothing about whether the model was
lying or the matcher was fussy:

| kind | n | reading |
|---:|---:|---|
| ≥80%-contiguous near-miss | 20 | the verifier being stricter than the normalization — not model failure |
| paraphrase | 21 | the model rewrote instead of copying — a real prompt failure |
| fabrication (no anchor in source) | **2** | the actual hallucination floor |

Two fabrications in 777 proposed insights is the number that matters, and none
of the 43 were ever shown to a reader — they were rejected at the gate.

### 2. Hallucination control

Byte-verification proves the quote is real. It does not prove the *claim*
follows from the quote — a model can copy a sentence correctly and still
summarize it wrongly. So there is a second, independent check.

**Claim↔quote entailment (f15) — 95.2%.** Every one of the 734 insights was
re-judged with the claim and its quote *alone*, no document context:

| verdict | n | % |
|---|---:|---:|
| entailed | 699 | 95.2% |
| partial | 24 | 3.3% |
| not entailed | 11 | 1.5% |

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
| after one $1.05 run | **699 (95.2%)** | 24 | 11 |

The important property is the direction: repair only ever makes a claim
*weaker*. It cannot add a fact, because it is given nothing but the quote. The
11 `not_entailed` claims were not repaired away — they are gated out of every
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
beat, never as the shipped scorer** — the brief calls an arbitrary weighted sum
dressed up as a score a red flag, and the measurement below is why.

**Investment rubric** — 581 usable labels, 182 held-out pairs, 323 human-labeled
pairs available as an out-of-sample audit:

| model | held-out acc (LLM ref) | human acc (out-of-sample) | p@10 held-out | nDCG@20 held-out |
|---|---:|---:|---:|---:|
| **gbm_sklearn** (winner) | **0.835** | **0.703** | 0.80 | 0.473 |
| logistic | 0.813 | 0.669 | 0.80 | 0.416 |
| baseline_corroboration | 0.637 | 0.480 | 0.50 | 0.203 |
| hand_weights | 0.462 | 0.486 | 0.40 | 0.138 |
| baseline_recency | 0.434 | 0.461 | 0.40 | 0.101 |

**Technical rubric** — 721 usable labels, 218 held-out pairs, 53 human pairs:

| model | held-out acc | human acc | p@10 held-out | nDCG@20 held-out |
|---|---:|---:|---:|---:|
| **logistic** (winner) | **0.789** | **0.811** | 1.00 | 0.671 |
| gbm_sklearn | 0.784 | 0.736 | 0.90 | 0.491 |
| hand_weights | 0.468 | 0.340 | 0.20 | 0.126 |
| baseline_corroboration | 0.404 | 0.547 | 0.30 | 0.219 |
| baseline_recency | 0.317 | 0.340 | 0.30 | 0.258 |

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

The measured margin is **+0.198** (0.835 against the best baseline's 0.637), so
the first row applies and the coefficients are reported below. Had the third row
happened, the headline of the evaluation would have been "twenty features do not
capture this policy" — and that was committed to before the number existed.

Three things worth reading off these tables:

**The hand-weighted sum is worse than chance.** 0.462 and 0.468 held-out, on a
task where chance is 0.500. A plausible-looking set of weights chosen by an
engineer does not merely underperform a fitted model — it fails to rank at all.
Shipping it would have been the exact failure mode the brief warns about, and
the only reason that is known is that it was benchmarked instead of assumed.

**`human_acc` is the non-circular number.** Human labels never enter training,
so this column is a fully out-of-sample audit against a different labeler
population. The winner scores **0.703** (investment) and **0.811** (technical)
against a human. Both are well above the baselines, and both are lower than the
LLM-reference accuracy — which is the correct and expected direction: agreeing
with the judge that trained you is easier than agreeing with a person.

**The winner differs per rubric, and that is allowed.** Investment picks the
GBM, technical picks logistic. Each rubric trains and selects independently;
nothing forces one model family on both audiences.

#### Does it generalize?

**Overfitting (f10)** — largest train/held-out gap is `gbm_sklearn` at +0.050.
Small, so the held-out number is trustworthy.

**Learning curve (f11)** — the last step, 160→320 pairs, moved accuracy +0.025
and is still climbing. More labels are worth buying. This is also the honest
counterweight to the headline: as the label set grew, accuracy did not rise
monotonically, because newer labels deliberately cover harder pairs. An early
number computed on an easier test set flattered the model.

**Ablation (f8) — reported with its limits.** Leave-one-feature-out moves
held-out accuracy by at most 0.011, i.e. **two pairs out of 182**. Only
`specificity` shows a positive delta. The honest conclusion is not "only
specificity matters" but "**at 581 labels the ablation is underpowered**" — no
single feature is load-bearing enough to detect at this resolution. The fitted
logistic coefficients are more informative than the ablation deltas:

| feature | coefficient |
|---|---:|
| `mechanism_channel` | +0.989 |
| `specificity` | +0.520 |
| `corroboration` | +0.395 |
| `event_type_release` | +0.348 |
| `source_type_github` | −0.393 |
| `event_type_benchmark` | −0.587 |

The largest positive weight is "does this event's own quote establish a market
transmission mechanism?" — which is what an investment reader should reward, and
it was added *because* a fairness investigation said the feature surface was
missing it.

#### Fairness

Lab identity is never a feature. Per-lab precision@10 is the check, but raw
p@10 tracks each lab's base rate, so **lift over base rate** is the fairness
number — and lift is itself ceilinged at `1 − base`, so lift/ceiling is the only
comparable figure across labs:

| lab | n | base | p@10 | lift | lift/ceiling |
|---|---:|---:|---:|---:|---:|
| Google DeepMind | 94 | 0.436 | 1.00 | +0.564 | 100% |
| DeepSeek | 29 | 0.586 | 0.90 | +0.314 | 76% |
| Qwen | 71 | 0.310 | 0.80 | +0.490 | 71% |
| OpenAI | 56 | 0.375 | 0.80 | +0.425 | 68% |
| Meta AI | 35 | 0.543 | 0.80 | +0.257 | 56% |
| Anthropic | 67 | 0.299 | 0.60 | +0.301 | 43% |
| Mistral | 28 | 0.357 | 0.60 | +0.243 | 38% |

xAI is excluded rather than scored: 2 events, and a ratio on 2 events is noise.
Excluding a lab is more honest than printing a number for it.

The weakest lift is Mistral (+0.243), and its top-10 misses are `release` ×2 and
`infrastructure` ×2 — official-channel engineering posts whose feature shape
(official source, high specificity) the score rewards but the judges call
irrelevant. That is a feature-shape gap, not lab bias. The same diagnosis
previously applied to Meta AI, and adding `mechanism_channel` plus ~320 labels
moved Meta from the bottom of this table to the middle — evidence the diagnosis
was right and the fix worked.

#### Does the machinery work at all?

Every number above is measured against a judge that is not ground truth. So one
figure removes that dependency entirely.

**Planted-policy recovery (f17) — SYNTHETIC.** Four known weight vectors are
planted over the *real* standardized feature matrix (real, so recovery must
survive the actual feature correlations), 400 pairwise labels are generated from
each with **10% of verdicts flipped** — the same order of unreliability measured
in the real judge — and the bake-off's own training path is asked for the
planted ranking back:

| planted policy | AUC | F1 |
|---|---:|---:|
| single_feature | 0.99 | 0.91 |
| hand_shape | 0.97 | 0.91 |
| dense_mixed | 0.95 | 0.84 |
| anti_prior | 0.97 | 0.89 |

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

There is no labelled dataset of "important frontier-lab events", and nobody on
this project is a portfolio manager. The two obvious answers are both weak:

- **"I labelled 150 pairs myself."** The author is an engineer, not a PM, so the
  labels are an undefended opinion wearing a lab coat.
- **"I used an LLM as judge."** BIT already runs ">1bn tokens across 30+
  LLM/LRM processes per month" — LLM-as-judge is their day job. It
  differentiates nothing, and it moves the unanswered question one level up:
  how good is the judge?

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
effectively guessed", and those verdicts are **excluded from training** (436 of
1,017 investment verdicts). Keeping coin-flips out is worth more than the label
volume they would add.

The lab name is withheld from the judge, presentation order is randomised per
pair and un-swapped on store, and labels are namespaced
`llm:<model>/<rubric>/r<version>` so judgements made under different rubrics are
never pooled.

#### (b) Reliability from disagreement — and the trap it hides

Dawid–Skene estimates each labeler's accuracy from their disagreement alone, on
468 investment pairs across three independent families:

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
written down *before any code was changed*, on the principle that a result which
can be graded after the fact is not a result. The decisive one was:

> **H4.** Even after the lexicon is repaired, channel-assignment F1 stays below
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

### 5. Is the corpus still the corpus the models were fitted to?

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

On the committed database — last 14 days (from 2026-07-15) vs everything before:

| metric | kind | value | threshold | n_ref | n_cur | verdict |
|---|---|---:|---:|---:|---:|---|
| doc `source_type` mix | PSI | 0.356 | 0.250 | 817 | 372 | **MAJOR** |
| insight `event_type` mix | PSI | 0.792 | 0.250 | 529 | 192 | **MAJOR** |
| doc length | KS | 0.176 | 0.085 | 817 | 372 | **MAJOR** |
| insight score | KS | 0.078 | 0.114 | 529 | 192 | stable |

**3 MAJOR of 4**, and the causes are legible rather than mysterious:

| | history | last 14d |
|---|---:|---:|
| `social` documents | 65.2% | 46.0% |
| `arxiv` documents | 11.1% | **32.5%** |
| `open_source` events | 8.3% | **0.0%** |
| `commercial` events | 9.8% | 19.3% |
| `other` events | 8.3% | 18.8% |

arXiv nearly tripled its share of the corpus — that is co-author expansion
working as designed, pulling in paper listings — which also explains the
document-length shift, since an arXiv abstract page is a different size from a
tweet. Neither is a defect.

The `open_source` collapse is a genuine finding and is discussed in the final
report: **the event type that dominates the engineering ranking produced zero
events in the most recent window.** The drift monitor caught, on its first run, a
real problem that no invariant check would ever have flagged.

That `insight score` is the one **stable** metric is the reassuring half: the
inputs moved, but the score distribution the ranking produces did not. The
scoring layer absorbed a substantial change in corpus composition without its
output distribution shifting — which is weak evidence that it is responding to
event content rather than to corpus mix.

Drift is deliberately **not** part of the `checks` battery. An organic news
cycle must not turn the release gate red. It exits with the count of MAJOR
drifts so a scheduler can alarm on it, and it runs as a free, informational node
inside the graph.

### What this evaluation does not establish

Stated here rather than left for a reader to discover.

- **The judged tier is provisional.** Bake-off, ablation and fairness are all
  measured against an unaudited LLM reference. `human_acc` is the corrective,
  and it is lower.
- **The ablation is underpowered** at 581 labels — ±0.011 is two pairs.
- **Per-lab n is small** (28–94 events). A p@10 on 28 events is a noisy
  statistic even after the lift correction.
- **The entailment figures pre-date the prompt fix** that was measured to
  improve them. The corpus has not been re-extracted.
- **The channel benchmark is 100 posts**, one annotator, one policy version.
- **The raw corpus ranking is neither de-duplicated nor windowed, and the
  divergence figure is computed on it.** `event_scores.rank` orders all 734
  events by score alone: near-duplicate claims from one cluster can occupy
  adjacent ranks (the investment top 10 is really 6 distinct stories), and there
  is no recency window, so the technical top 10 includes events published as far
  back as 2024-05. The *delivered* slate applies both corrections —
  one-per-cluster suppression and the policy's 90-day window
  (`fli/intelligence/scoring.py`, `seen_clusters`; "104 outside window, 19 same
  story" in the committed digest) — so a reader sees neither problem. But
  `fig_rubric_divergence` ranks by `score DESC` over the whole table, which
  makes its top-k *examples* unrepresentative of what ships. The Kendall τ and
  the overlap curve are still valid measures of how differently the two rubrics
  order the same corpus; the illustrative items are not the delivered product.
- **No gold labels exist for extraction.** Quote verification is mechanical and
  entailment is LLM-judged; neither is a human-audited extraction reference.
  That is the single largest gap in the evaluation.

What it would take to close each of these is collected under
[future scope](#future-scope) rather than repeated here.

## Future scope

Every known gap, deferred decision and unimplemented capability in the system,
in one place. Nothing here is a surprise found late: each item is the direct
consequence of a measurement reported above, and each names what it would take.

### 1. Correctness of the ranking diagnostic

| | |
|---|---|
| **Rank on cluster representatives, inside the policy window** | The raw ranking neither collapses clusters nor applies the 90-day window, so the investment top 10 is really 6 distinct stories and the engineering top 10 reaches back to 2024-05. The delivered slate corrects both, so the product is right and the diagnostic is wrong. One change fixes both and makes the divergence figure describe what actually ships. **Highest priority — it is the only item that changes a number a reviewer reads.** |
| **Scope the drift metrics to extraction-eligible documents** | `_DOC_MIX` and `_DOC_LEN` count every `raw_documents` row, including register pages that never yield an event. 121 arXiv expansion documents produced zero insights while driving `source_type` PSI to 0.356, so one of the three MAJOR readings is partly an artifact of the metric. `_EVENT_MIX` and `_SCORE` are over insights and are unaffected. |

### 2. Labels — where the remaining accuracy is

| | |
|---|---|
| **Buy the next tranche from the cheaper judge** | GPT-5.2 costs $0.0028 per usable label against Sonnet's $0.0182, returns fewer low-confidence verdicts (22.0% vs 27.5%), and its estimated reliability is marginally *higher* (0.874 vs 0.864). The same $12.87 already spent would buy roughly 4,600 labels instead of 708. The learning curve (last step +0.025) says they are still worth buying. |
| **Then buy human labels on the investment rubric** | Where the two models' shared blind spot is largest and slate precision is weakest (53% kept, against 86% for `ai_team`). |
| **Human labels on the technical rubric** | 53 human pairs against 323 for investment, so the technical `human_acc` of 0.811 rests on a much thinner reference than its investment counterpart. |
| **A human-audited extraction reference** | The single largest evaluation gap. Quote verification is mechanical and entailment is LLM-judged; there is no gold extraction set. Everything in the JUDGED tier stays provisional until there is one. |

### 3. Cost

| | |
|---|---|
| **Batch the judge runs** | The `--batch` path exists and is tested but post-dates most of the committed labels. The batch API is a flat 50% off and judge runs are embarrassingly parallel, which would take the $12.87 judge line to about $6.43. |
| **Stop paying the cache write premium** | Only the judge wrote cache tokens — 2,087 writes, **0 reads** — because the 5-minute ephemeral TTL expired between calls every time. It cost about $0.002 in premium and returned nothing. The fix is batching, above, not more caching. |

### 4. Corpus and coverage

| | |
|---|---|
| **Resolve the `open_source` question** | 8.3% of the corpus historically, 0.0% in the last 14 days — and it supplies 8 of the engineering top 10. Genuine pause or extraction-side artifact is not yet decidable; it needs a longer window and a per-lab release-feed check. The one open question here with a direct product consequence. |
| **Witness a live talent move** | The synthesis path is verified end to end in the suite, but re-observation runs on a 7-day cadence and the first observation landed 2026-07-30, so the earliest a real move can be witnessed is 2026-08-06. Waiting, not building. |
| **Deepen person attribution** | 58 of 734 events reach a person. The register, expansion and approval machinery works; official channels simply rarely name individuals outside paper bylines. GitHub and arXiv also track disjoint populations — the measured overlap is zero. |
| **Re-extract the back corpus** | The quote-first prompt revision cut the partial-entailment rate on the 30 hardest documents from 65.6% to 47.9%, but it applies only to new extractions, so the reported corpus figures are the pre-revision audit. |
| **Grow the channel benchmark** | 100 posts, one annotator, one policy version. |

### 5. Deployment

The system runs four ways today — CLI, Flask UI, MCP server, and a scheduled
GitHub Actions job — and ships a `Dockerfile`, so a reviewer can build and run
it without a Python environment at all. What remains is unimplemented but
unblocked: state is one SQLite file, the runtime is plain Python, and model
routing is a single dictionary in `fli/ops/llm.py`.

| | |
|---|---|
| **Vertex AI / Bedrock inference** | A provider entry in `fli/ops/llm.py` — `KEY_ENV`, `provider_for` and `PRICES` — rather than a rewrite. The OpenAI path already proves the second-provider shape works. |
| **Durable checkpointing** | The graph compiles with an `InMemorySaver`, which is sufficient because the approval pause and its resume happen in one CLI process. A long-running or distributed runner would need a real checkpointer. |

### 6. Governance

| | |
|---|---|
| **The policy has no domain owner** | `config/policy.yml` reads `owner: "BIT PM — unassigned"`, and the system prints `[NOT REVIEWED]` on every run rather than hiding it. Every weight in it is provisional until a portfolio manager signs off. This is not an engineering task, and it is the item that most limits what the scoring can claim. |
