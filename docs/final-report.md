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
each, and delivers a cited digest and an alert path. Total LLM spend to date is
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

*(The investment ranks skip numbers because the raw corpus ranking does not
collapse clusters: ranks 3/4/5 are three near-identical reports of the same $40M
commitment, 6/7 one DiffusionGemma story, 8/9 one pricing story. Distinct
stories are shown. The engineering top 10 has no such duplicates. The reader
never sees them either — the delivered slate suppresses them — but it is a real
defect in the diagnostic, discussed under the honest negatives below.)*

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

## Insight 5 (corpus) — lab coverage is 6× skewed by channel style, and the ranking corrects rather than amplifies it

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

**The corpus ranking does not collapse clusters; only the delivered slate does.**
Clustering correctly groups near-duplicate claims — the three reports of Google's
$40M Genesis commitment all carry `cluster_id` 32 — but `event_scores.rank`
orders all 734 events without consulting that grouping, so the raw investment
top 10 contains only **6 distinct stories**. A reader never sees this: the slate
builder (`fli/intelligence/scoring.py`, `seen_clusters`) enforces one item per
cluster and reports what it suppressed, and the committed investment digest says
so in as many words: *"669 no mechanism, 21 lab cap, 14 outside window, 11 not
entailed, 4 duplicate cluster, 4 same story, 3 undated."* So the product is correct and
the diagnostic is not — but the divergence figure above is computed on raw
ranks, which means its "top 10" is really a top 6. The fix is to rank on cluster
representatives rather than events, and it is the first thing on the next list.

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

## Where the signal is strongest, and what it would take to deepen it

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
(where the sign needs a per-event reading). The engineering product is already
dense, because "usable and reproducible" is a property the corpus carries on its
face. The single most valuable next investment would be more human pairwise
labels on the investment rubric, where the models' shared blind spot is largest.
