"""fli.knowledge.register.x_identities — the admission rule for an X handle.

`classify` is kept as a pure function precisely so these run with no network
and no token: the decision that admits a row into `identities` is the part
worth pinning down, not the HTTP around it.
"""
import unittest
from unittest import mock

from fli import storage
from fli.core.http import FetchError
from fli.knowledge.register import x_identities as X
from fli.knowledge.register.x_identities import (TIER_FOR_METHOD,
                                                 bio_names_lab, classify)
from tests.helpers import DBTestCase


class TestBioMatching(unittest.TestCase):
    def test_handles_and_plain_names_both_match(self):
        self.assertTrue(bio_names_lab("Research scientist @AnthropicAI", "Anthropic"))
        self.assertTrue(bio_names_lab("Working on Gemini at Google DeepMind",
                                      "Google DeepMind"))
        self.assertTrue(bio_names_lab("co-founder, xAI", "xAI"))

    def test_substring_collisions_do_not_match(self):
        """The failure this rules out is the `cluster` bug in another costume:
        substring matching would read 'metaphor' as Meta and 'unfairly' as FAIR,
        attributing a stranger's posts to a lab."""
        self.assertFalse(bio_names_lab("I write about metaphor and meaning", "Meta AI"))
        self.assertFalse(bio_names_lab("Treated unfairly by recommender systems",
                                       "Meta AI"))
        self.assertFalse(bio_names_lab("Interested in mistrals and other winds",
                                       "Mistral"))


class TestAdmissionRule(unittest.TestCase):
    BIO = {"name": "Some Researcher", "description": "Research scientist @AnthropicAI"}
    BLANK = {"name": "Some Researcher", "description": ""}

    def test_bio_naming_the_lab_is_self_link_verbatim(self):
        self.assertEqual(classify(self.BIO, "Anthropic", None),
                         ("accept", "verbatim", "self_link"))

    def test_silent_bio_falls_back_to_the_register(self):
        """A bio that says nothing is still admissible if the person is already
        in the register on other evidence — but tiered `name_match_only`, which
        is what it is. `corroborated` means an independent second source, and
        check C4 enforces that pairing."""
        self.assertEqual(classify(self.BLANK, "Anthropic", 42),
                         ("accept", "name_match_only", "exact"))

    def test_tiers_satisfy_the_C4_pairing_rule(self):
        """C4 lives in the validation layer and only runs against a populated
        DB, so it caught this in production rather than in CI. The rule itself
        is TIER_FOR_METHOD, defined once in x_identities and imported by
        checks.py — this test pins classify's output to it at the source."""
        for profile, pid in [(self.BIO, None), (self.BLANK, 42)]:
            _d, tier, method = classify(profile, "Anthropic", pid)
            self.assertIn(tier, TIER_FOR_METHOD[method], f"{tier}/{method}")

    def test_unknown_person_with_silent_bio_is_rejected(self):
        """No evidence, no row. This is the case that keeps a mistyped or
        squatted handle out of the register instead of inventing a person."""
        self.assertEqual(classify(self.BLANK, "Anthropic", None)[0], "reject")

    def test_wrong_lab_in_bio_does_not_admit(self):
        self.assertEqual(classify(self.BIO, "OpenAI", None)[0], "reject")


class TestNameKeyAccentFold(unittest.TestCase):
    """Entity resolution across sources that disagree about diacritics.

    This is not hypothetical: 'Timothée Lacroix' was already in the register
    and an X lookup for 'Timothee Lacroix' rejected him as a stranger. Five of
    55 registered people have accented names, and every source spells them
    differently.
    """

    def test_accented_and_plain_spellings_are_one_person(self):
        from fli.core.text import name_key
        for accented, plain in [("Timothée Lacroix", "Timothee Lacroix"),
                                ("Tim Rocktäschel", "Tim Rocktaschel"),
                                ("Amélie Héliou", "Amelie Heliou"),
                                ("Baptiste Rozière", "Baptiste Roziere")]:
            self.assertEqual(name_key(accented), name_key(plain), accented)

    def test_the_fold_does_not_merge_different_people(self):
        """Folding is permissive, so the guard is that it stays letter-exact
        once the marks are gone — it must not turn near-names into one."""
        from fli.core.text import name_key
        for a, b in [("Jean Dupont", "Jean Dupond"), ("Li Wei", "Li Wu"),
                     ("Chris Olah", "Chris Olahs")]:
            self.assertNotEqual(name_key(a), name_key(b), f"{a} vs {b}")

    def test_verbatim_matching_is_untouched(self):
        """`norm` backs the quote invariant (C2) and must NOT fold: a quote
        that differs by an accent differs from the stored bytes."""
        from fli.core.text import contains_verbatim
        self.assertFalse(contains_verbatim("we thank Timothée Lacroix",
                                           "Timothee Lacroix"))


class TestHomonymGuard(DBTestCase):
    """Two tracked people sharing a folded name key. A name-only lookup that
    returned whichever row came first would attach a live profile — and every
    later observation made through it — to the wrong person, which no check
    could catch afterwards. Both platform lookups must abstain instead."""

    def _add_person(self, name):
        return self.conn.execute(
            "INSERT INTO people (canonical_name, discovered_via, first_seen_at)"
            " VALUES (?, 'manual', '2026-01-01T00:00:00Z')", (name,)).lastrowid

    def test_unique_name_still_resolves(self):
        pid = self._add_person("Liang Wenfeng")
        self.assertEqual(X._person_by_name(self.conn, "Wenfeng Liang"), pid)

    def test_two_people_one_key_abstains_on_both_platforms(self):
        from fli.knowledge.register import gh_identities as GH
        self._add_person("J. Smith")
        self._add_person("Smith J.")          # same name_key, different person
        self.assertIsNone(X._person_by_name(self.conn, "J. Smith"))
        self.assertIsNone(GH._person_by_name(self.conn, "Smith J."))

    def test_abstention_downgrades_admission_not_identity(self):
        """With the register ambiguous, a silent bio must now REJECT (there is
        no name_match_only to fall back on) while a bio naming the lab still
        admits as self_link — the profile's own words are not ambiguous."""
        self._add_person("J. Smith")
        self._add_person("Smith J.")
        pid = X._person_by_name(self.conn, "J. Smith")
        self.assertEqual(classify({"name": "J. Smith", "description": ""},
                                  "Anthropic", pid)[0], "reject")
        self.assertEqual(classify({"name": "J. Smith",
                                   "description": "Research scientist @AnthropicAI"},
                                  "Anthropic", pid),
                         ("accept", "verbatim", "self_link"))


class _FakeClient:
    """Stands in for XClient. The four profiles below are the four things the
    live API actually does: answer, answer about someone else, 404, and 429."""

    PROFILES = {
        "newperson": {"name": "Brand New Person",
                      "description": "Research scientist @AnthropicAI"},
        "known":     {"name": "Known Person", "description": "no affiliation stated"},
        "stranger":  {"name": "Random Stranger", "description": "crypto enjoyer"},
        "gone":      None,
        "boom":      "RAISE",
    }

    def __init__(self, *a, **k):
        self.users_read = 0

    def user_profile(self, handle):
        self.users_read += 1
        p = self.PROFILES[handle]
        if p == "RAISE":
            raise FetchError("HTTP 429 rate limited")
        return p


class TestWritePath(DBTestCase):
    """Exercises the DATABASE writes, not just the decision.

    This class exists because of a specific failure: `classify` was fully
    tested and the module still crashed twice in production on CHECK
    constraints (`people.discovered_via`, `rejections.stage`) that only the
    write path touches. Testing a pure function proves the decision is right,
    not that the row is admissible.
    """

    def setUp(self):
        super().setUp()
        self.conn.execute("INSERT INTO labs (name, is_public_company) VALUES ('Anthropic', 0)")
        self.conn.execute("INSERT INTO labs (name, is_public_company) VALUES ('Meta AI', 1)")
        self.conn.execute(
            "INSERT INTO people (canonical_name, seniority_tier, discovered_via,"
            " first_seen_at) VALUES ('Known Person','ic','seed',?)", (storage.now_utc(),))
        self.conn.commit()

    def _run(self, candidates):
        with mock.patch("fli.ingestion.x_api.bearer_token", lambda: "fake"), \
             mock.patch("fli.ingestion.x_api.XClient", _FakeClient):
            return X.seed_x_identities(self.conn, candidates=candidates)

    def test_every_branch_writes_admissible_rows(self):
        res = self._run([
            ("newperson", "Anthropic", "Brand New Person"),   # accept, creates a person
            ("known",     "Meta AI",   "Known Person"),       # accept, links existing
            ("stranger",  "Anthropic", "Someone Else"),       # reject
            ("gone",      "Anthropic", "Ghost"),              # 404
            ("boom",      "Anthropic", "Boom"),               # 429
        ])
        self.assertEqual((res["accepted"], res["rejected"], res["errors"]), (2, 2, 1))
        # exactly one person created: the linked one must not be duplicated
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM people").fetchone()[0], 2)
        tiers = dict(self.conn.execute(
            "SELECT handle, confidence_tier FROM identities WHERE platform='x'"))
        self.assertEqual(tiers, {"newperson": "verbatim", "known": "name_match_only"})

    def test_every_evidence_quote_occurs_in_the_stored_document(self):
        """THE INVARIANT, checked at the point of writing.

        The first version stored the profile as `json.dumps(...)`, which
        escapes newlines, while the evidence held the raw bio — so the quote
        was not a substring of the document and C2 failed on 8 rows in the live
        database. C2 only runs against a populated DB; this runs in CI.
        """
        from fli.core.text import contains_verbatim
        self._run([("newperson", "Anthropic", "Brand New Person"),
                   ("known", "Meta AI", "Known Person")])
        rows = list(self.conn.execute(
            "SELECT e.id, e.verbatim_content, d.raw_content FROM evidence e"
            " JOIN raw_documents d ON d.id = e.document_id"))
        self.assertTrue(rows, "no evidence written")
        for r in rows:
            self.assertTrue(
                contains_verbatim(r["raw_content"], r["verbatim_content"]),
                f"evidence {r['id']} is not a substring of its document")

    def test_no_orphan_evidence(self):
        """C10: evidence that proves no claim is an unfalsifiable row."""
        self._run([("newperson", "Anthropic", "Brand New Person"),
                   ("stranger", "Anthropic", "Someone Else")])
        orphans = self.conn.execute(
            "SELECT COUNT(*) FROM evidence e WHERE NOT EXISTS"
            " (SELECT 1 FROM identities i WHERE i.evidence_id = e.id)"
            " AND NOT EXISTS (SELECT 1 FROM affiliations a WHERE a.evidence_id = e.id)"
        ).fetchone()[0]
        self.assertEqual(orphans, 0)

    def test_rejections_are_recorded_not_dropped(self):
        self._run([("stranger", "Anthropic", "Someone Else"),
                   ("gone", "Anthropic", "Ghost")])
        reasons = {r[0] for r in self.conn.execute(
            "SELECT reason FROM rejections WHERE reason LIKE 'x_handle%'")}
        self.assertEqual(reasons, {"x_handle_unverified", "x_handle_not_found"})

    def test_rerunning_costs_nothing(self):
        cands = [("newperson", "Anthropic", "Brand New Person")]
        self._run(cands)
        again = self._run(cands)
        self.assertEqual(again.get("spend_usd", 0.0), 0.0)

    def test_name_mismatch_is_reported(self):
        """A handle that resolves to someone else is data about list decay, so
        it must surface rather than being silently corrected."""
        res = self._run([("newperson", "Anthropic", "Noam Brown")])
        self.assertEqual([(h, e, g) for h, e, g in res["mismatches"]],
                         [("newperson", "Noam Brown", "Brand New Person")])


class _FakeReobsClient:
    """Profiles for the re-observation cases: unchanged, moved, and blanked."""

    PROFILES = {
        "steady": {"name": "Steady Person", "description": "Researcher @AnthropicAI"},
        "mover":  {"name": "Moving Person", "description": "Now scaling llamas at Meta AI"},
        "blank":  {"name": "Blank Person",  "description": "opinions my own"},
    }

    def __init__(self, *a, **k):
        self.users_read = 0

    def user_profile(self, handle):
        self.users_read += 1
        return self.PROFILES[handle]


class TestBioReobservation(DBTestCase):
    """reobserve_x_bios: the X-side of observe(). A bio rewritten from lab A
    to lab B must append the arrival observation that mobility synthesis pairs
    into a personnel event in the same pipeline run."""

    def setUp(self):
        super().setUp()
        # Departure observed relative to the real clock: the arrival is stamped
        # now_utc(), so a fixed date here would drift past the 90-day pairing
        # window as the calendar advances and fail this suite from October on.
        from datetime import datetime, timedelta, timezone
        old = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
        self.conn.execute("INSERT INTO labs (id, name) VALUES (1,'Anthropic'),(2,'Meta AI')")
        self.mover_id = self._identity("mover", "Moving Person", lab_id=1,
                                       observed_at=old)
        self._identity("steady", "Steady Person", lab_id=1, observed_at=old)
        self._identity("blank", "Blank Person", lab_id=1, observed_at=old)

    def _identity(self, handle, name, lab_id, observed_at) -> int:
        """A person admitted at seeding time: identity + dated affiliation,
        both evidenced by a stored profile document, as the seeder writes them."""
        cur = self.conn.execute(
            "INSERT INTO people (canonical_name, seniority_tier, discovered_via,"
            " first_seen_at) VALUES (?,?,?,?)", (name, "ic", "seed", storage.now_utc()))
        pid = cur.lastrowid
        url = f"https://x.com/{handle}#profile"
        sid = storage.upsert_source(self.conn, "social", f"@{handle} profile", url,
                                    channel="third_party", purpose="register")
        doc, _ = storage.store_document(self.conn, sid, "social", url,
                                        f"@{handle}\n{name}\n\nold bio\n", None)
        ev = storage.insert_evidence(self.conn, doc, "{}", name, "exact", 1.0)
        self.conn.execute(
            "INSERT INTO identities (person_id, platform, handle, confidence_tier,"
            " resolution_method, evidence_id) VALUES (?,?,?,?,?,?)",
            (pid, "x", handle, "verbatim", "self_link", ev))
        self.conn.execute(
            "INSERT INTO affiliations (person_id, lab_id, basis, observed_at,"
            " evidence_id) VALUES (?,?,?,?,?)",
            (pid, lab_id, "page_verbatim", observed_at, ev))
        return pid

    def _run(self, **kw):
        with mock.patch("fli.ingestion.x_api.bearer_token", lambda: "fake"), \
             mock.patch("fli.ingestion.x_api.XClient", _FakeReobsClient):
            return X.reobserve_x_bios(self.conn, **kw)

    def test_moved_bio_appends_arrival_and_mobility_pairs_it(self):
        res = self._run()
        self.assertEqual(res["new_lab"], 1)     # mover: Anthropic -> Meta AI
        self.assertEqual(res["no_lab"], 1)      # blank: no tracked lab named
        # the arrival observation is exactly what mobility synthesis pairs
        from fli.knowledge.register.mobility import detect_mobility_events
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            out = detect_mobility_events(self.conn, window_days=90)
        self.assertEqual(out["created"], 1)
        ev = self.conn.execute(
            "SELECT attributed_person_id p, attributed_lab_id l FROM insights"
            " WHERE event_type='personnel'").fetchone()
        self.assertEqual((ev["p"], ev["l"]), (self.mover_id, 2))

    def test_unchanged_bio_is_a_dated_reobservation_not_a_move(self):
        self._run()
        import contextlib, io
        from fli.knowledge.register.mobility import detect_mobility_events
        with contextlib.redirect_stdout(io.StringIO()):
            out = detect_mobility_events(self.conn, window_days=90)
        # steady's re-observation is same-lab: currency, never an event
        steady = self.conn.execute(
            "SELECT COUNT(*) FROM affiliations a JOIN people p ON p.id=a.person_id"
            " WHERE p.canonical_name='Steady Person' AND a.lab_id=1").fetchone()[0]
        self.assertEqual(steady, 2)
        self.assertEqual(out["created"], 1)     # only the mover fires

    def test_cadence_gate_skips_recently_observed(self):
        self._run()
        again = self._run()
        self.assertEqual(again.get("observed", 0), 0)
        self.assertEqual(again.get("spend_usd", 1.0), 0.0)

    def test_dry_run_spends_nothing_and_writes_nothing(self):
        before = self.conn.execute("SELECT COUNT(*) FROM affiliations").fetchone()[0]
        res = self._run(dry_run=True)
        self.assertTrue(res["dry_run"])
        self.assertEqual(
            before, self.conn.execute("SELECT COUNT(*) FROM affiliations").fetchone()[0])

    def test_cost_cap_bounds_a_single_run(self):
        res = self._run(max_lookups=1)
        self.assertEqual(res["observed"] + res["no_lab"] + res["errors"], 1)

    def test_evidence_quotes_occur_in_stored_documents(self):
        """Same C2 invariant the seeder is pinned to: the bio quoted as
        evidence must be a substring of the stored profile document."""
        from fli.core.text import contains_verbatim
        self._run()
        rows = list(self.conn.execute(
            "SELECT e.id, e.verbatim_content, d.raw_content FROM evidence e"
            " JOIN raw_documents d ON d.id = e.document_id"
            " WHERE e.locator LIKE '%reobservation%'"))
        self.assertTrue(rows, "no re-observation evidence written")
        for r in rows:
            self.assertTrue(
                contains_verbatim(r["raw_content"], r["verbatim_content"]),
                f"evidence {r['id']} is not a substring of its document")


if __name__ == "__main__":
    unittest.main()
