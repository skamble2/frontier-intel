# Prompts

Five prompts do all the LLM work. Each is quoted from the code; full text is in
the file named above it.

The principle running through all five: **the prompt is a contract and the
parser enforces it.** A reply that omits its working, or answers a different
question than the one asked, is rejected rather than stored. Every rationale
below is about what that strictness protects against.

---

## 1. Classifier gate

`fli/knowledge/extraction.py` — `CLASSIFY_SYSTEM`, on Haiku.

```
Return ONLY JSON: {"event_type": one of <types>,
"substantive": true|false, "reason": "<one line>"}.
substantive=false means: marketing fluff, event promotion, job posting,
or no concrete technical/personnel/product information.
```

Extraction is the expensive call and roughly a quarter of the corpus will yield
nothing. This gate runs first on Haiku over the *same* 6k prefix the extractor
will see, so the prefix is not paid for twice, and kills ~28% of documents
before Sonnet.

`substantive` is defined by **negatives** on purpose. The abstract word is
vague; the failure modes are concrete, and naming the three things this corpus
is actually full of gives a sharper boundary than a definition would.

---

## 2. Extractor

`fli/knowledge/extraction.py` — `_EXTRACT_TEMPLATE`, on Sonnet.

```
{"insights": [{"claim": "<one-sentence factual claim, no speculation>",
  "quote": "<VERBATIM contiguous quote, 10-60 words, that fully supports the claim>",
  "event_type": ..., "attributed_lab": "<or null>", "attributed_person": "<or null>"}]}

PICK THE QUOTE FIRST, THEN WRITE THE CLAIM FROM IT. Every load-bearing fact in
the claim — each number, date, price, model name, version, actor — must appear
INSIDE the quote you chose. A modest claim its quote fully carries beats a rich
claim the quote only half-supports: facts you remember from elsewhere in the
document do not belong in the claim.
Return one object per DISTINCT event, most decision-relevant first, at most <N>.
Do NOT invent events the text does not support, and do NOT pad the list to the
maximum — return FEWER when the document supports fewer.
```

**"PICK THE QUOTE FIRST" is a measured revision, not a flourish.** An entailment
audit found 46% of claim–quote pairs only *partially* entailed, and the failures
had one shape: a model name, version or number that lived in a *different*
sentence than the quote. True facts, wrong provenance. The block inverts the
writing order and names the failure explicitly. A/B on the 30 hardest documents
(seed 7, same judge): **partial rate 65.6% → 47.9%**, zero not-entailed, quote
verification still holding at 96 of 102.

**The quote is the load-bearing field.** The claim is a paraphrase and may be
smooth; the quote is checked byte-for-byte against the stored document before
anything persists. "Contiguous" and "character-for-character" are not
politeness — a stitched quote fails verification and the event is dropped.

**"Do NOT pad the list."** A model will fill N slots because the schema offers
N. Precision beats recall here: a false event is a permanent lie in the corpus,
a missed one is recoverable on the next fetch.

**Attribution is nullable** because the honest answer is usually that the text
names no one. A model told it must name someone will confabulate one.

---

## 3. Channel classifier

`fli/knowledge/channels.py` — `CHANNEL_SYSTEM`. Decides how an event reaches the
portfolio, or that it does not.

```
DECIDE ON MECHANISM, NOT TOPIC. The corpus is entirely about AI, so AI
vocabulary is uninformative. "GPU", "cluster", "energy", "license" appearing in
the text means nothing on its own.

Ask: does this change a QUANTITY someone outside the lab must respond to?
 - "900 megawatts contracted in Abilene"        -> energy_datacenter (a number moved)
 - "our model tops the reasoning benchmark"     -> none (impressive, no quantity)
 - "we clustered 3,000 values in this analysis" -> none (topical word, no mechanism)
 - "board appointed a new trustee"              -> none (governance, not research talent)

Default to "none".
```

Channels are injected from `config/policy.yml`, so the fund's transmission model
is configuration rather than prompt text.

**Why a model and not the keyword lexicon it replaced.** Keywords are good at
*exposure* and bad at *mechanism*. On the 100-label human-audited reference the
lexicon scores **F1 0.267 against the classifier's 0.444**, and its errors are
the dangerous kind — it once returned a phone codec that "increased power usage
by 14%" as a datacenter signal, because "power" is ambient in an AI corpus.

The paired examples teach exactly that line: "we clustered 3,000 values" and
"900 megawatts contracted" both contain channel-adjacent words, and only one
moves a quantity. Asking for the `quantity` that moved makes the model check its
own answer — no number usually means the channel should have been `none`.

**"Default to none"** aligns the model with the base rate, which is why a `none`
verdict is treated downstream as an answer rather than a gap.

---

## 4. Pairwise judge

`fli/intelligence/judge.py` — composed by `build_rubric_system` from a rubric
file plus a fixed reply contract. All audience-specific content lives in
`config/rubrics/*.yml`; only the contract is in code.

```
ORDERING RULES — apply in order, stop at the first that SEPARATES the pair.
A rule only separates a pair when it applies to one event and not the other.

Reply: {"winner": "a"|"b", "thesis_channel": ..., "rule": <1-N>,
        "confidence": "high"|"medium"|"low", "reason": "<one line citing the rule>"}

YOU MUST CHOOSE "a" OR "b". "tie" is not an available answer.
Instead, report how forced the choice was:
  "low" — no rule separated them and you effectively guessed. SAY SO. A
          truthful "low" is more useful than a confident coin flip, because
          low-confidence pairs are excluded from training.
Do not inflate confidence. If you would not give the same answer when shown the
two events in the opposite order, that is "low".
```

**Pairwise, with a rule number.** Scoring importance in isolation is not a task
a person could do consistently. Comparing two events is. Requiring the model to
cite the rule that decided makes every label auditable — a verdict that cannot
cite one is rejected and retried once — and forces the rubric to be applied
rather than skimmed.

**Why "tie" was removed.** An earlier version allowed it: of 615 investment
verdicts, 274 came back tie or low and carried no training signal. Forcing a
binary while letting the model tell the truth about how forced it was means a
coin flip is *labelled* as one and excluded, instead of polluting training as a
real preference. The closing clause ties confidence to the position-bias test the
harness already runs.

**The lab name is withheld.** The rubric bans lab identity as a reason, per-lab
precision is the fairness check, and presentation order is randomised and
un-swapped on store — all three would be meaningless if the judge could see the
publisher.

**The rubric is a file, not a string.** Swapping
`config/rubrics/investment.yml` re-points the whole ranking with no code change,
and the model+rubric pair is baked into the labeler id so two rubrics never pool
their labels.

---

## 5. Per-audience reader

`fli/delivery/personas.py`. This is the step that commits to a reading — what an
event *means* and what to do — so its constraints are the tightest.

```
HARD RULES
 - Use only what is in the claim and the quote. If the quote does not support a
   consequence, say so rather than inferring one. A confident reading of
   something the evidence does not say is the worst outcome here.
 - You are not told which lab published this.
 - `reasoning` is shown to the reader verbatim. Write the actual chain, not a
   restatement of the claim.

YOUR JOB IS THE SIGN:                                    [investment block]
  "threat"   the event erodes the holding's market, margin or moat
  "tailwind" the event increases demand for what the holding sells
  "unclear"  the quote does not establish a direction — a common, correct answer
```

The engineering block asks "adopt / investigate / monitor" and rules commercial
consequence out of scope entirely.

**The reading is separated from the ranking** because the sign of a demand
channel is a judgement about a sentence, not a property of the channel — so the
deterministic layer withholds it and this step supplies it.

**"A confident reading of something the evidence does not say is the worst
outcome here"** is the most important line in the file. The expensive error is
not missing an event; it is telling a PM a holding faces a threat on evidence
that does not support it. `unclear` is made explicitly correct and common, and
the parser enforces the boundary: a direction outside the audience's vocabulary
is rejected, because it means the model answered the other audience's question.

**`reasoning` is `NOT NULL`** and shown verbatim, so a reader can check the
working against the quote. A reading with empty reasoning is discarded.

---

## The pattern across all five

- **The reply contract is stated exactly and the parser enforces it.** Strictness
  is what keeps a plausible-but-wrong reply out of the database.
- **The honest null is always available** — `substantive: false`, `channel:
  none`, `attributed_person: null`, `direction: unclear`, `confidence: low`.
  Every prompt makes "the evidence does not support a stronger answer" a
  first-class response, because the alternative is a model that confabulates to
  fill a field.
- **Identity is withheld where it would bias.** Neither the judge nor the reader
  is told which lab published the event.
- **The quote is the anchor.** Extraction pins every event to a verbatim quote,
  and every later prompt reasons from that quote and nothing else.
