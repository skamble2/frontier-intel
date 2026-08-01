"""LLM client. Every call is cost-logged to llm_calls from call #1."""
from __future__ import annotations

import os
import sqlite3
import time
from typing import Any

from fli.core.paths import ROOT


def load_dotenv() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


# A second provider exists for one reason: Dawid-Skene estimates labeler
# reliability from disagreement and assumes labelers are conditionally
# independent, which prompt variants of a single model are not (measured at
# 92-100% agreement, which rates all of them ~0.99). A different model family
# is a genuinely independent labeler. It doubles as the fallback path when one
# provider is down.
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


def have_api_key(model: str | None = None) -> bool:
    """True if the key for `model`'s provider is set. Defaults to Anthropic."""
    load_dotenv()
    if model is None:
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    return bool(os.environ.get(KEY_ENV[provider_for(model)]))


# One model per task, picked on measured cost-quality. Haiku takes the
# high-volume, bounded-output jobs; Sonnet takes the ones needing faithful
# quoting or audited reasoning. The Haiku work costs $0.65 against $1.95 on
# Sonnet, and the classify gate stops ~93 documents before extraction.
MODEL_FOR_TASK = {
    "classify": "claude-haiku-4-5-20251001",  # high volume, structured output
    "extract": "claude-sonnet-5",             # faithful quoting + schema adherence
    "repair": "claude-sonnet-5",              # claim tightening: same faithfulness bar as extract
    "persona": "claude-sonnet-5",             # UNUSED: no caller routes to it
    "judge": "claude-sonnet-5",               # pairwise preference, audited
    "channel": "claude-haiku-4-5-20251001",   # 5-way choice over every event
    "verify": "claude-haiku-4-5-20251001",    # claim<->quote entailment, 3-way
}

# USD per 1M tokens (input, output). Verify against live pricing before
# shipping. There is deliberately no default entry and no fallback rate: a
# guessed price produces a confident number nobody checked, so an unknown model
# raises instead.
PRICES = {
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-sonnet-5": (3.00, 15.00),
    # Reasoning tokens are billed as OUTPUT and are already inside
    # `usage.completion_tokens`, so this rate is correct — but the visible JSON
    # verdict is ~120 tokens while billed output can be many times that.
    # `reasoning_tokens` is logged separately so the split stays reportable.
    "gpt-5.2": (1.75, 14.00),
}
PRICES_CHECKED_AT = "2026-07-28"     # provider pricing pages, by hand

# Prompt-cache pricing multipliers on the INPUT rate and the batch discount,
# from the same pricing pages as PRICES. Cache write costs a 25% premium once;
# every read of that prefix costs 10%. The Batch API halves everything in
# exchange for asynchronous delivery (up to 24h, usually minutes).
#
# Caveat measured 2026-08-01: every system prompt in this repo is 200-620
# tokens, BELOW Anthropic's cache minimum (1024 Sonnet / 2048 Haiku), so a
# cache_control mark on the system block alone caches nothing today. It is
# still always sent — below-minimum marks are free and ignored, and the mark
# starts working the day a prompt grows past the line. The judge additionally
# marks its first event block, which pushes the prefix past 1024 tokens when
# consecutive pairs share their first event — see judge.build_user_blocks.
CACHE_WRITE_MULT = 1.25
CACHE_READ_MULT = 0.10
BATCH_DISCOUNT = 0.5

# Module alias so tests can silence batch polling without patching time.
_sleep = time.sleep

def reasoning_effort() -> str | None:
    """reasoning.effort for OpenAI reasoning models, or None for their default.

    Read at call time, not at import, so FLI_REASONING_EFFORT works from .env
    as well as inline.

    Left at the provider default on purpose: the judge follows five explicit
    ordering rules over two short texts, and raising effort multiplies billed
    output for an answer that is one of two letters. Set the variable to test
    that rather than assume it — the difference lands in llm_calls either way.
    """
    load_dotenv()
    return os.environ.get("FLI_REASONING_EFFORT")


def cost_usd(model: str, input_tokens: int, output_tokens: int,
             cache_write_tokens: int = 0, cache_read_tokens: int = 0,
             batch: bool = False) -> float:
    """Cache tokens are billed at multiples of the INPUT rate and are NOT
    inside input_tokens (the API reports them separately); batch halves the
    whole call. Callers that pass neither get the exact old behaviour."""
    if model not in PRICES:
        raise KeyError(
            f"no price recorded for {model!r}. Add its per-1M input/output "
            f"rate to fli/ops/llm.PRICES as {model!r}: (input, output). "
            f"Refusing to invent a rate.")
    pin, pout = PRICES[model]
    usd = (input_tokens * pin + output_tokens * pout
           + (cache_write_tokens or 0) * pin * CACHE_WRITE_MULT
           + (cache_read_tokens or 0) * pin * CACHE_READ_MULT) / 1_000_000
    return usd * (BATCH_DISCOUNT if batch else 1.0)


# Typical judge call, measured over the 615 existing judgements: two event
# blocks with quotes plus the rules block in, one short JSON verdict out.
TYPICAL_JUDGE_TOKENS = (1500, 120)


def _flatten(user: str | list[dict]) -> str:
    """Content blocks back to the plain string they are guaranteed to equal."""
    if isinstance(user, str):
        return user
    return "".join(b["text"] for b in user)


def _cached_system(system: str) -> list[dict]:
    """System prompt as a block with a cache mark. Below the provider's cache
    minimum the mark is free and ignored (see CACHE_WRITE_MULT comment); above
    it, every repeat call in a 5-minute window reads the prefix at 10%."""
    return [{"type": "text", "text": system,
             "cache_control": {"type": "ephemeral"}}]


def _cache_usage(usage) -> tuple[int, int]:
    """(cache_write, cache_read) tokens, 0 when absent — older SDK responses
    and OpenAI usage objects simply lack the fields."""
    return (getattr(usage, "cache_creation_input_tokens", 0) or 0,
            getattr(usage, "cache_read_input_tokens", 0) or 0)


def preflight(model: str, n_calls: int = 0) -> float:
    """Check SDK, key and price before the first paid call, and project spend.

    `cost_usd` also raises on an unpriced model, but only after the API has
    answered — so the call is paid for and then discarded by the exception.
    Same discipline as the X ingest budget check.
    """
    load_dotenv()
    prov = provider_for(model)
    # The SDK is imported lazily in _client(), so a missing package would
    # otherwise surface on the first pair of a long run.
    import importlib.util
    pkg = {"anthropic": "anthropic", "openai": "openai"}[prov]
    if importlib.util.find_spec(pkg) is None:
        raise SystemExit(
            f"the {pkg!r} package is not installed, and judge model {model!r} "
            f"needs it.\n    pip install '{pkg}>=1.40'\n"
            f"(it is in requirements.txt as an optional second provider)")
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
            f"Refusing to run rather than invent a rate.")
    tin, tout = TYPICAL_JUDGE_TOKENS
    est = n_calls * cost_usd(model, tin, tout)
    if n_calls:
        print(f"  projected: {n_calls} calls x ~{tin}+{tout} tokens "
              f"= ~${est:.2f} on {model}")
    return est


class LLM:
    """One client object, one or two providers behind it.

    Clients are built lazily per provider on first use, so a run that only
    touches Claude never needs an OpenAI key present, and vice versa.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        # `Any` because the two SDK client types share no base class.
        self._clients: dict[str, Any] = {}

    def _client(self, provider: str) -> Any:
        if provider not in self._clients:
            key = os.environ.get(KEY_ENV[provider])
            if not key:
                raise SystemExit(
                    f"{KEY_ENV[provider]} not set (put it in .env). Needed "
                    f"because a task is routed to a {provider} model.")
            if provider == "anthropic":
                import anthropic          # lazy: keyless envs never import it
                # max_retries is explicit, not left to the SDK default: both
                # SDKs retry 429/5xx/connection errors with their own
                # exponential backoff, and 4 attempts rides out the typical
                # "overloaded_error" burst without hiding a real outage.
                self._clients[provider] = anthropic.Anthropic(
                    api_key=key, max_retries=4)
            else:
                import openai
                self._clients[provider] = openai.OpenAI(
                    api_key=key, max_retries=4)
        return self._clients[provider]

    # Models that reject an explicit `temperature`. Class-level so the
    # capability is learned once per process instead of costing a rejected
    # round trip on every call.
    _no_temperature: set[str] = set()

    def call(self, task: str, system: str, user: str | list[dict],
             max_tokens: int = 1024, temperature: float = 0.0,
             model: str | None = None) -> str:
        """`model` overrides the task default — that is how a second judge from
        another provider is run over the identical pairs.

        `user` is either a plain string or a list of Anthropic content blocks.
        Blocks exist for one reason: a `cache_control` mark inside the user
        message lets consecutive calls share a prefix (the judge's first event
        block). The concatenated block text is byte-identical to the string a
        caller would otherwise send — callers guarantee that, tests check it —
        so caching can only change the bill, never the answer."""
        from fli import storage
        from fli.ops import tracing
        model = model or MODEL_FOR_TASK[task]
        if provider_for(model) == "openai":
            return self._call_openai(task, model, system, _flatten(user),
                                     max_tokens, temperature)
        with tracing.llm_span(task) as span:
            tracing.annotate(span, tracing.input_attrs(model, system,
                                                       _flatten(user)))
            # temperature=0 wherever the model accepts it: every task here is
            # structured extraction or classification, where the same input
            # should give the same answer.
            #
            # Models running adaptive thinking reject an explicit temperature.
            # That is detected once and remembered. On those models
            # reproducibility cannot be asserted from a parameter and has to be
            # measured instead — see `judge --consistency N`.
            kwargs = dict(model=model, max_tokens=max_tokens,
                          system=_cached_system(system),
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
            # A ThinkingBlock may precede the text block — take text only.
            text = "".join(b.text for b in resp.content if b.type == "text")
            tracing.annotate(span, tracing.output_attrs(
                text, usage.input_tokens, usage.output_tokens))
        cw, cr = _cache_usage(usage)
        storage.log_llm_call(self.conn, task, model, usage.input_tokens,
                             usage.output_tokens,
                             cost_usd(model, usage.input_tokens,
                                      usage.output_tokens,
                                      cache_write_tokens=cw,
                                      cache_read_tokens=cr),
                             cache_write_tokens=cw or None,
                             cache_read_tokens=cr or None)
        return text

    def call_batch(self, task: str, system: str,
                   items: list[tuple[str, str | list[dict]]],
                   max_tokens: int = 1024, temperature: float = 0.0,
                   model: str | None = None,
                   poll_s: float = 15.0) -> dict[str, str | None]:
        """Send `items` [(custom_id, user), ...] through the Batch API at 50%
        of the synchronous price. Returns {custom_id: text}, with None for any
        item that errored — callers fall back to a synchronous `call` for
        those, so a batch failure degrades to full price, never to a lost
        verdict.

        Anthropic-only by design: the one OpenAI use (second judge family) is
        deliberately run synchronously so its labeler id keeps meaning "the
        same pairs, judged independently, the same way".

        Blocks until the batch ends. Batches usually finish in minutes; the
        24h ceiling is the provider's, not ours — progress is printed so an
        operator can Ctrl-C and re-run later (every caller's queue query is
        resumable, so nothing is lost but the batch discount on unfinished
        items)."""
        from fli import storage
        model = model or MODEL_FOR_TASK[task]
        if provider_for(model) != "anthropic":
            raise ValueError(f"call_batch supports anthropic models only, "
                             f"got {model!r}")
        client = self._client("anthropic")
        params = dict(model=model, max_tokens=max_tokens,
                      system=_cached_system(system))
        if model not in LLM._no_temperature:
            params["temperature"] = temperature
        reqs = [{"custom_id": cid,
                 "params": {**params,
                            "messages": [{"role": "user", "content": user}]}}
                for cid, user in items]
        batch = client.messages.batches.create(requests=reqs)
        print(f"  batch {batch.id}: {len(reqs)} request(s) submitted "
              f"({model}, 50% batch rate)")
        waited = 0.0
        while batch.processing_status != "ended":
            _sleep(poll_s)
            waited += poll_s
            batch = client.messages.batches.retrieve(batch.id)
            if waited % 120 < poll_s:            # a line every ~2 minutes
                print(f"  batch {batch.id}: {batch.processing_status} "
                      f"after {waited:.0f}s")
        out: dict[str, str | None] = {cid: None for cid, _ in items}
        errored = 0
        for entry in client.messages.batches.results(batch.id):
            if entry.result.type != "succeeded":
                errored += 1
                continue
            msg = entry.result.message
            text = "".join(b.text for b in msg.content if b.type == "text")
            u = msg.usage
            cw, cr = _cache_usage(u)
            storage.log_llm_call(self.conn, task, model, u.input_tokens,
                                 u.output_tokens,
                                 cost_usd(model, u.input_tokens,
                                          u.output_tokens,
                                          cache_write_tokens=cw,
                                          cache_read_tokens=cr, batch=True),
                                 cache_write_tokens=cw or None,
                                 cache_read_tokens=cr or None)
            out[entry.custom_id] = text
        done = sum(1 for v in out.values() if v is not None)
        print(f"  batch {batch.id}: ended — {done} succeeded, {errored} "
              f"errored{' (will retry synchronously)' if errored else ''}")
        return out

    # OpenAI's chat API differs in three ways, each handled by learning the
    # model's capability once rather than hardcoding a model list that goes
    # stale:
    #   - the system prompt is a message, not a top-level argument
    #   - reasoning models want `max_completion_tokens`, not `max_tokens`
    #   - reasoning models reject `temperature`
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

            effort = reasoning_effort()

            def build() -> dict:
                kw = dict(base)
                kw["max_completion_tokens" if model in LLM._no_max_tokens
                   else "max_tokens"] = max_tokens
                if model not in LLM._no_temperature:
                    kw["temperature"] = temperature
                if effort:
                    kw["reasoning_effort"] = effort
                return kw

            resp = None
            for _ in range(3):          # at most: max_tokens fix, temp fix, send
                try:
                    resp = client.chat.completions.create(**build())
                    break
                except Exception as e:
                    msg = str(e).lower()
                    # One message can name both parameters, so handle each
                    # independently rather than with elif.
                    handled = False
                    if "max_tokens" in msg and model not in LLM._no_max_tokens:
                        LLM._no_max_tokens.add(model)
                        handled = True
                    if "temperature" in msg and model not in LLM._no_temperature:
                        LLM._no_temperature.add(model)
                        print(f"  note: {model} does not accept an explicit "
                              f"temperature; sending none for the rest of this "
                              f"run.")
                        handled = True
                    if not handled:
                        raise
            if resp is None:
                raise RuntimeError(f"{model}: could not find an accepted "
                                   f"parameter combination in 3 attempts")

            text = resp.choices[0].message.content or ""
            u = resp.usage
            in_tok, out_tok = u.prompt_tokens, u.completion_tokens
            # Already inside completion_tokens; pulled out for reporting only.
            details = getattr(u, "completion_tokens_details", None)
            reasoning = getattr(details, "reasoning_tokens", None) if details else None
            tracing.annotate(span, tracing.output_attrs(text, in_tok, out_tok))
        storage.log_llm_call(self.conn, task, model, in_tok, out_tok,
                             cost_usd(model, in_tok, out_tok),
                             reasoning_tokens=reasoning)
        return text
