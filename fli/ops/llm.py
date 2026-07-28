"""LLM client. Every call is cost-logged to llm_calls from call #1."""
from __future__ import annotations

import os
import sqlite3

from fli.core.paths import ROOT


def load_dotenv() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def have_api_key(model: str | None = None) -> bool:
    load_dotenv()
    if model is None:
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    return bool(os.environ.get(KEY_ENV[provider_for(model)]))


# WHY A SECOND PROVIDER EXISTS AT ALL, since one would be simpler:
#
# The judge trains the ranker, and until now the judge, the extractor and every
# prompt variant were the same model family. Dawid-Skene estimates labeler
# reliability from DISAGREEMENT, and it assumes labelers are conditionally
# independent — an assumption three Claude prompts violate outright. Measured:
# r2/r3/r4 agreed 92-100%, so DS rated all three ~0.99, which is an artifact of
# asking one model three times rather than a finding.
#
# A different model family is a genuinely independent labeler. That turns the
# reliability estimate into something identifiable, and turns "the LLM agreed
# with itself" into an inter-family agreement number.
#
# It is also the fallback story: one provider outage currently stops the
# pipeline.
PROVIDERS = {"anthropic": ("claude",),
             "openai": ("gpt-", "o1", "o3", "o4", "chatgpt")}
KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


def provider_for(model: str) -> str:
    for prov, prefixes in PROVIDERS.items():
        if model.startswith(prefixes):
            return prov
    raise ValueError(
        f"unknown provider for model {model!r}. Add its prefix to "
        f"fli/ops/llm.PROVIDERS — guessing a provider would send a key to the "
        f"wrong endpoint.")

MODEL_FOR_TASK = {
    "classify": "claude-haiku-4-5-20251001",  # high volume, cheap, structured output
    "extract": "claude-sonnet-5",             # needs faithful quoting + schema adherence
    "persona": "claude-sonnet-5",             # reasoning quality visible to end reader
    "label": "claude-sonnet-5",               # rubric application; the reference set
    "judge": "claude-sonnet-5",               # pairwise preference; reasoning is audited
    # the channel classifier that replaces keyword matching (F1 0.195).
    # Haiku because it runs over every event and the task is a 5-way choice with
    # the rubric supplied — cheap model, bounded output, cost-quality trade-off
    # measured rather than assumed (see docs/report-notes.md).
    "channel": "claude-haiku-4-5-20251001",
}

# USD per 1M tokens (input, output); verify against live pricing before shipping.
#
# There is deliberately NO default entry and no fallback rate. Token cost is a
# graded deliverable, and a guessed price is worse than a missing one: it
# produces a confident number nobody checked. An unknown model raises instead.
PRICES = {
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-sonnet-5": (3.00, 15.00),
}
PRICES_CHECKED_AT = None


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    if model not in PRICES:
        raise KeyError(
            f"no price recorded for {model!r}. Look up its per-1M input/output "
            f"rate and add it to fli/ops/llm.PRICES as "
            f"{model!r}: (input, output). The tokenomics figures are reported "
            f"to the reader, so this refuses to invent a rate.")
    pin, pout = PRICES[model]
    return (input_tokens * pin + output_tokens * pout) / 1_000_000


# Typical judge call, measured over the 615 existing judgements: two event
# blocks with quotes plus the rules block in, one short JSON verdict out.
TYPICAL_JUDGE_TOKENS = (1500, 120)


def preflight(model: str, n_calls: int = 0) -> float:
    """Check key AND price BEFORE the first paid call, and project the spend.

    THE BUG THIS PREVENTS: `cost_usd` raises on an unpriced model, but it was
    only reached AFTER the API had answered — so adding a new judge model
    meant paying for a call whose response was then thrown away by the
    exception. A guard that fires after the money is gone is not a guard.

    Same discipline as the X ingest budget check, for the same reason.
    """
    load_dotenv()
    prov = provider_for(model)
    if not os.environ.get(KEY_ENV[prov]):
        raise SystemExit(
            f"{KEY_ENV[prov]} not set. Add it to .env:\n"
            f"    {KEY_ENV[prov]}=...\n"
            f"Needed because the judge model {model!r} is a {prov} model.")
    if model not in PRICES:
        raise SystemExit(
            f"no price recorded for {model!r}.\n"
            f"Add one line to fli/ops/llm.PRICES, using the rates from the "
            f"provider's pricing page:\n"
            f"    {model!r}: (input_per_1M, output_per_1M),\n"
            f"Refusing to run rather than invent a rate: token cost is a "
            f"reported figure, and a guessed price is worse than a missing one.")
    tin, tout = TYPICAL_JUDGE_TOKENS
    est = n_calls * cost_usd(model, tin, tout)
    if n_calls:
        print(f"  projected: {n_calls} calls x ~{tin}+{tout} tokens "
              f"= ~${est:.2f} on {model}")
    return est


class LLM:
    """One client object, one or two providers behind it.

    Clients are built LAZILY, per provider, on first use — so a run that only
    touches Claude never requires an OpenAI key to be present, and vice versa.
    That matters because the demo has to stay runnable with a single key.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._clients: dict[str, object] = {}

    def _client(self, provider: str):
        if provider not in self._clients:
            key = os.environ.get(KEY_ENV[provider])
            if not key:
                raise SystemExit(
                    f"{KEY_ENV[provider]} not set (put it in .env). Needed "
                    f"because a task is routed to a {provider} model.")
            if provider == "anthropic":
                import anthropic          # lazy: keyless envs never import it
                self._clients[provider] = anthropic.Anthropic(api_key=key)
            else:
                import openai
                self._clients[provider] = openai.OpenAI(api_key=key)
        return self._clients[provider]

    # Models that answered "`temperature` is deprecated for this model".
    # Class-level so the capability is learned ONCE per process rather than
    # rediscovered on every call. The previous version only suppressed the
    # warning, not the retry, so each sonnet-5 call cost two round trips: a
    # 400 and then the real one.
    _no_temperature: set[str] = set()

    def call(self, task: str, system: str, user: str, max_tokens: int = 1024,
             temperature: float = 0.0, model: str | None = None) -> str:
        """`model` overrides the task default — that is how a second judge from
        another provider is run over the identical pairs."""
        from fli import storage
        from fli.ops import tracing
        model = model or MODEL_FOR_TASK[task]
        if provider_for(model) == "openai":
            return self._call_openai(task, model, system, user,
                                     max_tokens, temperature)
        with tracing.llm_span(task) as span:
            tracing.annotate(span, tracing.input_attrs(model, system, user))
            # temperature=0 wherever the model still accepts it: every task
            # here is structured extraction or classification, where the same
            # input should give the same answer. It was previously unset, so
            # the API default applied and identical calls could differ.
            #
            # claude-sonnet-5 runs adaptive thinking and rejects an explicit
            # temperature outright. That is a capability of the model, not a
            # failure, so it is DETECTED ONCE and remembered — the parameter is
            # simply not sent again for that model.
            #
            # What this costs us is worth stating plainly: on such a model
            # reproducibility cannot be asserted from a parameter, so it has to
            # be MEASURED. `judge --consistency N` judges N pairs both ways and
            # reports the flip rate, which is the honest version of the claim
            # that temperature=0 was standing in for.
            kwargs = dict(model=model, max_tokens=max_tokens, system=system,
                          messages=[{"role": "user", "content": user}])
            create = self._client("anthropic").messages.create
            if model in LLM._no_temperature:
                resp = create(**kwargs)
            else:
                try:
                    resp = create(temperature=temperature, **kwargs)
                except Exception as e:
                    if "temperature" not in str(e).lower():
                        raise
                    LLM._no_temperature.add(model)
                    print(f"  note: {model} does not accept an explicit "
                          f"temperature; sending none for the rest of this run. "
                          f"Determinism for this model is measured, not "
                          f"declared — see `judge --consistency N`.")
                    resp = create(**kwargs)
            usage = resp.usage
            # claude-sonnet-5 runs adaptive thinking by default, so a ThinkingBlock
            # may precede the text block — take text blocks only.
            text = "".join(b.text for b in resp.content if b.type == "text")
            tracing.annotate(span, tracing.output_attrs(
                text, usage.input_tokens, usage.output_tokens))
        storage.log_llm_call(self.conn, task, model, usage.input_tokens,
                             usage.output_tokens,
                             cost_usd(model, usage.input_tokens, usage.output_tokens))
        return text

    # OpenAI's chat API differs in three ways that matter, each handled by
    # learning the model's capability once rather than by hardcoding a list of
    # model names that would go stale:
    #   - the system prompt is a message, not a top-level argument
    #   - reasoning models want `max_completion_tokens`, not `max_tokens`
    #   - reasoning models reject `temperature`, exactly like sonnet-5
    _no_max_tokens: set[str] = set()

    def _call_openai(self, task: str, model: str, system: str, user: str,
                     max_tokens: int, temperature: float) -> str:
        from fli import storage
        from fli.ops import tracing
        client = self._client("openai")
        with tracing.llm_span(task) as span:
            tracing.annotate(span, tracing.input_attrs(model, system, user))
            base = dict(model=model,
                        messages=[{"role": "system", "content": system},
                                  {"role": "user", "content": user}])

            def build() -> dict:
                kw = dict(base)
                kw["max_completion_tokens" if model in LLM._no_max_tokens
                   else "max_tokens"] = max_tokens
                if model not in LLM._no_temperature:
                    kw["temperature"] = temperature
                return kw

            for _ in range(3):          # at most: max_tokens fix, temp fix, send
                try:
                    resp = client.chat.completions.create(**build())
                    break
                except Exception as e:
                    msg = str(e).lower()
                    if "max_tokens" in msg and model not in LLM._no_max_tokens:
                        LLM._no_max_tokens.add(model)
                    elif "temperature" in msg and model not in LLM._no_temperature:
                        LLM._no_temperature.add(model)
                        print(f"  note: {model} does not accept an explicit "
                              f"temperature; sending none for the rest of this "
                              f"run.")
                    else:
                        raise
            else:
                raise RuntimeError(f"{model}: could not find an accepted "
                                   f"parameter combination")

            text = resp.choices[0].message.content or ""
            u = resp.usage
            in_tok, out_tok = u.prompt_tokens, u.completion_tokens
            tracing.annotate(span, tracing.output_attrs(text, in_tok, out_tok))
        storage.log_llm_call(self.conn, task, model, in_tok, out_tok,
                             cost_usd(model, in_tok, out_tok))
        return text
