"""LLM client. Every call is cost-logged to llm_calls from call #1."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any, TypeVar

from pydantic import BaseModel

from fli.core.paths import ROOT

T = TypeVar("T", bound=BaseModel)


def load_dotenv() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


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


MODEL_FOR_TASK = {
    "classify": "claude-haiku-4-5-20251001",
    "extract": "claude-sonnet-5",
    "repair": "claude-sonnet-5",
    "persona": "claude-sonnet-5",
    "judge": "claude-sonnet-5",
    "channel": "claude-haiku-4-5-20251001",
    "verify": "claude-haiku-4-5-20251001",
    "faithfulness": "claude-haiku-4-5-20251001",
}

PRICES = {
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-sonnet-5": (3.00, 15.00),
    "gpt-5.2": (1.75, 14.00),
}
PRICES_CHECKED_AT = "2026-07-28"

CACHE_WRITE_MULT = 1.25
CACHE_READ_MULT = 0.10
BATCH_DISCOUNT = 0.5

_sleep = time.sleep

def reasoning_effort() -> str | None:
    """reasoning.effort for OpenAI reasoning models, or None for their default.
    reasoning.effort for OpenAI reasoning models, or None for their default."""
    load_dotenv()
    return os.environ.get("FLI_REASONING_EFFORT")


def cost_usd(model: str, input_tokens: int, output_tokens: int,
             cache_write_tokens: int = 0, cache_read_tokens: int = 0,
             batch: bool = False) -> float:
    """Cache tokens are billed at multiples of the INPUT rate and are NOT inside
    input_tokens (the API reports them separately); batch halves the whole
    call."""
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


TYPICAL_JUDGE_TOKENS = (1500, 120)


def _flatten(user: str | list[dict]) -> str:
    """Content blocks back to the plain string they are guaranteed to equal."""
    if isinstance(user, str):
        return user
    return "".join(b["text"] for b in user)


def _strict_schema(schema: dict) -> dict:
    """Pydantic's model_json_schema, made acceptable to Anthropic structured
    outputs: the endpoint requires `additionalProperties: false` on every
    object (measured 2026-08-01: 400 without it)."""
    import copy
    out = copy.deepcopy(schema)

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                node.setdefault("additionalProperties", False)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(out)
    return out


def validate_json(text: str, schema: type[T]) -> T:
    """Model output -> validated pydantic instance. """
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        t = t[4:] if t.startswith("json") else t
    try:
        data = json.loads(t)
    except json.JSONDecodeError:
        s, e = t.find("{"), t.rfind("}")
        if s == -1 or e <= s:
            raise
        data = json.loads(t[s:e + 1])
    return schema.model_validate(data)


def _cached_system(system: str) -> list[dict]:
    """System prompt as a block with a cache mark. """
    return [{"type": "text", "text": system,
             "cache_control": {"type": "ephemeral"}}]


def _cache_usage(usage) -> tuple[int, int]:
    """(cache_write, cache_read) tokens, 0 when absent — older SDK responses and
    OpenAI usage objects simply lack the fields."""
    return (getattr(usage, "cache_creation_input_tokens", 0) or 0,
            getattr(usage, "cache_read_input_tokens", 0) or 0)


def preflight(model: str, n_calls: int = 0) -> float:
    """Check SDK, key and price before the first paid call, and project spend.
    Check SDK, key and price before the first paid call, and project spend."""
    load_dotenv()
    prov = provider_for(model)
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
    """One client object, one or two providers behind it."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._clients: dict[str, Any] = {}

    def _client(self, provider: str) -> Any:
        if provider not in self._clients:
            key = os.environ.get(KEY_ENV[provider])
            if not key:
                raise SystemExit(
                    f"{KEY_ENV[provider]} not set (put it in .env). Needed "
                    f"because a task is routed to a {provider} model.")
            if provider == "anthropic":
                import anthropic
                self._clients[provider] = anthropic.Anthropic(
                    api_key=key, max_retries=4)
            else:
                import openai
                self._clients[provider] = openai.OpenAI(
                    api_key=key, max_retries=4)
        return self._clients[provider]

    _no_temperature: set[str] = set()

    def call(self, task: str, system: str, user: str | list[dict],
             max_tokens: int = 1024, temperature: float = 0.0,
             model: str | None = None,
             output_config: dict | None = None) -> str:
        """`model` overrides the task default — that is how a second judge from
        another provider is run over the identical pairs."""
        from fli import storage
        from fli.ops import tracing
        model = model or MODEL_FOR_TASK[task]
        if provider_for(model) == "openai":
            return self._call_openai(task, model, system, _flatten(user),
                                     max_tokens, temperature)
        with tracing.llm_span(task) as span:
            tracing.annotate(span, tracing.input_attrs(model, system,
                                                       _flatten(user)))
            kwargs = dict(model=model, max_tokens=max_tokens,
                          system=_cached_system(system),
                          messages=[{"role": "user", "content": user}])
            if output_config is not None:
                kwargs["output_config"] = output_config
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

    _no_output_config: set[str] = set()

    def call_typed(self, task: str, system: str, user: str | list[dict],
                   schema: type[T], max_tokens: int = 1024,
                   temperature: float = 0.0, model: str | None = None) -> T:
        """`call`, but the answer comes back as a validated pydantic instance.
        `call`, but the answer comes back as a validated pydantic instance."""
        model = model or MODEL_FOR_TASK[task]
        if (provider_for(model) == "anthropic"
                and model not in LLM._no_output_config):
            oc = {"format": {"type": "json_schema",
                             "schema": _strict_schema(schema.model_json_schema())}}
            try:
                return validate_json(
                    self.call(task, system, user, max_tokens, temperature,
                              model, output_config=oc), schema)
            except Exception as e:
                if "output_config" not in str(e):
                    raise
                LLM._no_output_config.add(model)
                print(f"  note: {model} rejected output_config; falling back "
                      f"to client-side validation for the rest of this run.")
        return validate_json(
            self.call(task, system, user, max_tokens, temperature, model),
            schema)

    def call_batch(self, task: str, system: str,
                   items: list[tuple[str, str | list[dict]]],
                   max_tokens: int = 1024, temperature: float = 0.0,
                   model: str | None = None,
                   poll_s: float = 15.0) -> dict[str, str | None]:
        """Send `items` [(custom_id, user), ...] through the Batch API at 50% of
        the synchronous price."""
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
            if waited % 120 < poll_s:
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
            for _ in range(3):
                try:
                    resp = client.chat.completions.create(**build())
                    break
                except Exception as e:
                    msg = str(e).lower()
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
            details = getattr(u, "completion_tokens_details", None)
            reasoning = getattr(details, "reasoning_tokens", None) if details else None
            tracing.annotate(span, tracing.output_attrs(text, in_tok, out_tok))
        storage.log_llm_call(self.conn, task, model, in_tok, out_tok,
                             cost_usd(model, in_tok, out_tok),
                             reasoning_tokens=reasoning)
        return text
