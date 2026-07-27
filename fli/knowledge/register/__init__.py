"""The tracked-entity register (LAYER 2).

Facade over four single-responsibility modules, split out of what was one
580-line file:

    seeding      - the tracked labs and their founding people (verbatim-gated)
    approval     - overrides > per-lab slate > auto-approve rule
    observation  - affiliation currency (re-observe, once per person/lab/day)
    reporting    - per-lab balance, the de-skew evidence (check C13)

Import a submodule directly when you need its internals (tests do); import from
here for the public operations.
"""
from fli.knowledge.register.approval import (auto_approve, auto_approve_rule,
                                             load_overrides, review, show_queue,
                                             valid_candidate_name)
from fli.knowledge.register.observation import observe
from fli.knowledge.register.reporting import (balance_by_lab, print_balance,
                                              report)
from fli.knowledge.register.seeding import (LAB_PAGES, LABS, PERSON_PAGES,
                                            SEED_PEOPLE, seed_labs, seed_people)

__all__ = [
    "LABS", "LAB_PAGES", "PERSON_PAGES", "SEED_PEOPLE",
    "auto_approve", "auto_approve_rule", "balance_by_lab", "load_overrides", "observe",
    "print_balance", "report", "review", "seed_labs", "seed_people",
    "show_queue", "valid_candidate_name",
]
