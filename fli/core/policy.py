"""Editorial policy, loaded from config/policy.yml.

The split this module enforces:

    fli/core/config.py   ENGINEERING constants - timeouts, seeds, cost caps.
                         Changing one is a code change.
    config/policy.yml    BUSINESS decisions - what counts as decision-relevant.
                         Changing one changes the ranking, and is owned by a
                         domain expert rather than by this repository.

Validation is strict on purpose: an unknown key raises. A policy file is edited
by a non-programmer, so a typo (`slate-k` for `slate_k`) must fail loudly rather
than silently fall back to a default - a silent default would put the business
decision back in the code, defeating the point of the file.

Every key here is read by something. Config nobody reads is just hardcoding
with extra steps, so unused keys are deleted rather than kept "for later".
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from fli.core.paths import CONFIG_DIR

POLICY_PATH = CONFIG_DIR / "policy.yml"


@lru_cache(maxsize=512)
def term_pattern(term: str) -> re.Pattern:
    """Word-boundary matcher for one lexicon term.

    Substring matching scored `cluster` against "we clustered similar values" —
    data clustering, not a GPU cluster. Multi-word terms match as phrases with
    flexible whitespace, so a line break inside "training run" still hits.

    Boundaries are applied only at ends that are word characters. `\\b` is a
    transition between a word and a non-word character, so `\\b@handle` demands
    a word character immediately before the `@` and can never match "scientist
    @AnthropicAI" or a handle at the start of a string. No current term begins
    with punctuation; this stops the next one from silently never firing.
    """
    body = r"\s+".join(re.escape(w) for w in term.split())
    left = r"\b" if term[:1].isalnum() or term[:1] == "_" else ""
    right = r"\b" if term[-1:].isalnum() or term[-1:] == "_" else ""
    return re.compile(left + body + right, re.IGNORECASE)

_KEYS = {"version", "owner", "effective_from", "source", "channels",
         "event_type_prior", "slate_k", "hand_weights"}

# `positions` is OPTIONAL so this module loads both a v2 file (no positions)
# and a v3 file (with them). That is what makes the rollout safe: new code
# reads old config, so the code can ship before the policy is swapped.
_OPTIONAL_KEYS = {"positions", "window_days", "show_undated",
                  "max_per_lab", "story_rare_df", "story_days",
                  "primary_rubric"}
_POSITION_KEYS = {"ticker", "name", "weight_pct", "thesis", "exposure_terms"}


class PolicyError(ValueError):
    """Raised when the policy file is malformed. Never swallowed."""


@dataclass(frozen=True)
class Position:
    """One disclosed holding. BIT-SPECIFIC content, kept apart from the general
    channel mechanism so the fund can be swapped without touching the code."""
    ticker: str
    name: str
    weight_pct: float | None
    thesis: str
    exposure_terms: tuple[str, ...]


@dataclass(frozen=True)
class Policy:
    version: int
    owner: str
    effective_from: str
    source: str
    channels: dict[str, tuple[str, ...]]     # name -> lowercase match terms
    event_type_prior: dict[str, int]
    slate_k: int
    hand_weights: dict[str, float]
    window_days: int = 90
    show_undated: bool = False
    # The persona whose ranking also lands in insights.score, and which the
    # evaluation figures describe unless told otherwise. Optional with a
    # default so a v2/v3 policy file that predates the key still loads.
    primary_rubric: str = "investment"
    # Slate composition — render-time only, never seen by the scorer.
    max_per_lab: int = 0            # 0 = no cap
    story_rare_df: float = 0.0      # 0 = same-story suppression off
    story_days: int = 7
    positions: tuple[Position, ...] = ()
    positions_as_of: str | None = None
    positions_source: str | None = None

    def channel_for(self, text: str) -> str | None:
        """Best-matching channel by term hits, or None.

        Terms match on word boundaries, never as substrings. Ties break by
        order in the YAML file, so results are reproducible across machines.
        """
        best, best_hits = None, 0
        for name, terms in self.channels.items():   # dicts keep insertion order
            hits = sum(1 for t in terms if term_pattern(t).search(text))
            if hits > best_hits:
                best, best_hits = name, hits
        return best

    def positions_for(self, text: str) -> list[str]:
        """Tickers whose exposure vocabulary appears in the text.

        Deliberately separate from `channel_for`. Two different questions:

            positions_for  does this touch something the fund owns? (topical —
                           what keyword matching is actually good at)
            channel_for    through what mechanism does it transmit? (semantic —
                           "does this move a number in a thesis", which keywords
                           cannot decide)

        Fusing them put sector nouns like `health` and `broker` in the
        `competitive_displacement` lexicon, so any post mentioning health scored
        as displacement whether or not anything was displaced. Exposure without
        a mechanism is a candidate, not a signal.
        """
        return [p.ticker for p in self.positions
                if any(term_pattern(t).search(text) for t in p.exposure_terms)]

    def type_prior(self, event_type: str | None) -> int:
        """Ordinal prior for an event type; unknown types rank lowest.

        Deliberately not an exception: the extractor may emit a new type before
        the owner has ranked it, and that must not break a pipeline run. Check
        C17 reports the gap instead.
        """
        return self.event_type_prior.get(event_type or "", 0)

    @property
    def is_owned(self) -> bool:
        """False while `owner` is a placeholder, so nothing can claim a domain
        sign-off that never happened."""
        return "unassigned" not in self.owner.lower()


def parse_policy(raw: dict) -> Policy:
    if not isinstance(raw, dict):
        raise PolicyError("policy.yml must be a mapping at the top level")

    unknown = set(raw) - _KEYS - _OPTIONAL_KEYS
    if unknown:
        raise PolicyError(
            f"policy.yml: unknown key(s) {sorted(unknown)}; allowed "
            f"{sorted(_KEYS | _OPTIONAL_KEYS)}. "
            f"Refusing to ignore it - a typo here would silently restore a "
            f"hardcoded default and put the decision back in the code.")
    missing = _KEYS - set(raw)
    if missing:
        raise PolicyError(f"policy.yml: missing key(s) {sorted(missing)}")

    if not isinstance(raw["version"], int):
        raise PolicyError("policy.yml: `version` must be an integer")
    if not isinstance(raw["slate_k"], int) or raw["slate_k"] < 1:
        raise PolicyError("policy.yml: `slate_k` must be a positive integer")
    if not 0 <= float(raw.get("story_rare_df", 0.0)) <= 1:
        raise PolicyError(
            "policy.yml: `story_rare_df` is a FRACTION of claims (0-1), not a "
            "count. 0.03 means 'a token in at most 3% of claims'; 3 would make "
            "every token uncommon and collapse the slate to one item per lab.")
    primary_rubric = raw.get("primary_rubric", "investment")
    if not isinstance(primary_rubric, str) or not primary_rubric.strip():
        raise PolicyError("policy.yml: `primary_rubric` must be the name of a "
                          "rubric file (config/rubrics/<name>.yml)")

    channels = {}
    for name, terms in (raw["channels"] or {}).items():
        if not terms:
            raise PolicyError(f"channels.{name}: needs at least one term "
                              f"(a channel with none can never match)")
        channels[name] = tuple(str(t).lower() for t in terms)
    if not channels:
        raise PolicyError("policy.yml: at least one channel is required")

    prior = raw["event_type_prior"] or {}
    for k, v in prior.items():
        if not isinstance(v, int):
            raise PolicyError(f"event_type_prior.{k}: must be an integer rank, "
                              f"got {v!r} - these are an ordering, not weights")

    weights = raw["hand_weights"] or {}
    for k, v in weights.items():
        if not isinstance(v, (int, float)):
            raise PolicyError(f"hand_weights.{k}: must be numeric")

    pos_block = raw.get("positions") or {}
    if pos_block and not isinstance(pos_block, dict):
        raise PolicyError("positions: must be a mapping with as_of/source/holdings")
    positions = []
    for h in (pos_block.get("holdings") or []):
        _reject_unknown_position(h)
        for req in ("ticker", "name", "thesis", "exposure_terms"):
            if not h.get(req):
                raise PolicyError(f"positions.holdings[{h.get('ticker','?')}]: "
                                  f"missing `{req}`")
        positions.append(Position(
            ticker=str(h["ticker"]).upper(),
            name=str(h["name"]),
            weight_pct=float(h["weight_pct"]) if h.get("weight_pct") is not None else None,
            thesis=str(h["thesis"]),
            exposure_terms=tuple(str(t).lower() for t in h["exposure_terms"]),
        ))
    if positions and not pos_block.get("as_of"):
        raise PolicyError(
            "positions: `as_of` is required. Holdings go stale quarterly, and a "
            "position list with no date silently claims to be current.")

    return Policy(
        version=raw["version"],
        owner=str(raw["owner"]),
        effective_from=str(raw["effective_from"]),
        source=str(raw["source"]),
        channels=channels,
        event_type_prior={str(k): int(v) for k, v in prior.items()},
        slate_k=raw["slate_k"],
        hand_weights={str(k): float(v) for k, v in weights.items()},
        window_days=int(raw.get("window_days", 90)),
        show_undated=bool(raw.get("show_undated", False)),
        primary_rubric=primary_rubric.strip(),
        max_per_lab=int(raw.get("max_per_lab", 0)),
        story_rare_df=float(raw.get("story_rare_df", 0.0)),
        story_days=int(raw.get("story_days", 7)),
        positions=tuple(positions),
        positions_as_of=pos_block.get("as_of"),
        positions_source=pos_block.get("source"),
    )


def _reject_unknown_position(h: dict) -> None:
    unknown = set(h) - _POSITION_KEYS
    if unknown:
        raise PolicyError(
            f"positions.holdings[{h.get('ticker','?')}]: unknown key(s) "
            f"{sorted(unknown)}; allowed {sorted(_POSITION_KEYS)}")


@lru_cache(maxsize=4)
def load_policy(path: str | Path | None = None) -> Policy:
    """Load and validate. Cached per path; tests that write a policy file should
    call `load_policy.cache_clear()`."""
    p = Path(path) if path else POLICY_PATH
    if not p.exists():
        raise PolicyError(
            f"policy file not found: {p}\nIt holds the business decisions and is "
            f"required - there is deliberately no hardcoded fallback.")
    try:
        raw = yaml.safe_load(p.read_text())
    except yaml.YAMLError as e:
        raise PolicyError(f"{p}: invalid YAML: {e}") from e
    return parse_policy(raw)


def describe(policy: Policy) -> str:
    """One-screen summary, printed every run so each run states its policy."""
    owner = policy.owner if policy.is_owned else f"{policy.owner}  [NOT REVIEWED]"
    return (f"policy v{policy.version} ({policy.effective_from})  owner: {owner}\n"
            f"  channels: {', '.join(policy.channels)}\n"
            f"  event types ranked: {len(policy.event_type_prior)}  "
            f"slate_k: {policy.slate_k}  window: {policy.window_days}d")
