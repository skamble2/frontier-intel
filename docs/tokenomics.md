# Tokenomics

What the system spends, on what, and how the price of a token shaped which model
does which job.

Every figure here is queried from the `llm_calls` table in the committed
database — one row per API call, written at call time by `fli/ops/llm.py`, with
the model, the token counts the provider reported, and the dollar cost computed
from a pinned price table. Nothing is estimated. Regenerate the whole table
with:

```bash
sqlite3 data/fli.db < docs/metrics.sql > docs/metrics-out.txt
```

## The bill

**$29.87 across 8,707 calls** — 7,344,833 input tokens and 956,824 output
tokens, over nine days (2026-07-25 to 2026-08-02).

| task | model | calls | input tok | output tok | USD | $/call | share |
|---|---|---:|---:|---:|---:|---:|---:|
| `judge` | claude-sonnet-5 | 3,260 | 3,597,966 | 321,458 | 15.624 | 0.00479 | 52.3% |
| `extract` | claude-sonnet-5 | 408 | 923,343 | 221,857 | 6.136 | 0.01504 | 20.5% |
| `label` | claude-sonnet-5 | 385 | 369,625 | 44,768 | 1.780 | 0.00462 | 6.0% |
| `judge` | gpt-5.2 | 762 | 574,996 | 46,537 | 1.658 | 0.00218 | 5.6% |
| `repair` | claude-sonnet-5 | 449 | 226,127 | 46,719 | 1.379 | 0.00307 | 4.6% |
| `verify` | claude-haiku-4.5 | 1,568 | 544,068 | 99,882 | 0.970 | 0.00062 | 3.2% |
| `channel` | claude-haiku-4.5 | 1,128 | 507,239 | 76,187 | 0.844 | 0.00075 | 2.8% |
| `persona` | claude-sonnet-5 | 109 | 100,083 | 30,292 | 0.755 | 0.00693 | 2.5% |
| `classify` | claude-haiku-4.5 | 508 | 436,828 | 33,027 | 0.602 | 0.00119 | 2.0% |
| `faithfulness` | claude-haiku-4.5 | 130 | 64,558 | 36,097 | 0.123 | 0.00095 | 0.4% |

The shape of this table is the main result: **the product costs almost nothing
to run; the evaluation is what costs money.** Extraction — the thing that
actually produces the 954 events a reader sees — is 20.5% of spend. Judging and
human-assisted labeling, which exist only to *validate* the ranking, are 63.8%
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
is substantive at all before Sonnet is allowed to read it. It killed **119
documents** as `low_substance` — marketing, event promotion, job posts. The gate
itself cost $0.60. Running those same classification tokens on Sonnet would have
cost $1.81, and running the killed documents through Sonnet extraction would
have cost more again. Counting only the routing decision (not the documents
avoided), Haiku-routing the four closed-set tasks saved **$5.80 — 16% of the
counterfactual all-Sonnet bill**. The classifier's prefix is shared with the
extractor, so the 6k-token read is not paid twice.

**2. The expensive judge is not the better judge.** This is the finding that
most changed how the budget is spent:

| judge | calls | stored labels | low-conf | USD | $/label | Dawid–Skene accuracy |
|---|---:|---:|---:|---:|---:|---:|
| claude-sonnet-5 | 3,260 | 1,546 | 27.0% | 15.624 | **0.0101** | 0.864 |
| gpt-5.2 | 762 | 762 | 22.0% | 1.658 | **0.0022** | 0.874 |

GPT-5.2 costs **4.6× less per stored label** and its estimated reliability is
*marginally higher*, not lower. It also returns fewer low-confidence verdicts
(22.0% vs 27.0%), so more of what it is paid for survives into training. The
honest reading is that Sonnet's price is buying nothing measurable on this task —
the pairwise rubric decision is not hard enough to need it. Sonnet-as-judge
remains in the system because Dawid–Skene requires a second independent family
and because it is the incumbent the second family was introduced to check. The
learning curve has since plateaued (+0.004 on the last doubling), so the lesson
is carried forward as a routing rule rather than a purchase order: whatever
labels are bought next should come from the cheaper family — see
[future scope](final-report.md#future-scope).

**3. Spend caps that refuse rather than truncate.** `--max-extract` caps events
per run; the paid X source is gated behind `--dry-run` with a projected cost;
`judge --dry-run` previews spend before sending. Each paid entry point runs
`preflight`, which checks the API key *and* that a price exists for the model,
and refuses to start a run it cannot price. The failure mode being designed
against is a loop that silently spends the whole budget on a bad prompt.

The graph runner makes this structural rather than procedural. `graph --spend`
is the only way to reach `verify`, `personas` and `faithfulness`, and
`_spend_ready` requires the flag *and* an API key; without both, the conditional
edges route around those nodes entirely, so **a default `graph` run costs exactly
what `pipeline` costs.** The paid stages are not skipped at runtime — they are
not on the path. Five tests pin it, including "spend without a key still skips
paid stages".

The flag is not the last word, though, and deliberately so: a flag is set before
the operator knows what the run will find. An `approve` node raises a LangGraph
`interrupt` that **pauses the run** and prints the work sized from the current
database before asking — `unaudited_claims` (insights with no `claim_checks`
row) and `existing_notes`. On the committed corpus that reads
`unaudited_claims: 0`, which is the gate doing its job: there is nothing for the
paid audit to do, so the honest answer is `n` and the run costs nothing.
Declining is not an error and one answer covers both paid segments; `--yes`
skips the pause for schedulers, and a missing tty declines rather than hanging.

The cost story here is small but real: the most expensive run is the one nobody
meant to start. Sizing the work at the moment of decision is cheaper than any
model-routing optimisation in this document.

Worth noting what is deliberately free: the `drift` node runs on every graph
invocation and costs **$0** — PSI and KS are computed directly from SQL, with no
scipy and no model call. Monitoring that costs money gets switched off, so the
corpus-shape signal was built to have no per-run price.

**The one source that is not an LLM cost at all.** X is the only paid *source*,
and it was originally postponed on price: X Basic was a $200/month
subscription, which is not defensible for one signal on a €100 budget. X moved
new developers to pay-per-use in early 2026 — $0.005 per post read, $0.010 per
user read, no minimum, deduplicated within a 24-hour UTC window — which puts a
full sweep of the tracked lab accounts at about **$0.77**. The decision reversed
because the input changed, not because the reasoning did.

The first live run cost **$0.175** for 29 posts, and those 29 were immediately
frozen into a JSON fixture. Every number measured against them since has cost
nothing, which is the general pattern worth copying: pay once for real data,
then make the evaluation re-runnable offline forever.

## Cache and batch: from built-but-idle to earning

Both discounts are implemented, and the ledger says exactly how much each has
actually earned.

**Caching now pays.** For most of the corpus's history it did not: early judge
runs wrote cache tokens whose 5-minute ephemeral TTL expired between calls,
paying the 1.25× write premium and collecting none of the 0.10× read discount.
Extraction reversed that. Across the corpus:

| task | cache write | cache read |
|---|---:|---:|
| `extract` | 4,828 | **65,178** |
| `judge` | 2,087 | 0 |

The judge line is still the old pattern — premium paid, nothing collected — but
extraction now reads roughly 13× what it writes, which is the discount working
as intended.

**Batching is real but narrow.** `llm_calls` has no batch column, so the flag is
not directly queryable; it is recoverable arithmetically, because a batched call
stores half its full-price cost. Recomputing every row from its tokens and the
pinned price table:

| task | batched calls | of total | saved |
|---|---:|---:|---:|
| `verify` | 220 | 1,568 | $0.07 |
| `faithfulness` | 130 | 130 | $0.12 |
| `channel` | 112 | 1,128 | $0.04 |
| everything else | 0 | — | — |

**462 of 8,707 calls, saving $0.24.** The two lines that would actually benefit —
`judge` at $17.28 and `extract` at $6.14, 78% of all spend between them — have
never been through it: the path is Anthropic-only and post-dates the bulk of
both. Routing them through it is worth roughly $11 on a re-run, which is why
batching-by-default is carried into
[future scope](final-report.md#future-scope).

No reasoning tokens were billed on any model — zero across all three — so
none of the output cost above is invisible thinking.

## Unit economics

| unit | cost |
|---|---|
| per stored event, all-in (total spend ÷ 954 events) | **$0.0313** |
| per stored event, extraction only | $0.0064 |
| per `extract` call (≈2.3 events) | $0.0150 |
| per `classify` call | $0.0012 |
| per stored judge label (Sonnet) | $0.0101 |
| per stored judge label (GPT-5.2) | $0.0022 |

**What a marginal day costs.** Splitting spend by date, a routine day that
ingests, filters, extracts and delivers — without buying new judge labels —
lands around **$1–2**: 2026-07-29 and 2026-07-31 are the clean examples at
$1.16 and $1.19. The recurring daily cost of running the product is therefore
roughly **$1**, and the $30 total is dominated by one-time evaluation work:
4,022 judge calls, a repair pass, and a channel-classification sweep over the
entire corpus.

Per-day totals:

| date | calls | USD | what dominated |
|---|---:|---:|---|
| 2026-07-25 | 334 | 2.76 | first extraction runs |
| 2026-07-26 | 1,529 | 5.70 | judging + human-assisted labeling |
| 2026-07-27 | 1,164 | 5.38 | judging + extraction |
| 2026-07-28 | 926 | 3.59 | judging |
| 2026-07-29 | 114 | 1.16 | extraction |
| 2026-07-30 | 2,043 | 3.16 | judge 1.60 · verify 0.54 · persona 0.51 |
| 2026-07-31 | 346 | 1.19 | judge label push |
| 2026-08-01 | 967 | 2.23 | repair 1.05 · extract 0.60 · verify 0.28 |
| 2026-08-02 | 1,284 | 4.69 | corpus refresh: judge 2.76 (batched) · extract 1.18 · repair 0.33 |

## What this cost profile means for scaling

The corpus is 1,675 documents and 8 labs. Spend divides cleanly by what it
scales with:

| scales with | tasks | USD | share |
|---|---|---:|---:|
| corpus size | `classify` `extract` `verify` `channel` `faithfulness` `repair` | 10.05 | 34% |
| label count | `judge` `label` | 19.06 | 64% |
| slate size | `persona` | 0.75 | 3% |

The corpus-scaling half is about **$0.0060 per document**. Tripling the number
of tracked labs would move it to roughly $30 and change nothing structurally —
and most of that is `extract`, the one line where paying for Sonnet is defended.

The parts that scale with *label count* — `judge` and `label` — are $19.06, and
they do not need to grow with the corpus at all. They needed to grow until the
learning curve flattened, and it now has: the last doubling of pairs
moved held-out accuracy +0.004. The label budget is roughly spent; what remains
is feature work and human labels, which buy a different thing than more judge
consensus.

Against a €100 budget, **$29.87 was spent — under a third of it.** The binding
constraint was never money; it was how many pairs a human could label in a
sitting.
