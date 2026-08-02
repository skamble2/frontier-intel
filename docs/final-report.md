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

## Insight 4 (methodology) — the channel classifier failed its first human audit at F1 = 0.000, and only the audit could have caught it

The mechanism classifier that connects events to holdings looked healthy while
it was measured against its own kind — LLM-judged benchmarks, cached verdicts,
self-consistent. Then the frozen 29-post benchmark was human-audited, and
against that reference the cached classifier collapsed to **F1 = 0.000**
(systematic channel over-assignment: it kept finding mechanisms in posts where
a person finds none) while the humble keyword lexicon survived at 0.667. The
failure was diagnosed and fixed, and the benchmark grown to **100 human-audited
labels behind an audit gate** — new posts enter classifier-seeded but are
dropped from scoring until a human reviews them, so the model can never grade
its own homework. On that reference the fixed classifier measures **F1 0.444
vs the lexicon's 0.267**, and its remaining errors are the safe kind (missed
mechanisms) rather than the lexicon's dangerous kind (a phone codec flagged as
a datacenter signal because "power" appears). Both scores stay in the
evaluation report, 0.000 included — it is the strongest evidence the reference
is real and frozen rather than curated after the fact.

## Insight 5 (corpus) — lab coverage is 10× skewed by channel style, and the ranking corrects rather than amplifies it

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

## Insight 6 (monitoring) — the drift monitor's first run found the engineering product's supply drying up

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

## Future scope

Every known gap, deferred decision and unimplemented capability in the system,
in one place. Nothing here is a surprise found late: each item is the direct
consequence of a measurement reported above, and each names what it would take.

### 1. Labels — where the remaining accuracy is

| | |
|---|---|
| **Spend on features and human labels, not more LLM labels** | The learning curve has plateaued: the last doubling of pairs moved held-out accuracy +0.004. More labels from the same judges will buy noise; the remaining accuracy lives in the feature surface (the f9 misses are all one feature shape) and in human labels, which measure reader preference rather than judge consensus. When labels are bought, GPT-5.2 costs $0.0022 per usable label against Sonnet's $0.0101, returns fewer low-confidence verdicts (22.0% vs 27.0%), and its estimated reliability is marginally *higher* (0.874 vs 0.864). |
| **Then buy human labels on the investment rubric** | Where the two models' shared blind spot is largest and slate precision is weakest (53% kept, against 86% for `ai_team`). |
| **Human labels on the technical rubric** | 64 human sittings against 363 for investment, so the technical `human_acc` of 0.849 rests on a much thinner reference (53 decided pairs) than its investment counterpart. |
| **A human-audited extraction reference** | The single largest evaluation gap. Quote verification is mechanical and entailment is LLM-judged; there is no gold extraction set. Everything in the JUDGED tier stays provisional until there is one. |

### 2. Cost

| | |
|---|---|
| **Batch the remaining paid paths** | The `--batch` path (flat 50% off) is implemented and carried the most recent judge, verification and repair tranches, but the majority of the committed $17.28 judge line predates it and was paid at full price. Any future re-judging or re-extraction should go through it by default — the pipeline's paid stages are all embarrassingly parallel. |

### 3. Corpus and coverage

| | |
|---|---|
| **Resolve the `open_source` question** | 6.8% of the corpus historically, 0.0% in the last 14 days — and it supplies the top 3 of the engineering ranking. Genuine pause or extraction-side artifact is not yet decidable; it needs a longer window and a per-lab release-feed check. The one open question here with a direct product consequence. |
| **Witness a live talent move** | The synthesis path is verified end to end in the suite, but re-observation runs on a 7-day cadence and the first observation landed 2026-07-30, so the earliest a real move can be witnessed is 2026-08-06. Waiting, not building. |
| **Deepen person attribution** | 61 of 954 events reach a person (17 attributed to a person as the event's subject). The register, expansion and approval machinery works; official channels simply rarely name individuals outside paper bylines. GitHub and arXiv also track disjoint populations — the measured overlap is zero. |
| **Re-extract the back corpus** | The quote-first prompt revision cut the partial-entailment rate on the 30 hardest documents from 65.6% to 47.9%, but it applies only to new extractions, so part of the corpus predates it; the repair pass has since brought corpus-wide entailment to 97.5%. |
| **Grow the channel benchmark** | 100 posts, one annotator, one policy version. |

### 4. Deployment — unblocked, not done

None of the following is implemented. They are listed as *unblocked* because
nothing in the design prevents them: state is one SQLite file, the runtime is
plain Python, and model routing is a single dictionary in `fli/ops/llm.py`.

| | |
|---|---|
| **Vertex AI / Bedrock inference** | A provider entry in `fli/ops/llm.py` — `KEY_ENV`, `provider_for` and `PRICES` — rather than a rewrite. The OpenAI path already proves the second-provider shape works. |
| **Containerisation** | A Dockerfile over `requirements.txt`. The pipeline has no system dependencies; even the PDF renderer is dependency-free by design. |
| **Durable checkpointing** | The graph compiles with an `InMemorySaver`, which is sufficient because the approval pause and its resume happen in one CLI process. A long-running or distributed runner would need a real checkpointer. |

### 5. Governance

| | |
|---|---|
| **The policy has no domain owner** | `config/policy.yml` reads `owner: "BIT PM — unassigned"`, and the system prints `[NOT REVIEWED]` on every run rather than hiding it. Every weight in it is provisional until a portfolio manager signs off. This is not an engineering task, and it is the item that most limits what the scoring can claim. |
