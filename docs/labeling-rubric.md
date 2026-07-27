# Labeling rubric — what makes a frontier-lab event decision-relevant to BIT Capital

**Purpose.** Ranking needs a ground truth, and "which event is more important?"
is not answerable without being a portfolio manager. This rubric replaces that
judgement with an **auditable application of BIT Capital's own published
investment theses**, so every label can be defended by pointing at the fund's
own words rather than at an opinion.

**Every claim below is sourced from `research/facts.md`, fetched live on
2026-07-22.** Nothing here is inferred from memory.

---

## 1. The structural fact that drives the whole rubric

> Flagship focus: "global technology and AI-infrastructure stocks", **market cap
> $2–100bn**, benchmark-independent, low index overlap, high-conviction
> weighting. — BIT Global Technology Leaders fund page

None of the tracked labs' parents sit in that band. Alphabet, Meta and Alibaba
are all far above $100bn; OpenAI, Anthropic, Mistral and DeepSeek are private.

**So a frontier-lab event is never directly tradeable for this fund.** It matters
only through what it implies for the $2–100bn suppliers, and BIT has told us
which suppliers it cares about by holding them:

| Held position | Since | BIT's stated thesis |
|---|---|---|
| MU (Micron) | 03.07.2023 | AI memory / HBM demand |
| IREN | 17.05.2022 | Power + datacenter capacity |
| RDDT | 12.06.2024 | AI training-data licensing |

Plus two live signals: the "memory supercycle" podcast (02.07.2026) and the
F.A.Z. headline BIT links from its own homepage, "TSMC löst Nvidia ab"
(20.11.2025).

**The labeling question is therefore:**

> Which of these two events moves a number in one of BIT's four transmission
> channels, more directly and sooner?

That is rubric application. It needs no finance expertise — only the fund's
published positions and honest reading.

---

## 2. The four transmission channels

A labeler must name exactly one channel (or `none`) for the winning event.

| # | `thesis_channel` | The question it answers | Anchor |
|---|---|---|---|
| A | `compute_memory` | Does this change how many chips / how much HBM the frontier labs will buy? | MU, TSMC/Nvidia |
| B | `energy_datacenter` | Does this change megawatts, siting, or datacenter build-out? | IREN |
| C | `data_economics` | Does this change what training data costs or who gets paid for it? | RDDT |
| D | `competitive_displacement` | Does this threaten or protect the revenue of a listed mid-cap? | benchmark-independent, high-conviction |
| — | `none` | Interesting to an AI researcher, inert to this fund | — |

---

## 3. Ordering rules, in strict precedence

Applied in order; the first that separates the pair decides it.

1. **Channel over no channel.** An event touching A–D beats one at `none`,
   however technically impressive the latter is.
2. **Quantity over topic.** An event that changes a *number* in a channel beats
   one that merely relates to it. "Trained on 100k H100s for 90 days" moves a
   number; "we care deeply about compute efficiency" does not.
3. **Sooner over later.** A shipped/contracted/hired fact beats a stated
   intention. Announced beats rumoured; rumoured beats speculated.
4. **Specific over vague.** Named parties, dates, magnitudes, model names.
   (This is the one axis the feature set already measures.)
5. **New over restated.** If the event shares a `cluster_id` with an earlier
   event, the earlier one carries the information; this one is an echo.
6. **Otherwise `tie`.** Ties are a legitimate answer and must not be
   discouraged — forcing a winner on a genuinely equal pair injects noise.

---

## 4. Explicit non-criteria

These are the failure modes to audit for. A label citing any of them is wrong
regardless of whether the outcome feels right.

- **Lab identity is never a reason.** "OpenAI matters more than Mistral" is
  banned. The register is deliberately de-skewed and per-lab precision@10 is
  reported as a fairness check. A labeler that leaks lab prestige into labels
  defeats both.
- **Technical impressiveness is not investment relevance.** A benchmark SOTA is
  channel `none` unless it implies compute, energy, data or displacement.
- **Benchmark results are weak by default.** 58 of 406 events are `benchmark`;
  most move no number in any channel.
- **Open-source releases cut both ways.** They can *reduce* inference demand
  (cheaper models) or *raise* it (wider deployment). If the direction is not
  stated in the evidence, it is not a reason.
- **Recency is not importance.** Recency is already a feature; the labeler must
  not double-count it beyond rule 3.

---

## 5. Who labels, and why that is defensible

**Primary labeler: an LLM applying this rubric**, emitting for every pair a
`winner`, a `thesis_channel`, and a one-line `reason` that must cite a rule
number from the ordering rules above. Cost is cents; ~150 pairs take minutes.

**Validity evidence: a human audit of ~40 pairs.** The auditor does *not* judge
importance — they check rubric compliance: was the cited rule correctly applied,
and does the reason survive reading the evidence quote? Agreement is reported as
Cohen's κ.

This is honest for three reasons:

1. **The rubric is externally anchored.** It is BIT's published thesis, not the
   labeler's taste. Disagreements become rubric disputes, which are resolvable.
2. **The labeler is audited, not assumed.** κ is reported whatever it says. A
   low κ is a finding about the rubric's clarity and gets written up as one.
3. **It matches the client's own method.** BIT describes itself as an "AI Native"
   platform running ">1bn tokens across 30+ LLM/LRM processes per month" and
   ">75% of trades influenced by systematic data signals and models". LLM
   annotation under human audit is their house style, not a shortcut around it.

**The circularity is stated, not hidden.** The ranker is trained on LLM
judgements, so it cannot exceed the labeler. But the labeler reads the full
document while the ranker sees only nine numeric features. The model is
therefore *compressing* a stated editorial policy, not imitating an oracle, and
the experiment's actual question is:

> How much of a written editorial policy is recoverable from nine cheap features?

If the answer is "little", that is reported prominently rather than buried. A
negative result here is a real finding about feature sufficiency at n=406, and
it is the honest counterweight to shipping an arbitrary weighted sum.

---

## 6. Why not let the market label it

Considered and rejected, with numbers.

- **Ticker coverage looks adequate**: DeepMind→GOOGL 135, Qwen→BABA 124,
  Meta AI→META 31 = 290 of 406 events (71%).
- **But the fund cannot trade any of them.** All three sit above the $2–100bn
  band the flagship targets. The label would measure moves in securities
  outside the mandate.
- **And it is not identifiable anyway**: 163 events across 29 days, median 5 per
  day, max 17. A daily return cannot be attributed to one of five same-day
  events without an intraday event-study design and a far longer window.

Running it regardless would produce a number with a confidence interval wide
enough to contain any conclusion. That is worse than not running it.

---

## 7. Reusable output

Each label names a `thesis_channel`, which is exactly the mapping needed to go
from event → thesis → ticker. The labeling pass therefore seeds the ticker map
rather than being thrown away after training.
