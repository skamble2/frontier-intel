# Scoring without ground truth — work order

## The problem, stated plainly

Ranking needs a notion of "important". Nobody on this project is qualified to
supply it: the author is an engineer, not a portfolio manager. The usual answers
are both weak here.

- **"I labeled 150 pairs myself"** — an interviewer knows the author is not a PM.
  The labels are then an undefended opinion wearing a lab coat.
- **"I used an LLM as judge"** — BIT already runs ">1bn tokens across 30+ LLM/LRM
  processes per month". LLM-as-judge is their day job. It differentiates nothing,
  and it moves the unanswered question one level up: how good is the judge?

So this project takes a different position:

> **Do not claim the policy. Prove the instrument.**
>
> "Importance" is a *stated editorial policy*, owned by a named domain expert and
> versioned as configuration. What an engineer can legitimately validate is the
> **machinery that turns a person's judgements into a ranker** — that it is
> identifiable, sample-efficient, and noise-tolerant. That validation needs no
> finance knowledge at all, because the ground truth is planted by us.

Four components. Each is independently shippable; see the stop rule in §6.

---

## 1. Policy is configuration, not code

**Problem being fixed.** Today `SLATE_K = 5`, the rule `corroborated + top-K per
lab by paper_count`, and eight `HAND_WEIGHTS` numbers live in Python. That last
one is precisely the "arbitrary weighted sum" the brief names as a red flag —
currently indistinguishable from a number someone made up, because it is one.

Create `config/policy.yml`, read on every run:

```yaml
version: 1
owner: "BIT PM — unassigned"        # deliberately NOT the author of this repo
effective_from: 2026-07-25
provenance: research/facts.md       # BIT's published theses, fetched 2026-07-22
notes: >
  Editorial policy for what counts as decision-relevant. Every value here is a
  business decision, not an engineering one. Changing a number here changes the
  ranking and is recorded as a new version; the code is not touched.

channels:                            # labeling-rubric.md §2
  compute_memory:
    anchor_positions: [MU, TSMC]
    lexicon: [gpu, h100, tpu, hbm, wafer, fab, flops, training run, cluster]
  energy_datacenter:
    anchor_positions: [IREN]
    lexicon: [megawatt, datacenter, power, grid, cooling, campus]
  data_economics:
    anchor_positions: [RDDT]
    lexicon: [licensing, training data, corpus, copyright, content deal]
  competitive_displacement:
    anchor_positions: []
    lexicon: [deprecates, replaces, api pricing, free tier, open weights]

event_type_prior:                    # ordinal, not magic weights
  infrastructure: 5
  commercial: 4
  personnel: 4
  release: 3
  research: 2
  open_source: 2
  benchmark: 1
  other: 0

register:
  slate_k: 5
  rule: corroborated_top_k_per_lab
  min_paper_count: 1

scoring:
  hand_weights_v1:                   # a NAMED, DATED policy - a baseline to beat,
    recency: 1.0                     # never the shipped ranker
    corroboration: 0.6
    specificity: 0.5
    attribution_confidence: 0.4
    quote_len_words: 0.2
```

**Requirements**

- `fli/core/policy.py` loads and validates it; unknown keys are an error, not a
  silent default.
- `event_scores` gains `policy_version INTEGER NOT NULL`.
- New check **C17**: every `event_scores` row cites a policy version present in
  `config/policy.yml`; no score is attributable to an unknown policy.
- `config/register_overrides.yml` stays as-is and keeps its precedence. Note for
  the write-up: **it is currently empty — `approve:` and `reject:` are both
  blank.** No person in the register was hand-picked; the rule produced all 14
  approvals. The file exists so the policy owner can intervene without a code
  change, and that is a claim we can make honestly.

**What this buys in the interview.** *"The weighted sum isn't mine and isn't
hidden. It's `hand_weights_v1` in a versioned config with a named owner, it is
carried as the baseline to beat, and every score in the database records which
policy version produced it."*

---

## 2. Synthetic policy recovery — the rigor showcase

**This is the differentiated component.** It validates the learner using ground
truth we generate, so it requires zero finance knowledge and zero human time.

`fli/intelligence/simulation.py`. Plant known policies, generate labels from
them, and measure whether the pipeline recovers them.

### Policies to plant

Planting only a linear policy and recovering it with a linear model proves
nothing — that objection will be raised, so pre-empt it by planting four:

| id | form | why it is there |
|---|---|---|
| `linear` | weighted sum over standardised features | sanity check: if this fails, the fitter is broken |
| `lexicographic` | the rubric's §3 precedence rules, applied in order | **the honest test** — the real rubric is lexicographic, not linear |
| `threshold_interaction` | important only if `channel present AND specificity > τ` | can additive features represent a conjunction? |
| `noisy_human` | `lexicographic` + p% flips + tie-proneness | what a real annotator actually is |

### Measure, per policy

1. **Recovery** — Spearman correlation between fitted coefficients and planted
   weights (where defined).
2. **Learning curve** — held-out pairwise accuracy at
   n ∈ {10, 20, 40, 80, 150, 300} labels. Report **n\*** = labels needed to
   reach 95% of asymptotic accuracy.
3. **Noise tolerance** — sweep flip rate p ∈ {0, 5, 10, 20, 30}% at fixed n;
   report the p at which recovery degrades below a stated threshold.

### Acceptance

- `linear` recovers with Spearman ≥ 0.9 at n ≥ 80. **If it does not, the bug is
  in the learner, not the labels** — this doubles as a regression test for
  `scoring.py` and should live in `tests/intelligence/test_simulation.py`.
- `lexicographic` recovery is reported *whatever it is*, and is expected to be
  worse. That gap is the most informative number in the project (see §5).
- Deterministic: seeded, offline, no LLM, no network, completes in < 60s.
- Output is a **file** (`docs/simulation-results.md`), not a table. Nothing
  JOINs to simulation results and no invariant depends on them, so by the
  project's own storage criterion (plan §24.3) they do not belong in the DB.

**What this buys in the interview.** *"I can't validate the policy — I'm not a
PM. I validated the instrument. Here is the learning curve: a PM needs to give
me n\* judgements, not an afternoon. Here is the noise tolerance: they can be p%
inconsistent and it still recovers. And here is where a linear model fails to
represent a lexicographic rubric, which tells me what feature to build next."*

---

## 3. Weak supervision — model the noise instead of denying it

Replace "one gold labeler" with **many noisy labelers whose reliabilities are
estimated from their disagreement structure**, using Dawid–Skene EM (1979).
No gold labels required. ~60 lines of numpy; do not add a dependency.

**Reuse the existing table.** `pairwise_labels` already has
`UNIQUE (event_a, event_b, labeler)`, so every source is just another labeler:

| `labeler` | source |
|---|---|
| `lf:corroboration` | cluster size difference |
| `lf:specificity` | numeric/version token density |
| `lf:channel_lexicon` | channel keyword hits, from `policy.yml` |
| `lf:event_type_prior` | ordinal prior, from `policy.yml` |
| `lf:novelty` | cluster canonical vs echo |
| `llm:<model>` | the rubric judge (Task 3a) |
| `human:<name>` | the audit (Task 3b) |

Each labeling function votes `a` / `b` / abstain. Dawid–Skene returns a posterior
label per pair **and an estimated accuracy per labeler**.

### The two results worth reporting

1. **Estimated accuracy of the LLM judge, derived without gold labels.** This is
   the direct answer to "how do you know your judge is any good?"
2. **Cross-check that estimate against the 40 human audits.** If an unsupervised
   estimate lands near the supervised one, that is real evidence — and if it does
   not, that is a finding about labeling-function correlation, reported as one.

Train the ranker on Dawid–Skene posteriors (soft labels), not on raw LLM votes.

### Stated limitation

Dawid–Skene assumes labeling functions are conditionally independent given the
true label. Ours are **not** — `lf:specificity` and `quote_len_words` are close
to collinear. Mitigate by dropping near-duplicate LFs and report the correlation
matrix so the assumption's violation is visible rather than assumed away.

---

## 4. Active elicitation — treat expert time as the scarce resource

`fli/intelligence/elicitation.py`. Do not ask for 150 random pairs. Fit on
whatever labels exist, then select the next batch by uncertainty (|p − 0.5|)
subject to lab/type diversity so §22.5 stratification survives.

**Measure and report:** labels-to-convergence under active vs random sampling,
using the §2 learning-curve harness. If active sampling reaches the same accuracy
with materially fewer labels, that is the headline; if it does not at this scale,
report that instead — small-n active learning frequently does not help, and
saying so is better than implying it did.

**Framing:** the expensive input is a portfolio manager's attention. The system
is designed to spend as little of it as possible, and that cost is measured
rather than asserted.

---

## 5. The honest limitation, stated before anyone asks

**Synthetic recovery validates the learner, not the features, and not the
policy.** It proves that *if* a policy is expressible in the feature space, the
pipeline will find it from n\* judgements. It says nothing about whether nine
surface features can express what a real PM actually means by "important".

That question is answered — negatively or positively — by the `lexicographic`
row of §2 and by the ablation in `day5-scoring-spec.md`. If a linear model over
nine features cannot recover even a *known* lexicographic rubric, then it will
not recover a real PM's policy either, and the correct conclusion is:

> The features are the bottleneck, not the labels. The next experiment is a
> channel classifier that encodes the transmission channels directly, not more
> annotation.

Reaching that conclusion from evidence is a better outcome than a ranker with an
unfalsifiable accuracy number.

---

## 6. Order and stop rule

Ordered so that **the two components needing no labels come first** — the whole
scoring pipeline can be validated before a single judgement is collected.

| # | component | needs | est. |
|---|---|---|---|
| 1 | `policy.yml` + `core/policy.py` + C17 | nothing | ~1h |
| 2 | synthetic recovery + learning curves | nothing | ~2h |
| 3 | labeling functions + Dawid–Skene | policy.yml | ~2h |
| 4 | LLM rubric labels (Task 3a) | rubric | ~30m |
| 5 | active elicitation | 1–3 | ~1h |
| 6 | human audit, κ, cross-check (Task 3b) | 4 | ~30m human |

**Stop rule.** This is more than one day, and Day 6 (personas/reports, 15%) is
unbuilt. If time runs short, ship **1 + 2 + 4** and write up 3 and 5 as specified
but not evaluated. Components 1 and 2 carry the differentiated argument on their
own: policy is owned configuration, and the instrument is validated. Do **not**
sacrifice Day 6 for component 5.

**New checks**

- **C17** — every `event_scores` row cites a policy version present in `config/policy.yml`
- **C18** — every labeler in `pairwise_labels` has an estimated reliability in the
  label-model output, or is explicitly excluded with a reason

---

## 7. What this answers, in one paragraph

*I did not have access to a portfolio manager, so I did not pretend to be one.
Importance lives in `config/policy.yml` as a versioned, owned, dated artifact with
its provenance in BIT's published theses — the code contains no business
judgement. What I validated is the machinery: planted policies of four different
functional forms, measured how many judgements are needed to recover each, and
measured how much annotator noise it tolerates. Labels come from six weak sources
plus an LLM judge, and their reliabilities are estimated from disagreement
structure with no gold data, then cross-checked against a small human audit. The
most useful result is negative: a linear model over nine surface features cannot
represent a lexicographic rubric, so the bottleneck is the feature space, not the
annotation budget.*
