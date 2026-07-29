"""The tracked-entity register.

Facade over four single-responsibility modules:

    seeding      the tracked labs and their founding people (verbatim-gated)
    approval     overrides > per-lab slate > auto-approve rule
    observation  affiliation currency, re-observed once per person/lab/day
    reporting    per-lab balance, the de-skew evidence

Import from here for the public operations; import a submodule directly when
you need its internals, as the tests do.
"""
from fli.knowledge.register.approval import (auto_approve, auto_approve_rule,
                                             load_overrides, review, show_queue,
                                             valid_candidate_name)
from fli.knowledge.register.mobility import detect_mobility_events
from fli.knowledge.register.observation import observe
from fli.knowledge.register.reporting import (balance_by_lab, print_balance,
                                              report)
from fli.knowledge.register.seeding import (LAB_PAGES, LABS, PERSON_PAGES,
                                            SEED_PEOPLE, seed_labs, seed_people)
from fli.knowledge.register.x_identities import reobserve_x_bios, seed_x_identities

__all__ = [
    "LABS", "LAB_PAGES", "PERSON_PAGES", "SEED_PEOPLE",
    "auto_approve", "auto_approve_rule", "balance_by_lab", "detect_mobility_events",
    "load_overrides", "observe",
    "print_balance", "reobserve_x_bios", "report", "review", "seed_labs",
    "seed_people", "seed_x_identities", "show_queue", "valid_candidate_name",
]
