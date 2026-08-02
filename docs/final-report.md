# Frontier Lab Intelligence

## The system in one paragraph

Eight frontier AI labs and the people inside them: **1,675 documents** ingested
from official channels, **954 evidence-backed events** extracted, **285
researchers across 322 identities** resolved on arXiv, GitHub, X and lab pages.
Every event is ranked twice — once for an investment reader, once for an
engineering reader — from **2,735 pairwise labels** (2,308 from two model-judge
families, 427 from a human). Events are then mapped to public-equity holdings,
given a per-audience reading, and delivered as a cited digest and an alert path.
The same intelligence is readable four ways — CLI, Flask UI, a read-only MCP
server, and a scheduled GitHub Actions run — all calling the same layer
functions, so no surface can show a different answer. Total LLM spend:
**$29.87 across 8,707 calls**.

## The headline finding: one ranking cannot serve two readers

The most important result is not any single event — it is that "important" is
not one thing. The same 954 events, the same features, the same clustering,
ranked under two audience rubrics, share **0 of their top 10** and correlate at
a Kendall **τ of +0.128**.

| rank | investment ranking | rank | engineering ranking |
|---:|---|---:|---|
| 1 | Mistral: Emmi AI acquihire — 30+ researchers | 1 | Google: DiffusionGemma, 26B MoE text-diffusion, Apache 2.0 |
| 2 | Mistral: 10 MW Les Ulis datacenter for inference | 2 | Google: DiffusionGemma weights on Hugging Face, MLX-servable |
| 3 | Meta: 28% ads-retrieval tail-latency cut, 3.28 MW saved | 3 | Qwen: AgentWorld-35B-A3B open-sourced, 256K context |
| 4 | Google: DiffusionGemma, 3.8B active of 26B, fits in 18 GB | 4 | OpenAI: moderation endpoints for responses/chat-completions |
| 5 | Google: Gemini Omni Flash at $0.10/second of video | 5 | OpenAI: Python SDK adds Amazon Bedrock Responses |

Investment leads with `personnel`, `infrastructure` and `commercial`; engineering
leads with three `open_source` releases and fills with shippable tooling. A
single "importance score" would have quietly served one audience and mis-served
the other.

Both columns are the ranking a reader actually gets: the persisted rank collapses
each cluster to one representative story and places in-window events first. One
story appears in both columns for different reasons — the investment ranking
carries DiffusionGemma's consumer-GPU cost profile, the engineering ranking its
Apache 2.0 weights.

## Insight 1 — a launch that threatens two holdings

Of 954 events and 54 holding-exposure edges, exactly two are signed as a *threat*
to a named public position: OpenAI shipping Health inside ChatGPT (2026-07-23),
against Hinge Health and Oscar Health.

> OpenAI's Health in ChatGPT gives consumers a free, broad AI health companion
> that connects personal health data, competing for the same "personalized
> health guidance" use case that Hinge Health (HNGE) and Oscar Health (OSCR)
> monetize.

The reading rests on the lab's own words — "connect Apple Health and supported
medical records" — not a keyword, and is rated *medium* confidence over
*quarters*: the feature has shipped, but its effect on either funnel is a
plausible mechanism, not a measured one.

It fired the alert path twice, and **neither trigger was the score**. Its rank is
not stable across the bake-off contenders — 22nd under the shipped GBM, 14th
under logistic, 37th under hand-weights, 506th under the recency baseline. A
rank threshold would make the alert an accident of which model won the last
bake-off, so alerts fire on a *signed direction* instead, once, enforced by a
UNIQUE key.

## Insight 2 — the same channel points in opposite directions

Three datacenter-power events, one mechanism, opposite signs:

- **OpenAI's Project Camellia**, a 3.2 GW Georgia datacenter through 2028–2032 —
  a **tailwind** for IREN: capacity scarcity is the thesis.
- **Meta's kernel-scheduler optimization**, saving 3.28 MW through software — a
  **threat** to the same thesis: large workloads served with *less* power.
- **Gemma 4 12B**, matching a 26B model on under half the memory — a **threat**
  to Micron, undercutting the "more memory per model" demand driver.

The deterministic layer leaves all three `unclear` and defers the sign to the
persona layer, which reads the sentence. Averaging "building capacity" and
"saving capacity" into a channel-level direction would be confidently wrong, and
this is what most distinguishes the system from a keyword dashboard.

## Insight 3 — the engineering frontier is open weights, not benchmark records

The technical **top 3 are all `open_source`**, and the rest of the top 10 is
shippable tooling: moderation endpoints, a Bedrock-capable SDK, a robotics
suite. Corpus-wide `open_source` is only **50 of 954 events (5.2%)**, so putting
all three at ranks 1–3 means the ranking concentrates on a thin slice rather than
following volume. The readings stay restrained — the dominant verdict is
*investigate*, not *adopt*, which is the honest shape of a frontier-lab feed.

## Insight 4 — coverage is 10× skewed, and the ranking corrects for it

DeepSeek publishes almost nothing on official channels (37 events); Google
DeepMind publishes constantly (392). A volume-following score would read that
10× gap as a 10× importance gap. Measured, the opposite happens: in the
investment top 50, **DeepSeek is over-represented at 3.1× its corpus share** (4%
of events, 12% of the top 50), and its lift-over-ceiling is 100%, tied with
Google DeepMind at the top of the fairness table.

Two design choices produce this: scoring is pairwise, so an event competes
against events and never against a lab's volume; and the digest caps per-lab
slate seats.

## Insight 5 — the drift monitor caught the engineering supply drying up

On the committed corpus, drift reports **4 MAJOR of 4 metrics**. One matters
commercially:

> **`open_source` events: 6.8% of the corpus historically, 0.0% in the last 14
> days** — zero, against roughly 14 expected at the historical rate.

That is the event type supplying the top 3 of the engineering ranking, and the
newest one in the corpus is Gemma 4 12B on 2026-07-01.

**The follow-up located the cause, and it is neither a pause in the industry nor
a broken extractor — it is which labs the window sampled.** Only four labs
produce open-weight events at all (Qwen 23 of 50, Google DeepMind 13, Mistral 5,
DeepSeek 5), and in this window three of them nearly vanish:

| share of events | history | last 14d |
|---|---:|---:|
| Qwen | 18.2% | 2.9% |
| Mistral | 6.0% | **0** |
| DeepSeek | 5.0% | **0** |
| OpenAI + Anthropic | 17.8% | **70.4%** |

The window is 70% OpenAI and Anthropic, two labs that account for 2 of 50
open-source events between them. The same ingest wave that quadrupled arXiv's
share also tilted the lab mix toward the labs that do not open-weight.

Extraction was checked too, and cleared: 58 in-window documents mention
open-weight terms, but they are labs *discussing* the subject — "Anthropic
states that it has never advocated for a ban on open-weights models" — not
announcing releases. Nothing was misclassified.

So the drought is real for the reader and temporary in the corpus: the
engineering digest genuinely has less to serve this window, and the fix is
coverage balance rather than a prompt change. No invariant check would have
surfaced any of this — the database is perfectly consistent and C1–C20 are
green — and the diagnosis cost nothing beyond queries the monitor already
motivated.

## The honest negatives

**The investment digest can be honestly empty, and this week it is.** The most
recent committed investment digest delivers **zero items**: no in-window event
both establishes a market mechanism and reaches a tracked holding. The slate
ledger says where the corpus went — *"839 no mechanism, 42 thin quote, 40
outside window, 16 no holding link, 14 not entailed, 3 undated"* — and the
digest renders that ledger rather than padding. The engineering digest for the
same date carries 6 items, which is the two-persona design behaving as measured:
the same week can be news for one reader and noise for the other.

**Most events reach no public position.** About **5%** of events (54 exposure
edges across 50 of 954), and **52 of 54 are `unclear`** — exposure without an
established direction. Correct for a fund that cannot trade the labs themselves,
but the investment product is a few high-conviction connections surrounded by an
honest majority of "we see it, we can't sign it".

**GitHub and arXiv track disjoint populations.** After resolving 146 GitHub and
112 arXiv identities, the overlap is **zero**. The people who ship a lab's client
SDK are not the people who write its papers — a real finding about how labs are
structured, but it means the cross-platform link the register was built to make
is rarer than hoped.

**The two model judges agree with each other more than with the human**, which
is the most important result in the evaluation and is detailed below.

**Talent mobility is built but unwitnessed.** All 10 `personnel` events came from
document extraction, none from affiliation history. What the corpus cannot show,
a rank probe can: a synthetic move with the median feature shape of those 10,
dated two days ago, lands at **investment #46 of 954 (top 5%) and technical #953
of 954**. Nothing is written — it is an in-memory probe. The mechanism works, the
ranking would surface it, and the two rubrics disagree exactly as they should.
Only the observation is missing.

**The weakest lab was investigated, and the gap is composition, not bias.** Meta
AI has the lowest per-lab lift. Judge quality is *better* on Meta pairs
(human–judge agreement 0.761 vs 0.696 elsewhere), so the labels are not the
problem; 44% of Meta's events are `infrastructure` posts against 7% corpus-wide —
the exact feature shape the rubric tells judges to rank low. Adding
`mechanism_channel` moved Meta's lift from +0.05 to **+0.30**, and the recovery
held even as corpus growth returned Meta to the bottom of a now-tighter table.
It is a feature-surface limitation that moves with composition. Lab identity is
never a feature, so it structurally cannot be bias.

## How it was validated

Figures live in `docs/evaluation-report.md`, regenerated by
`python3 -m fli.cli evaluate` — database only, no API key, $0. Two consecutive
runs are byte-identical, so every number below reproduces from a clone.

### Metric tiers

The commonest way an evaluation lies is reporting "accuracy" against a reference
that is itself the thing being tested. Every figure carries a tier:

| tier | meaning | example |
|---|---|---|
| **MECHANICAL** | arithmetic over the database | funnel, cost, event-type mix |
| **SYNTHETIC** | ground truth known by construction | planted-policy recovery |
| **vs HUMAN REFERENCE** | agreement with a frozen human-audited set | channel classifier, slate precision |
| **JUDGED** | against an unaudited LLM reference — provisional | bake-off, ablation, fairness |

F1, precision and recall appear **only** in the SYNTHETIC and HUMAN tiers. On
real data with no gold standard the report says *agreement*, never *accuracy*.

### Extraction and hallucination control

**Quote verification — 94.8%.** Every proposed quote is matched against the
stored bytes before the insight persists; 52 were rejected.

**This number used to read 99.8%, and that was a bug.** Failed quotes were
silently `continue`d, so they were never written to `rejections` — the rate was
high because failures had stopped being *counted*. Per-insight logging was added
and the honest number fell below 95%. A verification metric that cannot go down
is not measuring anything.

The 52 rejections were characterised, not just counted: **25** near-misses (the
verifier stricter than the normalization), **25** paraphrases (a real prompt
failure), and **2** fabrications. Two fabrications in 1,006 proposed insights is
the hallucination floor, and none of the 52 ever reached a reader.

**Claim↔quote entailment — 97.5%** (930 of 954; 10 partial, 14 not entailed),
judged from the claim and quote *alone*. `verify --repair` rewrites every
`partial` claim constrained to its quote, which took the corpus from **52.0% to
97.5%** for $1.38. The direction is the point: repair can only make a claim
*weaker*, because it is given nothing but the quote. The 14 `not_entailed` are
gated out of every slate.

Checks **C1** and **C2** re-hash every document and re-verify every quote on
*every* run, so a source that silently edits a page surfaces as a failure rather
than leaving a stale claim in place.

### Scoring: the bake-off

Five contenders, same labels, same seeded split. Investment rubric — 760 usable
labels, 224 held-out pairs, 323 human pairs as an out-of-sample audit:

| model | held-out acc | human acc | p@10 | nDCG@20 |
|---|---:|---:|---:|---:|
| **gbm_sklearn** (winner) | **0.768** | **0.684** | 0.90 | 0.232 |
| logistic | 0.763 | — | 1.00 | 0.325 |
| baseline_corroboration | 0.491 | — | 0.20 | 0.074 |
| baseline_recency | 0.478 | — | 0.30 | 0.049 |
| hand_weights | 0.424 | — | 0.30 | 0.111 |

Technical rubric: the GBM wins at **0.827 held-out, 0.849 against a human**.

**The hand-weighted sum is worse than chance** — 0.424 against a 0.500 baseline.
A plausible-looking set of weights chosen by an engineer does not merely
underperform a fitted model, it fails to rank at all. The only reason that is
known is that it was benchmarked instead of assumed.

**`human_acc` is the non-circular number.** Human labels never enter training, so
that column is a fully out-of-sample audit against a different labeler
population. It sits below the LLM-reference accuracy on the investment rubric,
which is the correct direction: agreeing with the judge that trained you is
easier than agreeing with a person.

**How the result would be reported was fixed before the bake-off ran** — clearly
better (≥ +0.10) ship and lead with coefficients; marginal (+0.02–0.10) ship but
state the margin; no better (< +0.02) **lead with the negative result**. The
measured margin is **+0.277**, so the first row applies. Had the third happened,
the headline would have been "twenty features do not capture this policy", and
that was committed to before the number existed.

**Limits, stated with the result.** The GBM's train/held-out gap is **+0.109** —
it memorises, and only the held-out number should be read. The learning curve has
**plateaued** (+0.004 on the last doubling): more labels from the same judges
will not move it; the features are the limit. Leave-one-feature-out moves
accuracy by at most +0.022 — five pairs out of 224 — so the ablation is coarse
rather than conclusive. The largest fitted coefficient is `mechanism_channel`
(+0.798), which was added *because* a fairness investigation said the feature
surface was missing it.

**Fairness.** Lab identity is never a feature. Raw p@10 tracks each lab's base
rate, so lift over base rate is the fairness number, and since lift is ceilinged
at `1 − base`, lift/ceiling is the only comparable figure: Google DeepMind 100%,
DeepSeek 100%, Qwen 69%, Mistral 68%, OpenAI 58%, Anthropic 53%, Meta AI 50%.
xAI is excluded rather than scored — 2 events, and a ratio on 2 events is noise.

### Reliability without ground truth

No labelled dataset of "important frontier-lab events" exists, and the two
obvious substitutes are weak: labels supplied by whoever built the system are an
engineer's opinion rather than the fund's editorial judgement, and an LLM judge
alone just moves the question up a level — how good is the judge? So:

> **Do not claim the policy. Prove the instrument.**

Importance is a *stated editorial policy*, owned and versioned as configuration.
What can legitimately be validated is the machinery that turns judgements into a
ranker. Four independent references stand in for gold labels.

**The judge, and why it is not trusted.** Importance is elicited pairwise, the
judge must cite the rubric rule that decided, and it must return a confidence —
`low` means "I guessed", and those are excluded from training (585 dropped,
leaving 760 usable). The lab name is withheld, presentation order is randomised
per pair and un-swapped on store.

**Dawid–Skene, and the trap it hides.** Estimated from disagreement alone across
three independent families, on 462 pairs voted by at least two:

| labeler | estimated accuracy |
|---|---:|
| GPT-5.2 | 0.874 |
| Claude Sonnet 5 | 0.864 |
| human | 0.778 |

**The human scoring lowest is the most important result here, and it is not a
finding about the human.** Two causes. *Adverse selection*: the labeling CLI
deliberately routes human sittings to pairs where the judges disagree or the
posterior sits nearest 0.5, so over half the human reference lives on the hardest
pairs while the models vote on the whole sample. *Correlated priors*: two models
sharing a training prior agree with each other, and a disagreement-based
estimator reads that mutual corroboration as accuracy. Measured with Cohen's κ:

| pair | pairs | raw agreement | κ |
|---|---:|---:|---:|
| model ↔ model | 462 | 0.773 | **0.546** |
| model ↔ human | 363 | 0.617–0.623 | 0.288–0.290 |

The models agree with each other roughly twice as strongly as either agrees with
the person. An earlier run made it explicit: Dawid–Skene rated both models
(0.870) *above* the human (0.843). The system now **refuses to compute
Dawid–Skene from a single model family** — three prompt variants of one model
agreed 92–100% and the estimator rated them all ~0.99 — and always reports human
agreement alongside the model estimate. The models are a usable ranking signal,
but they are not ground truth.

**A frozen benchmark, with the verdict pre-registered.** The mechanism
classifier is scored against 100 human-audited X posts behind an audit gate: new
posts enter classifier-seeded but are excluded from scoring until a human reviews
them, so the model can never grade its own homework. The threshold was fixed
before the classifier existed:

> Even after the lexicon is repaired, channel-assignment F1 stays below **0.80**.
> If F1 ≥ 0.80 the lexicon is sufficient — ship it and build no classifier.

A cheap deterministic matcher beating an LLM would have been the *better*
outcome, and it was pre-committed that the classifier would not get built
regardless. The lexicon lost on its own terms: **classifier F1 0.444 vs lexicon
0.267**, and the classifier's errors are the safe kind (missed mechanisms) while
the lexicon's are confident — it once read a phone codec that "increased power
usage" as a datacenter signal.

**Precision@k on the delivered product**, the only reference that measures what a
reader receives. Every digest item was marked keep or cut by a human: **ai_team
12/14 (86%)**, **investment 10/19 (53%)**. The investment number is the weakest
honest result, and it corroborates the fairness finding from an independent read
— the cuts are the same failure class.

**Planted-policy recovery — SYNTHETIC.** Four known weight vectors planted over
the *real* feature matrix, 400 labels generated from each with 10% of verdicts
flipped, and the bake-off's own training path asked for the planted ranking back:
AUC 0.96–1.00, F1 0.88–0.95. `anti_prior` puts a *negative* weight on recency, so
no baseline shape recovers it by luck. This validates the *learner* — not the
features, and not the policy.

### What the evaluation does not establish

- **The judged tier is provisional.** Bake-off, ablation and fairness are all
  measured against an unaudited LLM reference; `human_acc` is the corrective.
- **The ablation is coarse** at 760 labels — the largest delta is five pairs.
- **Per-lab n is small.** A p@10 on a few dozen events is noisy even after the
  lift correction.
- **The winner memorises** (+0.109 train/held-out gap); training numbers should
  never be quoted.
- **The back corpus pre-dates the quote-first prompt fix.** Repair compensates
  downstream, but the extraction-side fix applies only to new documents.
- **The channel benchmark is 100 posts**, one annotator, one policy version.
- **No gold labels exist for extraction** — the single largest gap.

## Future scope

Each item is the direct consequence of a measurement above.

### 1. Labels — where the remaining accuracy is

| | |
|---|---|
| **Spend on features and human labels, not more LLM labels** | The learning curve has plateaued (+0.004 on the last doubling). More labels from the same judges buy noise; the remaining accuracy is in the feature surface and in human labels, which measure reader preference rather than judge consensus. When labels *are* bought, the cheaper family costs $0.0022 per label against $0.0101 with marginally higher reliability. |
| **Human labels on both rubrics** | Investment is where the models' shared blind spot is largest and slate precision weakest (53%). Technical `human_acc` of 0.849 rests on only 53 decided pairs. |
| **A human-audited extraction reference** | The single largest evaluation gap. Everything in the JUDGED tier stays provisional until there is one. |

### 2. Cost

| | |
|---|---|
| **Batch the two lines that matter** | The `--batch` path (50% off) has carried 462 of 8,707 calls, saving $0.24. `judge` ($17.28) and `extract` ($6.14) are 78% of spend and have never been through it — worth roughly $11 on a re-run. |

### 3. Corpus and coverage

| | |
|---|---|
| **Balance lab coverage per window** | The `open_source` drought traced to sampling, not to the industry or the extractor: the last window was 70% OpenAI and Anthropic, and the four labs that actually open-weight had nearly vanished from it. Ingestion has no per-lab balance target, so an ingest wave weighted toward one channel silently reshapes what the engineering reader can be served. A per-lab floor on each window is the fix. |
| **Witness a live talent move** | Re-observation runs on a 7-day cadence and the first landed 2026-07-30, so the earliest a real move can be witnessed is 2026-08-06. Waiting, not building. |
| **Deepen person attribution** | 61 of 954 events reach a person. The machinery works; official channels rarely name individuals outside paper bylines. |
| **Re-extracting the back corpus — declined, not pending** | The quote-first revision applies only to new extractions, so part of the corpus predates it. Re-running extraction would cost about $6 in tokens, but the repair pass has already taken entailment to 97.5% and left **10 partial claims in 954** — and new extractions mean new event ids, which would invalidate 2,735 pairwise labels, 9,540 score rows and every position and persona note. Re-buying the label set to improve ten claims is the wrong trade, so it is recorded as declined rather than carried as work. |
| **Grow the channel benchmark** | 100 posts, one annotator, one policy version. |

### 4. Deployment — unblocked, not done

Four surfaces run today plus a `Dockerfile`. What remains is unimplemented but
unblocked: state is one SQLite file, the runtime is plain Python, and model
routing is a single dictionary.

| | |
|---|---|
| **Vertex AI / Bedrock inference** | A provider entry in `fli/ops/llm.py` rather than a rewrite; the OpenAI path already proves the shape. |
| **Durable checkpointing** | The graph compiles with an `InMemorySaver`, sufficient because the approval pause and resume share one process. A distributed runner would need more. |

### 5. Governance

| | |
|---|---|
| **The policy has no domain owner** | `config/policy.yml` reads `owner: "BIT PM — unassigned"` and the system prints `[NOT REVIEWED]` on every run. Every weight is provisional until a portfolio manager signs off — not an engineering task, and the item that most limits what the scoring can claim. |
