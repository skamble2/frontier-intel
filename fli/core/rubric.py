"""Judging rubrics, loaded from config/rubrics/*.yml.

A rubric is the definition of "important" for ONE audience. It was previously a
string constant in judge.py, which had two consequences worth stating:

  1. Changing what the system considers important meant editing Python, so the
     decision sat with the engineer rather than with the reader it serves.
  2. There could only be one of them, so there could only be one ranking.

The second turned out to matter. Measured on a single rubric, `commercial`
events took 60% of the top 50 at a 6.2x lift while `research` and `benchmark` —
34% of the corpus — took none at all. That is the investment rubric working
correctly and the AI team being served nothing. Two audiences need two
definitions of important, and a definition that lives in a file can be
swapped, diffed and owned.

The rubric name is part of the labeler id (`llm:<model>/<rubric>/<version>`),
so judgements made under different definitions can never be pooled by accident.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache


import yaml

from fli.core.paths import CONFIG_DIR

RUBRIC_DIR = CONFIG_DIR / "rubrics"

_REQUIRED = {"name", "version", "audience", "question", "rules", "banned",
             "use_policy_channels"}
_OPTIONAL = {"owner"}


class RubricError(ValueError):
    """Raised when a rubric file is malformed. Never swallowed."""


@dataclass(frozen=True)
class Rubric:
    name: str
    version: int
    audience: str
    owner: str
    question: str
    rules: tuple[str, ...]
    banned: tuple[str, ...]
    use_policy_channels: bool

    @property
    def label_suffix(self) -> str:
        """The part of a labeler id that identifies this rubric AND its
        version. Bumping `version` in the YAML makes new judgements a distinct
        labeler, so a rubric edit can never silently contaminate a prior run."""
        return f"{self.name}/r{self.version}"

    @property
    def is_owned(self) -> bool:
        return "unassigned" not in self.owner.lower()


def parse_rubric(raw: dict) -> Rubric:
    if not isinstance(raw, dict):
        raise RubricError("a rubric file must be a mapping at the top level")
    unknown = set(raw) - _REQUIRED - _OPTIONAL
    if unknown:
        raise RubricError(f"unknown key(s) {sorted(unknown)}; allowed "
                          f"{sorted(_REQUIRED | _OPTIONAL)}")
    missing = _REQUIRED - set(raw)
    if missing:
        raise RubricError(f"missing key(s) {sorted(missing)}")
    if not raw["rules"]:
        raise RubricError("`rules` is empty: a rubric with no ordering rules "
                          "cannot separate any pair")
    if not isinstance(raw["version"], int):
        raise RubricError("`version` must be an integer — it forms part of the "
                          "labeler id")
    return Rubric(
        name=str(raw["name"]),
        version=int(raw["version"]),
        audience=str(raw["audience"]),
        owner=str(raw.get("owner", "unassigned")),
        question=str(raw["question"]).strip(),
        rules=tuple(" ".join(str(r).split()) for r in raw["rules"]),
        banned=tuple(" ".join(str(b).split()) for b in raw["banned"]),
        use_policy_channels=bool(raw["use_policy_channels"]),
    )


@lru_cache(maxsize=8)
def load_rubric(name: str) -> Rubric:
    path = RUBRIC_DIR / f"{name}.yml"
    if not path.exists():
        raise RubricError(
            f"no rubric {name!r} at {path}. Available: {', '.join(available())}")
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise RubricError(f"{path}: invalid YAML: {e}") from e
    r = parse_rubric(raw)
    if r.name != name:
        raise RubricError(f"{path}: `name` is {r.name!r} but the file is "
                          f"{name}.yml — the id would not round-trip")
    return r


def available() -> list[str]:
    return sorted(p.stem for p in RUBRIC_DIR.glob("*.yml"))
