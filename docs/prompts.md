# Prompts and their rationale

Every place this system asks a language model, and why the prompt is written the
way it is. Five prompts do all the LLM work: a classifier gate, an extractor, a
channel classifier, a pairwise judge, and a per-audience reader. Each is quoted
here verbatim from the code, with the design decisions that shaped it.

A principle runs through all five: the prompt is treated as an interface, not a
suggestion. The parser that reads each reply is strict — a reply that omits its
working, or answers a different question than the one asked, is rejected rather
than stored — so the prompt has to specify the reply contract exactly, and the
rationale below is largely about *what the strictness is protecting against*.

---

## 1. The classifier gate

`fli/knowledge/extraction.py` — `CLASSIFY_SYSTEM`. Runs on cheap Haiku.

```
You classify documents from frontier AI lab channels.
Return ONLY JSON: {"event_type": one of <types>,
"substantive": true|false, "reason": "<one line>"}.
substantive=false means: marketing fluff, event promotion, job posting,
or no concrete technical/personnel/product information.
```

**Why it exists.** Extraction is the expensive call, and roughly a quarter of
the corpus is marketing, event promotion, or job posts that will yield no event.
Paying Sonnet to read them is waste. This gate runs first on Haiku, over the
same 6k-token prefix the extractor will see, so the prefix is not paid for
twice, and it kills ~28% of documents before the expensive stage.

**Why `substantive` is defined by negatives.** "Substantive" is vague; the
failure modes are concrete. Listing what non-substantive *looks like* —
marketing, promotion, a job posting — gives the model a sharper boundary than an
abstract definition would, and it names the exact things this corpus is full of.

---

## 2. The extractor

`fli/knowledge/extraction.py` — `_EXTRACT_TEMPLATE`. Runs on Sonnet.

```
You extract the decision-relevant events from a
frontier-AI-lab document for an investment fund's intelligence system.
A document MAY contain several DISTINCT events — e.g. a launch may carry a model
release, its pricing, benchmark results, and safety measures — but MOST contain
one. An arXiv abstract almost always describes a SINGLE research contribution.
Return ONLY JSON:
{"insights": [
  {"claim": "<one-sentence factual claim, no speculation>",
   "quote": "<VERBATIM contiguous quote from the document, 10-60 words, that fully supports the claim>",
   "event_type": one of <types>,
   "attributed_lab": "<lab name or null>",
   "attributed_person": "<person name or null>"}
]}
PICK THE QUOTE FIRST, THEN WRITE THE CLAIM FROM IT. Every load-bearing fact in
the claim — each number, date, price, model name, version, actor — must appear
INSIDE the quote you chose. If the decisive number sits in a different sentence
than the story, quote the sentence with the number. A modest claim its quote
fully carries beats a rich claim the quote only half-supports: facts you
remember from elsewhere in the document do not belong in the claim.
Return one object per DISTINCT event, most decision-relevant first, at most <N>.
Do NOT split one event into several claims, do NOT invent events the text does
not support, and do NOT pad the list to the maximum — return FEWER when the
document supports fewer. Each quote must be copied character-for-character.
```

**"PICK THE QUOTE FIRST" is a measured revision, not a flourish.** The
entailment audit judged 46% of the corpus's claim–quote pairs only
*partially* entailed, and the failure reasons had one shape: claims carrying a
model name, version or number that lives in a *different* sentence than the
quoted one — true facts, wrong provenance. The block inverts the model's
writing order (quote → claim) and names the failure explicitly ("facts you
remember from elsewhere in the document do not belong in the claim"). Measured
A/B on the 30 hardest documents (sampled with seed 7 from docs that already had
≥1 partial verdict, same entailment judge): partial rate **65.6% → 47.9%**,
entailed 34.4% → 52.1%, zero not-entailed, and quote verification still held
(96 of 102 fresh extractions verified verbatim). The revision applies to new
extractions; already-persisted insights keep their original audited verdicts.

**The quote is the load-bearing field.** The whole evidence-first invariant
rests on it. The `claim` is the model's paraphrase and is allowed to be smooth;
the `quote` is the source's own contiguous words and is checked byte-for-byte
against the stored document before anything persists. "Character-for-character"
and "contiguous" are not politeness — a paraphrased or stitched-together quote
fails verification and the event is dropped. This is what lets every downstream
number trace to a byte a lab actually published.

**"Do NOT pad the list to the maximum."** Left to itself a model will fill a
list of N because the schema offers N slots, inventing marginal events to do it.
The cap scales with document length, but the instruction to return *fewer*, plus
"an arXiv abstract almost always describes a SINGLE contribution", pushes back on
the model's tendency to over-generate. Precision here matters more than recall:
a false event is a permanent lie in the corpus, a missed one is recoverable on
the next fetch.

**"most decision-relevant first."** When the cap does bind, we want the events
we keep to be the ones that matter, not the last three paragraphs of a changelog.

**Attribution is nullable.** `attributed_lab`/`attributed_person` are "or null",
because the honest answer is often that the text does not name a person. A model
told it must name someone will confabulate one; allowing null means a blank is a
real answer, and person attribution genuinely resolves on only a small fraction
of events — a fact reported rather than papered over.

---

## 3. The channel classifier

`fli/knowledge/channels.py` — `CHANNEL_SYSTEM`. Decides how a lab event reaches
the fund's portfolio, or that it does not.

```
You decide how a frontier-AI-lab event reaches a technology
fund's portfolio, or that it does not.

The fund cannot trade the labs: they are private or far above its market-cap
band. An event matters ONLY through one of these transmission channels:

<channels, injected from config/policy.yml>

DECIDE ON MECHANISM, NOT TOPIC. The corpus is entirely about AI, so AI
vocabulary is uninformative. "GPU", "cluster", "energy", "license" appearing in
the text means nothing on its own.

Ask: does this change a QUANTITY someone outside the lab must respond to?
 - "900 megawatts contracted in Abilene"        -> energy_datacenter (a number moved)
 - "our model tops the reasoning benchmark"     -> none (impressive, no quantity)
 - "300M people ask ChatGPT health questions"   -> competitive_displacement (a market moved)
 - "we clustered 3,000 values in this analysis" -> none (topical word, no mechanism)
 - "Dr X is joining as research lead"           -> talent_movement
 - "board appointed a new trustee"              -> none (governance, not research talent)

Default to "none". Most posts are marketing, product UX, or research with no
portfolio consequence, and saying so is the correct answer.

Reply with ONLY:
{"channel": "<one of the channels above, or none>",
 "quantity": "<the number/commitment that moved, or null>",
 "confidence": "high" | "medium" | "low",
 "reason": "<one line>"}
```

**Why an LLM and not the keyword lexicon.** A keyword list is genuinely good at
*exposure* (does this text mention memory, datacenters, licensing?) and genuinely
bad at *mechanism*. Measured on the 100-label human-audited reference set, the
lexicon scores F1 0.267 against the classifier's 0.444, and its errors are the
dangerous kind: it returned
a phone codec that "increased power usage by 14%" as a datacenter signal because
"power" is an ambient word in an AI corpus. So the lexicon is kept for exposure
and the classifier decides mechanism.

**"DECIDE ON MECHANISM, NOT TOPIC."** This is the entire difficulty in one line.
Because the corpus is all about AI, the vocabulary that would trip a keyword
matcher — GPU, cluster, energy, license — is uninformative. The paired examples
are chosen to teach exactly that distinction: "we clustered 3,000 values" vs "900
megawatts contracted" both contain channel-adjacent words, and only one moves a
quantity someone outside the lab must respond to. Asking for the `quantity` that
moved forces the model to point at the number, which is a check on its own
answer — no number usually means the channel should have been "none".

**"Default to none."** The correct answer for most posts is that they have no
portfolio consequence, and a classifier that strains to find one on every
document is worse than useless. Making "none" the explicit default aligns the
model with the base rate and is why a "none" verdict is treated downstream as an
*answer*, not a gap.

---

## 4. The pairwise judge

`fli/intelligence/judge.py` — assembled by `build_rubric_system` from a rubric
file plus a fixed reply contract. The audience-specific content is entirely in
`config/rubrics/*.yml`; only the contract below is in code.

The composed prompt has this shape:

```
You rank frontier-AI-lab events for: <audience>.

THE ONLY QUESTION: <the rubric's question>

CHANNELS (pick the one that decided it, or "none"):     [investment rubric only]
 - <channels from policy.yml>

ORDERING RULES — apply in order, stop at the first that SEPARATES the pair.
A rule only separates a pair when it applies to one event and not the other.
 1. <rule>
 2. <rule>
 ...

BANNED REASONS:
 - <banned reason>
 ...

<binary tail, below>

Reply with ONLY this JSON:
{"winner": "a" | "b", "thesis_channel": "<channel or none>",
 "rule": <1-N>, "confidence": "high" | "medium" | "low",
 "reason": "<one line citing the rule>"}
```

The fixed tail that every rubric shares:

```
YOU MUST CHOOSE "a" OR "b". "tie" is not an available answer.

Instead, report how forced the choice was:
  "high"   — a rule clearly separated them; you would answer the same way again.
  "medium" — a rule separated them, but weakly.
  "low"    — no rule separated them and you effectively guessed. SAY SO. A
             truthful "low" is more useful than a confident coin flip, because
             low-confidence pairs are excluded from training.

Do not inflate confidence. If you would not give the same answer when shown the
two events in the opposite order, that is "low".
```

**Why pairwise, and why a rule number.** Scoring an event's importance in
isolation is not a task a person could do consistently, let alone a model.
Comparing two events is. Requiring the model to name the *rule* that decided the
comparison makes every label auditable — a verdict that cannot cite its ordering
rule is rejected and retried once — and it turns the rubric into something the
judge must actually apply rather than a preamble it can ignore.

**Why "tie" was removed.** An earlier version allowed ties, and of 615
investment verdicts, 274 came back "low"/tie and carried no training signal. The
fix was to force a binary choice but let the model tell the truth about how
forced it was: a truthful "low" is excluded from training, so a coin flip is
labelled as a coin flip instead of polluting the training set as a real
preference. The final clause — "if you would not give the same answer with the
events swapped, that is low" — ties confidence directly to the position-bias test
the harness runs.

**Why the lab name is absent.** The rubric bans lab identity as a reason,
per-lab precision is the fairness check, and presentation order is randomised and
un-swapped on store. All three would be meaningless if the judge could see who
published the event, so it sees only claim, type, date and the verified quote.

**Why the rubric is a file, not a string in code.** Everything a fund would
change lives in `config/rubrics/investment.yml`; everything an engineering team
would change lives in `config/rubrics/technical.yml`, which contains no fund
content at all (a test asserts this). Swapping the file re-points the whole
ranking without a line of code changing, and the model+rubric pair is baked into
the labeler id so two rubrics never pool their labels.

---

## 5. The per-audience reader (personas)

`fli/delivery/personas.py`. One shared preamble, then an audience-specific
block. This is the step that commits to a reading — what an event *means* and
what to do about it — so its constraints are the tightest in the system.

The shared preamble:

```
You will be given ONE event extracted from a frontier AI lab's own
publication: a claim, the verbatim quote it was verified against, and the date.

HARD RULES
 - Use only what is in the claim and the quote. If the quote does not support a
   consequence, say so rather than inferring one. A confident reading of
   something the evidence does not say is the worst outcome here.
 - You are not told which lab published this. Do not guess, and do not let a
   guess shape the reading.
 - `reasoning` is shown to the reader verbatim. Write the actual chain, not a
   restatement of the claim.
```

The investment block asks "what does this mean for the fund's positions?", gives
the model the holdings and the mechanism already established for the event, and
asks it for the one thing the deterministic layer deliberately withheld — the
*sign*:

```
YOUR JOB IS THE SIGN. Read whether the quantity in this event moved UP or DOWN
for the holding, and say which:
  "threat"   the event erodes the holding's market, margin or moat
  "tailwind" the event increases demand for what the holding sells
  "unclear"  the quote does not establish a direction — a common, correct answer
```

The engineering block asks "what should we adopt or investigate?" and rules
commercial consequence out of scope entirely (`adopt` / `investigate` /
`monitor`).

**Why the reading is separated from the ranking.** A ranked claim with a
citation is still a summary — the test is whether a reader knows what it means
and what to do. That requires committing to a reading, which an LLM does
and the deterministic layer does not, because the sign of a demand channel is a
judgement about a sentence, not a property of the channel.

**"A confident reading of something the evidence does not say is the worst
outcome here."** This is the single most important line in the file. The
expensive error for this system is not missing an event — it is telling a
portfolio manager a holding faces a threat on evidence that does not support it.
The prompt makes `unclear` (and its engineering equivalent, `monitor`) an
explicitly correct and common answer, and the parser enforces it: a reading whose
direction is not in the audience's vocabulary is rejected, because it means the
model answered the other audience's question.

**Why `reasoning` is mandatory and shown verbatim.** It is `NOT NULL` in the
schema. The reader is shown the working, not just the verdict, so they can check
it against the quote — which is the whole point of an evidence-first system, and
the reason a reading with an empty `reasoning` is discarded rather than stored.

**The failure diagnostics.** The parser distinguishes *why* a reply was
unusable — truncated at the token ceiling, missing its reasoning, or answering
the wrong audience — because a bare "unusable" hides real problems. Two readings
once failed silently to a token-limit truncation; the diagnostic now names it, so
the fix (raise the budget) is obvious from the log rather than found by
inspecting the payload.

---

## The pattern across all five

- **Reply contract stated exactly, parser enforces it.** Every prompt ends with
  a literal JSON shape, and every reply passes through a parser that rejects
  anything malformed, incomplete, or off-question. The strictness is not
  fussiness — it is what keeps a plausible-but-wrong reply out of the database.
- **The honest null is always available.** `substantive: false`, `channel:
  none`, `attributed_person: null`, `direction: unclear`, `confidence: low`.
  Every prompt makes "the evidence does not support a stronger answer" a
  first-class, correct response, because the alternative is a model that
  confabulates to fill a field.
- **Identity is withheld where it would bias.** The judge and the reader are
  both denied the lab name, so neither can be primed by prestige.
- **The quote is the anchor.** Extraction pins every event to a verbatim quote,
  and every later prompt is constrained to reason from that quote and nothing
  else.
