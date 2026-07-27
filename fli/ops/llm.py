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


def have_api_key() -> bool:
    load_dotenv()
    return bool(os.environ.get("ANTHROPIC_API_KEY"))

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

# USD per 1M tokens (input, output); verify against live pricing before shipping
PRICES = {
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-sonnet-5": (3.00, 15.00),
}
PRICES_CHECKED_AT = None


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = PRICES[model]
    return (input_tokens * pin + output_tokens * pout) / 1_000_000


class LLM:
    def __init__(self, conn: sqlite3.Connection):
        import anthropic  # lazy: keyless environments never need the package
        self.conn = conn
        self.client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    _temp_warned = False   # class-level: warn once per process, not per call

    def call(self, task: str, system: str, user: str, max_tokens: int = 1024,
             temperature: float = 0.0) -> str:
        from fli import storage
        from fli.ops import tracing
        model = MODEL_FOR_TASK[task]
        with tracing.llm_span(task) as span:
            tracing.annotate(span, tracing.input_attrs(model, system, user))
            # temperature=0 for every task here: all of them are structured
            # extraction/classification where the SAME input should give the
            # same answer. It was previously unset, so the API default (1.0)
            # applied and identical calls could differ — which quietly made the
            # judge and the classifier irreproducible.
            #
            # Some models reject an explicit temperature when extended thinking
            # is on. Falling back rather than crashing, and saying so once, so a
            # pipeline run never dies over a reproducibility flag.
            kwargs = dict(model=model, max_tokens=max_tokens, system=system,
                          messages=[{"role": "user", "content": user}])
            try:
                resp = self.client.messages.create(temperature=temperature, **kwargs)
            except Exception as e:
                if "temperature" not in str(e).lower():
                    raise
                if not LLM._temp_warned:
                    print(f"  note: {model} rejected temperature={temperature} "
                          f"({e}); falling back to the model default. Verdicts "
                          f"from this model are NOT bit-reproducible.")
                    LLM._temp_warned = True
                resp = self.client.messages.create(**kwargs)
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
