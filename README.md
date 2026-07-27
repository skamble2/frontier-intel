# Frontier Lab Intelligence

Tracks what the frontier AI labs and their researchers actually ship, turns it
into cited, ranked intelligence, and delivers it to a reader who has to make a
decision.

Built as a take-home case study for **BIT Capital**, a Berlin technology fund.

---

## The one idea

**Nothing enters the system without evidence, and nothing survives that cannot
be re-verified against the bytes it came from.**

Every insight carries an `evidence_id` (`NOT NULL`). Every quote is re-checked
against the stored document — not at write time, but again on every validation
run. If a source edits a page, or a bad extraction slipped through once, the
battery says so.

That single invariant is why the numbers below can be trusted, and it is the
reason the honest quote-verification rate went **down** from 99.8% to 95.5%
when a bug was fixed: the old figure was high because failures were not being
counted.

---

## What it does

```
21 sources          →  fetch, store immutably, hash-dedupe
(blogs, newsrooms,     ↓
 arXiv, GitHub)     stage 1: deterministic filter (free)
                       ↓
                    stage 2: LLM classify (Haiku) → extract (Sonnet)
                       ↓
                    quote verification — the gate
                       ↓
                    cluster → features → score
                       ↓
                    persona-tailored report with citations
```

Current corpus: **406 events** from 361 documents, 1,514 evidence rows, across
7 tracked labs.

---

## Layout

Packages mirror the data model, so the code map and the schema are the same
picture.

```
fli/core/           text, http, config, paths, policy
fli/storage/        SQLite persistence — no domain logic
fli/ingestion/      LAYER 1  raw sources
fli/knowledge/      LAYER 2  filtering, extraction, register
fli/intelligence/   LAYER 3  clustering, features, labels, scoring
fli/ops/            LLM client, tracing (cross-cutting)
fli/validation/     C1–C17 invariant battery
fli/orchestration/  pipeline, skeleton — composition only
```

Layers communicate **only through the database**, which is what makes each one
runnable and testable on its own. `tests/test_architecture.py` fails the build
if a lower layer imports a higher one.

---

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
echo "ANTHROPIC_API_KEY=sk-..." > .env

python3 -m fli.cli pipeline      # the full daily cycle
python3 -m fli.cli checks        # invariant battery; exit 0 = green
python3 -m unittest discover -s tests -t .
```

Any layer also runs alone — `python3 -m fli.cli ingest|filter|cluster|score|…`
Full operational detail in [`RUNNING.md`](RUNNING.md).

Without an API key everything deterministic still runs and stays green; only
LLM extraction is skipped.

---

## Two decisions worth knowing about

**Editorial policy is configuration, not code.** What counts as
"decision-relevant" is a business judgement, and the author of this repository
is an engineer, not a portfolio manager. So it lives in
[`config/policy.yml`](config/policy.yml) with a named owner, a version, and its
provenance in BIT's own published theses. Every scored event records the policy
version that produced it. The code contains no business judgement, and the
owner field currently reads `unassigned` — which the system prints on every run
rather than hiding.

**Importance is not treated as ground truth.** Nobody here can credibly say
which of two events matters more to a fund. Rather than pretend otherwise, the
system records *who* made each judgement — an LLM, a human, or a weak labeling
function — and estimates their reliability from disagreement. What is validated
is the *instrument*: how many judgements it takes to recover a known policy, and
how much annotator noise it tolerates. See
[`docs/scoring-without-ground-truth.md`](docs/scoring-without-ground-truth.md).

---

## Documentation

| file | what it covers |
|---|---|
| [`RUNNING.md`](RUNNING.md) | how to run every layer |
| [`docs/erd.mermaid`](docs/erd.mermaid), [`docs/hld.mermaid`](docs/hld.mermaid) | data model, high-level design |
| [`docs/labeling-rubric.md`](docs/labeling-rubric.md) | what makes an event decision-relevant, from BIT's published theses |
| [`docs/scoring-without-ground-truth.md`](docs/scoring-without-ground-truth.md) | the scoring design and why it is shaped that way |
| [`docs/evaluation-plan.md`](docs/evaluation-plan.md) | every metric, and which ones are honest where |
| [`docs/metrics.sql`](docs/metrics.sql) | reproducible metrics harness |
| [`docs/report-notes.md`](docs/report-notes.md) | measured findings for the write-up |

---

## Stated limitations

Kept here rather than buried, because a system that hides its boundaries is not
an intelligence system.

- **Person attribution resolves on 1 of 406 events.** The register, expansion
  and approval machinery works, but the corpus rarely names individuals.
- **`personnel` events are 2 of 406 (0.5%).** The policy ranks them highly; the
  sources barely produce them.
- **Some launches are behind JavaScript.** Those pages are captured manually and
  stored under the same immutability rules, rather than weakening the evidence
  invariant to accommodate them.
- **The policy has no domain owner.** Every weight in `policy.yml` is
  provisional until one signs off, and the system says so on every run.
