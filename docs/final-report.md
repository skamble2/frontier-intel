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
ingested 1,514 documents from official channels, extracted **734 evidence-backed
events** from them, resolved **285 researchers across 322 identities** on arXiv,
GitHub, X and lab pages, and ranks every event twice — once for an investment
reader, once for an engineering reader — from 959 pairwise judge labels. It then
connects lab events to public-equity holdings, writes a per-audience reading of
each, and delivers a cited digest and an alert path. Total LLM spend to date is
under $20.

## The headline finding: one ranking cannot serve two readers

The most important structural result is not any single event — it is that
"important" is not one thing. The same 734 events, the same features, the same
clustering, ranked under two audience rubrics, produce two orderings that share
**2 of their top 10** and correlate at a Kendall τ of ≈ +0.04 — near zero. A model with
released weights and a reproducible method sits at the *top* of the engineering
ranking (DiffusionGemma, Qwen-AgentWorld) and near the *bottom* of the
investment one, while a token-price change sits high for the investor and
nowhere for the engineer. This is measured, not asserted, and it is the
justification for the entire two-persona design: a single "importance score"
would have quietly served one audience and mis-served the other.

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
connection the system exists to surface, and notably this event scores *below the median*
on the investment ranking — the rubric rewards specificity and shipped-ness, not
portfolio consequence — which is why the alert path triggers on the *signed
direction*, not the score.

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
*reproducible* work, exactly as the technical rubric intends. The top items are
DiffusionGemma (an open 26B text-diffusion model under a permissive licence),
Qwen-AgentWorld-35B-A3B (open-source MoE for agentic workloads), and Meta's
Content Seal provenance signal. The system's readings are correspondingly
restrained: the dominant verdict is *investigate* ("spike it in a sandbox,
benchmark against what we run"), with *adopt* reserved for the rare case of
released weights plus a clear reason to switch. This restraint is the honest
shape of a frontier-lab feed — most of what is published is not something a team
can pick up this quarter — and the digest says so in as many words rather than
inflating every release into an action.

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

**The two model judges agree with each other more than with the human.** On the
89 investment pairs a human has now labelled, the two models (Claude and GPT)
agree with *each other* at Cohen's **κ 0.54** (77% raw), but each agrees with
the **human at only κ ≈ 0.43** (70% raw). The Dawid–Skene estimate — which
infers accuracy from disagreement — rates the human at 0.843 and both models at
0.870, i.e. it ranks the two models *above* the human, precisely because they
agree with each other. That is the trap: two models sharing a training prior
corroborate each other, and a disagreement-based reliability score reads that
corroboration as accuracy. Only an independent human reference exposes it. The
system now refuses to compute Dawid–Skene without at least two independent model
*families*, and reports the human agreement alongside the model estimate rather
than trusting the models' mutual agreement. The practical reading: the models
are a usable ranking signal (κ 0.43 with a human is real agreement, not noise),
but they are not ground truth, and the highest-value labels to buy next are
human ones.

**The talent-mobility path is built but unwitnessed.** The system can synthesize
a personnel event from a researcher observed at two labs in succession, and the
test suite plants such a move and shows it reach the digest; the live corpus has
simply not yet witnessed one on the weekly re-observation cadence. The mechanism
is real; the data is not there yet, and the report distinguishes the two.

## Where the signal is strongest, and what it would take to deepen it

The learning curve is still climbing — the last 80→160 pairs of judge labels
moved held-out accuracy by ~7 points — so more labels are worth buying, and the
human audit shows the highest-value labels are human ones. The investment
product is sharpest where a lab enters a market a holding occupies
(displacement, which carries an inherent sign) and weakest on demand channels
(where the sign needs a per-event reading). The engineering product is already
dense, because "usable and reproducible" is a property the corpus carries on its
face. The single most valuable next investment would be more human pairwise
labels on the investment rubric, where the models' shared blind spot is largest.
