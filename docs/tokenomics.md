# Tokenomics

What the system spends, on what, and how the price of a token shaped which model
does which job.

Every figure here is queried from the `llm_calls` table in the committed
database — one row per API call, written at call time by `fli/ops/llm.py`, with
the model, the token counts the provider reported, and the dollar cost computed
from a pinned price table. Nothing is estimated. Regenerate the whole table
with:

```bash
sqlite3 data/fli.db < docs/metrics.sql > docs/metrics-out.txt   # sections M5a–M5b
```

## The bill

**$25.18 across 7,423 calls** — 6,167,889 input tokens and 816,504 output
tokens, over eight days (2026-07-25 to 2026-08-01).

| task | model | calls | input tok | output tok | USD | $/call | share |
|---|---|---:|---:|---:|---:|---:|---:|
| `judge` | claude-sonnet-5 | 2,673 | 2,924,907 | 272,338 | 12.868 | 0.00481 | 51.1% |
| `extract` | claude-sonnet-5 | 350 | 760,384 | 178,246 | 4.955 | 0.01416 | 19.7% |
| `label` | claude-sonnet-5 | 385 | 369,625 | 44,768 | 1.780 | 0.00462 | 7.1% |
| `judge` | gpt-5.2 | 762 | 574,996 | 46,537 | 1.658 | 0.00218 | 6.6% |
| `repair` | claude-sonnet-5 | 341 | 171,544 | 35,538 | 1.048 | 0.00307 | 4.2% |
| `verify` | claude-haiku-4.5 | 1,241 | 430,100 | 79,101 | 0.826 | 0.00067 | 3.3% |
| `channel` | claude-haiku-4.5 | 1,016 | 457,612 | 68,495 | 0.800 | 0.00079 | 3.2% |
| `persona` | claude-sonnet-5 | 92 | 85,448 | 26,339 | 0.651 | 0.00708 | 2.6% |
| `classify` | claude-haiku-4.5 | 433 | 328,715 | 29,045 | 0.474 | 0.00109 | 1.9% |
| `faithfulness` | claude-haiku-4.5 | 130 | 64,558 | 36,097 | 0.123 | 0.00094 | 0.5% |

The shape of this table is the main result: **the product costs almost nothing
to run; the evaluation is what costs money.** Extraction — the thing that
actually produces the 734 events a reader sees — is 19.7% of spend. Judging and
human-assisted labeling, which exist only to *validate* the ranking, are 64.8%
between them. That ratio is a deliberate choice, not an accident: a scoring
system that cannot be defended is worth less than one that is slightly worse and
measured, so the budget went where the defensibility is.

## The price table, and why it is pinned

```python
# fli/ops/llm.py
PRICES = {
    "claude-haiku-4-5-20251001": (1.00,  5.00),   # $ per 1M (input, output)
    "claude-sonnet-5":           (3.00, 15.00),
    "gpt-5.2":                   (1.75, 14.00),
}
PRICES_CHECKED_AT = "2026-07-28"
CACHE_WRITE_MULT = 1.25   # cache writes bill at 1.25x input
CACHE_READ_MULT  = 0.10   # cache reads bill at 0.10x input
BATCH_DISCOUNT   = 0.5    # the batch API halves the whole call
```

`cost_usd()` raises `KeyError` on an unknown model rather than defaulting to
zero or guessing a rate. A silent zero would make an expensive model look free
in exactly the table above, so the system refuses to price what it has not been
told the price of. Cache and batch tokens are billed separately because the API
reports them separately — folding them into `input_tokens` would understate a
cached call and overstate a batched one.

## Model selection per task

| task | model | why this one |
|---|---|---|
| `classify` | Haiku 4.5 | Binary substantive/not over a 6k-char prefix. A cheap gate in front of an expensive step; getting it slightly wrong costs one skipped document, not a wrong claim. |
| `extract` | Sonnet 5 | The one step that must not be wrong. It emits a claim plus a verbatim quote that has to re-match the source byte-for-byte; quote fidelity is where cheap models fail. |
| `repair` | Sonnet 5 | Rewrites a claim down to what its quote actually supports. Same fidelity requirement as extraction. |
| `judge` | Sonnet 5 + GPT-5.2 | Pairwise ranking under a rubric. Two *families*, not two prompts, because Dawid–Skene needs conditionally independent labelers. |
| `persona` | Sonnet 5 | Writes the reader-facing "what this means / what to do". Rests on judgment and tone; 92 calls, so the price barely registers. |
| `channel` | Haiku 4.5 | Picks a transmission channel from a fixed 5-item list. Closed-set classification, no generation. |
| `verify` | Haiku 4.5 | Claim↔quote entailment, three-way verdict. Closed-set again. |
| `faithfulness` | Haiku 4.5 | Same shape as `verify`, over persona notes. |

The rule underneath it: **Sonnet where a wrong answer enters the database as a
fact; Haiku where a wrong answer only costs a re-check.** Every closed-set
classification runs on Haiku. Every open-ended generation that produces a stored
claim runs on Sonnet.

Routing is one dictionary, `MODEL_FOR_TASK` in `fli/ops/llm.py`, so the
cost-quality trade-off for the whole system is legible in eight lines and
changeable in one.

## Three places cost actually changed a design decision

**1. The Haiku gate in front of Sonnet.** Stage 1 asks Haiku whether a document
is substantive at all before Sonnet is allowed to read it. It killed **102
documents** as `low_substance` — marketing, event promotion, job posts. The gate
itself cost $0.47. Running those same classification tokens on Sonnet would have
cost $1.42, and running the killed documents through Sonnet extraction would
have cost more again. Counting only the routing decision (not the documents
avoided), Haiku-routing the four closed-set tasks saved **$4.69 — 16% of the
counterfactual all-Sonnet bill**. The classifier's prefix is shared with the
extractor, so the 6k-token read is not paid twice.

**2. The expensive judge is not the better judge.** This is the finding that
most changed how the budget is spent:

| judge | judged | usable | low-conf | USD | $/usable label | Dawid–Skene accuracy |
|---|---:|---:|---:|---:|---:|---:|
| claude-sonnet-5 | 976 | 708 | 27.5% | 12.868 | **0.01817** | 0.864 |
| gpt-5.2 | 762 | 594 | 22.0% | 1.658 | **0.00279** | 0.874 |

GPT-5.2 costs **6.5× less per usable label** and its estimated reliability is
*marginally higher*, not lower. It also returns fewer low-confidence verdicts
(22.0% vs 27.5%), so more of what it is paid for survives into training. The
honest reading is that Sonnet's price is buying nothing measurable on this task —
the pairwise rubric decision is not hard enough to need it. Sonnet-as-judge
remains in the system because Dawid–Skene requires a second independent family
and because it is the incumbent the second family was introduced to check, but
**the next tranche of labels should be bought from the cheaper judge**, and at
these rates the same $12.87 would buy roughly 4,600 usable labels instead of 708.
The learning curve (f11) says more labels are still worth buying, so this is the
single highest-leverage cost decision left in the system.

**3. Spend caps that refuse rather than truncate.** `--max-extract` caps events
per run; the paid X source is gated behind `--dry-run` with a projected cost;
`judge --dry-run` previews spend before sending. Each paid entry point runs
`preflight`, which checks the API key *and* that a price exists for the model,
and refuses to start a run it cannot price. The failure mode being designed
against is a loop that silently spends the whole budget on a bad prompt.

## Cache and batch: built, barely used

Both discounts are implemented and both are visible in the schema
(`cache_write_tokens`, `cache_read_tokens`, and a `--batch` flag that halves the
call). In the committed corpus only the judge wrote cache tokens — **2,087 cache
writes and 0 cache reads**. Zero reads means the 5-minute ephemeral TTL expired
between calls in every run, so the caching paid the 1.25× write premium and
collected none of the 0.10× read discount.

That is a small net loss, honestly reported rather than quietly dropped: on
judge input alone the premium cost roughly $0.002. The fix is not more caching
but batching — judge runs are embarrassingly parallel and the batch API is a
flat 50% off, which would have taken the $12.87 judge line to about $6.43. The
`--batch` path exists and is tested; it simply post-dates most of the labels in
the committed DB.

No reasoning tokens were billed on any model (`M5a2`: 0 across all three), so
none of the output cost above is invisible thinking.

## Unit economics

| unit | cost |
|---|---|
| per stored event, all-in (total spend ÷ 734 events) | **$0.0343** |
| per stored event, extraction only | $0.0068 |
| per `extract` call (≈2.1 events) | $0.0142 |
| per `classify` call | $0.0011 |
| per usable judge label (Sonnet) | $0.0182 |
| per usable judge label (GPT-5.2) | $0.0028 |

**What a marginal day costs.** Splitting spend by date, a routine day that
ingests, filters, extracts and delivers — without buying new judge labels —
lands around **$1–2**. 2026-08-01 is the clean example at $2.23, and $1.05 of
that was a one-off claim-repair pass over the whole back corpus. The recurring
daily cost of running the product is therefore roughly **$1**, and the $25 total
is dominated by one-time evaluation work: 3,435 judge calls, a repair pass, and
a channel-classification sweep over the entire corpus.

Per-day totals:

| date | calls | USD | what dominated |
|---|---:|---:|---|
| 2026-07-25 | 334 | 2.76 | first extraction runs |
| 2026-07-26 | 1,529 | 5.70 | corpus extraction |
| 2026-07-27 | 1,164 | 5.38 | extraction + first judging |
| 2026-07-28 | 926 | 3.59 | judging |
| 2026-07-29 | 114 | 1.16 | judging |
| 2026-07-30 | 2,043 | 3.16 | judge 1.60 · verify 0.54 · persona 0.51 · channel 0.51 |
| 2026-07-31 | 346 | 1.19 | judge label push |
| 2026-08-01 | 967 | 2.23 | repair 1.05 · extract 0.60 · verify 0.28 |

## What this cost profile means for scaling

The corpus is 1,541 documents and 8 labs. Spend divides cleanly by what it
scales with:

| scales with | tasks | USD | share |
|---|---|---:|---:|
| corpus size | `classify` `extract` `verify` `channel` `faithfulness` `repair` | 8.22 | 33% |
| label count | `judge` `label` | 16.31 | 65% |
| slate size | `persona` | 0.65 | 3% |

The corpus-scaling half is about **$0.0053 per document**. Tripling the number
of tracked labs would move it to roughly $25 and change nothing structurally —
and most of that is `extract`, the one line where paying for Sonnet is defended.

The parts that scale with *label count* — `judge` and `label` — are $16.31, and
they do not need to grow with the corpus at all. They need to grow until the
learning curve flattens, which f11 says it has not. Buying those labels from
GPT-5.2 rather than Sonnet is the difference between a few hundred more labels
and a few thousand for the same money.

The budget for this case study was €100. **$25.18 was spent — about a quarter of
it** — and the binding constraint was never money; it was how many pairs a human
could label in a sitting.
