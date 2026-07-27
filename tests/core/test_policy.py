"""fli.core.policy — the editorial policy loader.

Two properties are worth protecting here, and the tests are grouped to match:

1. A malformed policy fails LOUDLY. The file is edited by a non-programmer, so
   a silent default would put the business decision back in the code — exactly
   what config/policy.yml exists to prevent.
2. The shipped file stays honest: it covers every event type the extractor can
   emit, contains nothing the code ignores, and does not claim an owner it
   does not have.
"""
import tempfile
import unittest
from pathlib import Path

from fli.core.policy import POLICY_PATH, PolicyError, load_policy, parse_policy

VALID = {
    "version": 1,
    "owner": "BIT PM — unassigned",
    "effective_from": "2026-07-25",
    "source": "research/facts.md",
    "channels": {"compute_memory": ["hbm", "gpu"],
                 "energy_datacenter": ["megawatt"]},
    "event_type_prior": {"infrastructure": 5, "benchmark": 1},
    "slate_k": 5,
    "hand_weights": {"recency": 1.0},
}


def broken(**changes):
    """A copy of VALID with `changes` applied; None deletes the key."""
    import copy
    d = copy.deepcopy(VALID)
    for k, v in changes.items():
        d.pop(k) if v is None else d.update({k: v})
    return d


class TestRejectsMalformedPolicy(unittest.TestCase):
    """One behaviour: bad policy raises rather than falling back to a default."""

    CASES = [
        ("unknown key",        broken(scoring={"x": 1})),
        ("typo'd key",         broken(slate_k=None, **{"slate-k": 5})),
        ("missing section",    broken(channels=None)),
        ("channel with no terms", broken(channels={"compute_memory": []})),
        ("float event prior",  broken(event_type_prior={"benchmark": 0.7})),
        ("slate_k of zero",    broken(slate_k=0)),
    ]

    def test_each_malformation_raises(self):
        for name, bad in self.CASES:
            with self.subTest(name):
                with self.assertRaises(PolicyError):
                    parse_policy(bad)

    def test_valid_policy_parses(self):
        p = parse_policy(VALID)
        self.assertEqual((1, 5, {"recency": 1.0}),
                         (p.version, p.slate_k, p.hand_weights))

    def test_missing_file_is_an_error_not_a_default(self):
        with self.assertRaises(PolicyError):
            load_policy(Path(tempfile.gettempdir()) / "no-such-policy.yml")


class TestPolicyBehaviour(unittest.TestCase):
    """How the policy is consumed: channel matching and event-type priors."""

    def setUp(self):
        self.p = parse_policy(VALID)

    def test_channel_matching(self):
        cases = [
            ("picks the channel with most hits", "a big GPU cluster with HBM", "compute_memory"),
            ("is case-insensitive",              "a 200 MEGAWATT site",        "energy_datacenter"),
            ("returns None when nothing matches", "a new reasoning benchmark",  None),
            # one hit each -> file order decides, so runs are reproducible
            ("breaks ties by file order",        "gpu and megawatt",           "compute_memory"),
        ]
        for name, text, expected in cases:
            with self.subTest(name):
                self.assertEqual(expected, self.p.channel_for(text))

    def test_unranked_event_type_ranks_lowest_rather_than_raising(self):
        """The extractor may emit a new type before the owner ranks it; that
        must not break a pipeline run. Check C17 reports the gap instead."""
        self.assertEqual(0, self.p.type_prior("brand_new_type"))
        self.assertEqual(0, self.p.type_prior(None))
        self.assertEqual(5, self.p.type_prior("infrastructure"))

    def test_is_owned_is_false_until_a_real_owner_is_named(self):
        """Stops the write-up claiming a domain sign-off that never happened."""
        self.assertFalse(self.p.is_owned)
        self.assertTrue(parse_policy(broken(owner="Jane Doe")).is_owned)


class TestShippedPolicyFile(unittest.TestCase):
    """Properties of the real config/policy.yml, not of the parser."""

    def setUp(self):
        load_policy.cache_clear()
        self.p = load_policy()

    def test_every_event_type_the_extractor_emits_is_ranked(self):
        """An unranked type silently ranks last. Fail here, not in a ranking."""
        from fli.knowledge.extraction import EVENT_TYPES
        missing = [t for t in EVENT_TYPES if t not in self.p.event_type_prior]
        self.assertEqual([], missing, f"policy.yml does not rank: {missing}")

    def test_no_key_in_the_file_is_ignored_by_the_code(self):
        """Config nobody reads is hardcoding with extra steps."""
        import yaml
        from fli.core.policy import _KEYS, _OPTIONAL_KEYS
        keys = set(yaml.safe_load(POLICY_PATH.read_text()))
        self.assertEqual(set(), keys - (_KEYS | _OPTIONAL_KEYS),
                         "policy.yml has a key no code reads")
        self.assertEqual(set(), _KEYS - keys,
                         "policy.yml is missing a required key")

    def test_policy_has_no_domain_owner_yet(self):
        """Deliberate: an engineer is not a portfolio manager. If this fails, a
        real owner was assigned and the write-up should say so."""
        self.assertFalse(self.p.is_owned)


if __name__ == "__main__":
    unittest.main()
