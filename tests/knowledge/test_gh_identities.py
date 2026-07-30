"""fli.knowledge.register.gh_identities — the admission rule for a GitHub login.

`classify` and `as_document` are pure so the rule that admits a row into
`identities` is testable with no network and no token.
"""
import unittest

from fli.knowledge.register.gh_identities import (as_document, classify,
                                                  company_names_lab, is_bot,
                                                  lab_from_profile, member_quote,
                                                  prune_unnameable_github_people,
                                                  repo_slugs, retract_unverifiable)
from tests.helpers import memory_db

LABS = ["OpenAI", "Anthropic", "Google DeepMind", "Meta AI", "Mistral",
        "DeepSeek", "Qwen", "xAI"]


class TestOrgHandles(unittest.TestCase):
    """GitHub's `company` field carries the ORG HANDLE, which is often not the
    lab's name. Anthropic's org is `anthropics` — plural — and a word-boundary
    match on "anthropic" cannot match it, so three people whose company field
    literally named their employer were rejected."""

    def test_plural_org_handle_matches(self):
        self.assertTrue(company_names_lab("@anthropics", "", "Anthropic"))
        self.assertTrue(company_names_lab("@anthropics @glide-browser", "",
                                          "Anthropic"))

    def test_org_handles_for_every_lab_resolve(self):
        for company, lab in [("@openai", "OpenAI"), ("@mistralai", "Mistral"),
                             ("@deepseek-ai", "DeepSeek"), ("@qwenlm", "Qwen"),
                             ("@xai-org", "xAI")]:
            self.assertTrue(company_names_lab(company, "", lab), company)


class TestEmployerBeatsRepo(unittest.TestCase):
    """A person is found via whatever repo they commit to, which is not the
    same as who employs them."""

    def test_company_field_overrides_the_discovering_repo(self):
        # found on meta-llama, but their company says Anthropic
        self.assertEqual(
            lab_from_profile({"company": "@anthropics", "bio": ""}, LABS),
            "Anthropic")
        # found on openai-python, but their company says DeepMind
        self.assertEqual(
            lab_from_profile({"company": "Google Deepmind", "bio": ""}, LABS),
            "Google DeepMind")

    def test_a_profile_naming_no_tracked_lab_returns_none(self):
        for company in ("Tsinghua University", "Intel", "@voxel51", ""):
            self.assertIsNone(
                lab_from_profile({"company": company, "bio": ""}, LABS), company)


class TestSignalPrecedence(unittest.TestCase):
    """Signals are ordered by how much inference each requires. Org membership
    is GitHub's own answer and needs none, so it outranks a self-declared
    company string, which outranks agreeing with a name we already hold."""

    SILENT = {"name": "Some Dev", "company": None, "bio": None}

    def test_org_membership_admits_without_any_other_signal(self):
        self.assertEqual(
            classify(self.SILENT, "Anthropic", None, is_org_member=True),
            ("accept", "verbatim", "self_link"))

    def test_without_membership_the_same_profile_is_rejected(self):
        """The only difference is the membership flag — proving it is the
        signal doing the work, not something else in the profile."""
        self.assertEqual(
            classify(self.SILENT, "Anthropic", None, is_org_member=False)[0],
            "reject")

    def test_membership_does_not_override_a_missing_lab(self):
        """A member of some org is admitted for THAT lab; the caller decides
        which lab the flag refers to, so a wrong lab is a caller bug, not a
        silent misattribution here."""
        d, tier, method = classify(self.SILENT, "OpenAI", None, is_org_member=True)
        self.assertEqual((d, tier, method), ("accept", "verbatim", "self_link"))


class TestBotFilter(unittest.TestCase):
    """GitHub's `type` field catches Apps but not user accounts operated as
    bots, which is what `stainless-bot` is."""

    def test_automation_accounts_are_excluded(self):
        for login in ("stainless-bot", "dependabot[bot]", "github-actions-bot"):
            self.assertTrue(is_bot(login), login)

    def test_people_are_not_mistaken_for_bots(self):
        for login in ("RobertCraigie", "abotta", "robotnik"):
            self.assertFalse(is_bot(login), login)


class TestCompanyMatching(unittest.TestCase):
    def test_company_field_matches_on_word_boundaries(self):
        self.assertTrue(company_names_lab("@anthropic", "", "Anthropic"))
        self.assertTrue(company_names_lab("Google DeepMind", "", "Google DeepMind"))
        self.assertTrue(company_names_lab("", "researcher at Mistral", "Mistral"))

    def test_substring_collisions_do_not_match(self):
        """Same failure as the channel lexicon: substring matching reads
        'Metaphysics Inc' as Meta and attributes a stranger's commits."""
        self.assertFalse(company_names_lab("Metaphysics Inc", "", "Meta AI"))
        self.assertFalse(company_names_lab("", "I write about openairwaves",
                                           "OpenAI"))


class TestAdmissionRule(unittest.TestCase):
    AT_LAB = {"name": "Some Dev", "company": "@anthropic", "bio": ""}
    SILENT = {"name": "Some Dev", "company": None, "bio": None}

    def test_company_naming_the_lab_is_self_link(self):
        self.assertEqual(classify(self.AT_LAB, "Anthropic", None),
                         ("accept", "verbatim", "self_link"))

    def test_silent_company_falls_back_to_the_register(self):
        self.assertEqual(classify(self.SILENT, "Anthropic", 42),
                         ("accept", "name_match_only", "exact"))

    def test_unknown_person_with_silent_company_is_rejected(self):
        """A contributor to a lab's SDK is usually an outside user. No
        evidence, no row."""
        self.assertEqual(classify(self.SILENT, "Anthropic", None)[0], "reject")

    def test_wrong_lab_does_not_admit(self):
        self.assertEqual(classify(self.AT_LAB, "OpenAI", None)[0], "reject")

    def test_tiers_satisfy_the_C4_pairing_rule(self):
        tier_for_method = {"exact": {"verbatim", "name_match_only"},
                           "self_link": {"verbatim"}}
        for profile, pid in [(self.AT_LAB, None), (self.SILENT, 42)]:
            _d, tier, method = classify(profile, "Anthropic", pid)
            self.assertIn(tier, tier_for_method[method], f"{tier}/{method}")


class TestDocumentRendering(unittest.TestCase):
    def test_quoted_field_appears_verbatim_in_the_document(self):
        """THE INVARIANT. The X version stored json.dumps output, which escapes
        newlines, so the evidence quote was not a substring of the document and
        check C2 failed on 8 rows."""
        from fli.core.text import contains_verbatim
        profile = {"name": "Some Dev", "company": "@anthropic",
                   "bio": "lines\nwith\nnewlines", "id": 1}
        doc = as_document("somedev", profile)
        for field in ("@anthropic", "Some Dev", "lines\nwith\nnewlines"):
            self.assertTrue(contains_verbatim(doc, field), field)

    def test_org_membership_is_written_into_the_document(self):
        """THE BUG THIS CLOSES. The first membership path quoted "public member
        of the organisation" — a description of the API's answer, not bytes it
        returned — so 53 rows cited a string that appears nowhere in the stored
        document and failed C2. The quote must be a substring of the document."""
        from fli.core.text import contains_verbatim
        doc = as_document("dev", {"name": "Dev A", "id": 1}, member_of="openai")
        self.assertTrue(contains_verbatim(doc, member_quote("openai")))

    def test_a_profile_with_no_membership_omits_the_line(self):
        self.assertNotIn("public_member_of",
                         as_document("dev", {"name": "Dev A", "id": 1}))


class TestRetractionAndPrune(unittest.TestCase):
    """The system must be able to take a row back. Evidence-first is only worth
    something if a row that stops being evidence stops being in the register."""

    def _person_with_gh_identity(self, conn, name, quote, doc_body):
        from fli import storage
        sid = storage.upsert_source(conn, "github", f"{name} profile",
                                    f"https://github.com/{name}#profile",
                                    channel="third_party", purpose="register")
        doc_id, _ = storage.store_document(
            conn, sid, "github", f"https://github.com/{name}#profile",
            doc_body, None)
        storage.log_fetch(conn, sid, "ok", 1, "profile")
        ev = storage.insert_evidence(
            conn, doc_id, '{"kind": "github_profile"}', quote, "exact", 1.0)
        cur = conn.execute(
            "INSERT INTO people (canonical_name, seniority_tier, discovered_via,"
            " first_seen_at) VALUES (?,?, 'seed','t')", (name, "ic"))
        pid = cur.lastrowid
        conn.execute("INSERT INTO identities (person_id, platform, handle,"
                     " confidence_tier, resolution_method, evidence_id)"
                     " VALUES (?,?,?,?,?,?)",
                     (pid, "github", name, "verbatim", "self_link", ev))
        conn.commit()
        return pid

    def test_evidence_that_no_longer_matches_is_retracted(self):
        conn = memory_db()
        conn.execute("INSERT INTO labs (name) VALUES ('OpenAI')")
        # the exact bug: a quote that is not a substring of the stored document
        pid = self._person_with_gh_identity(
            conn, "Ada Lovelace", "public member of the organisation",
            "github:ada\nname: Ada Lovelace\ncompany: \n")
        retract_unverifiable(conn)
        self.assertIsNone(conn.execute(
            "SELECT 1 FROM identities WHERE person_id=?", (pid,)).fetchone())
        self.assertIsNone(conn.execute(
            "SELECT 1 FROM people WHERE id=?", (pid,)).fetchone())

    def test_evidence_that_still_matches_is_left_alone(self):
        conn = memory_db()
        conn.execute("INSERT INTO labs (name) VALUES ('OpenAI')")
        pid = self._person_with_gh_identity(
            conn, "Grace Hopper", "@openai",
            "github:grace\nname: Grace Hopper\ncompany: @openai\n")
        retract_unverifiable(conn)
        self.assertIsNotNone(conn.execute(
            "SELECT 1 FROM people WHERE id=?", (pid,)).fetchone())

    def test_a_login_masquerading_as_a_name_is_pruned(self):
        """A register of people cannot hold a row it cannot name. `liann-oai`
        is a login, not a person — it fails C9 even though its evidence is
        sound, so the prune, not the retract, removes it."""
        conn = memory_db()
        conn.execute("INSERT INTO labs (name) VALUES ('OpenAI')")
        pid = self._person_with_gh_identity(
            conn, "liann-oai", "@openai",
            "github:liann-oai\nname: liann-oai\ncompany: @openai\n")
        # evidence is fine, so retract must NOT be what removes it
        retract_unverifiable(conn)
        self.assertIsNotNone(conn.execute(
            "SELECT 1 FROM people WHERE id=?", (pid,)).fetchone())
        prune_unnameable_github_people(conn)
        self.assertIsNone(conn.execute(
            "SELECT 1 FROM people WHERE id=?", (pid,)).fetchone())

    def test_a_named_person_at_a_lab_survives_the_prune(self):
        conn = memory_db()
        conn.execute("INSERT INTO labs (name) VALUES ('OpenAI')")
        pid = self._person_with_gh_identity(
            conn, "Katherine Johnson", "@openai",
            "github:kjohnson\nname: Katherine Johnson\ncompany: @openai\n")
        prune_unnameable_github_people(conn)
        self.assertIsNotNone(conn.execute(
            "SELECT 1 FROM people WHERE id=?", (pid,)).fetchone())

    def test_a_pruned_person_keeps_their_pending_queue_row(self):
        """The queue keys on the name, not the person id, so a real name later
        re-admits them."""
        conn = memory_db()
        conn.execute("INSERT INTO labs (name) VALUES ('OpenAI')")
        # a valid-named but identity-less person, like a retract orphan
        cur = conn.execute(
            "INSERT INTO people (canonical_name, seniority_tier, discovered_via,"
            " first_seen_at) VALUES ('Real Name','ic','seed','t')")
        pid = cur.lastrowid
        from fli import storage
        sid = storage.upsert_source(conn, "arxiv", "s", "u", purpose="register")
        doc_id, _ = storage.store_document(conn, sid, "arxiv", "u", "Real Name", None)
        ev = storage.insert_evidence(conn, doc_id, "{}", "Real Name", "exact", 1.0)
        conn.execute("INSERT INTO person_candidates (name, discovered_via,"
                     " seed_person_ids, evidence_id, status, created_at)"
                     " VALUES ('Real Name','coauthor_expansion','[]',?, 'pending','t')",
                     (ev,))
        conn.commit()
        prune_unnameable_github_people(conn)  # identity-less -> pruned
        self.assertIsNone(conn.execute(
            "SELECT 1 FROM people WHERE id=?", (pid,)).fetchone())
        self.assertIsNotNone(conn.execute(
            "SELECT 1 FROM person_candidates WHERE name='Real Name'").fetchone())


class TestRepoDiscovery(unittest.TestCase):
    def test_repos_come_from_the_feeds_we_already_ingest(self):
        """The repos mined for people are exactly the repos already tracked —
        no second list to drift out of sync."""
        conn = memory_db()
        conn.execute("INSERT INTO labs (name, is_public_company) VALUES ('Anthropic',0)")
        conn.execute(
            "INSERT INTO sources (source_type,name,url,lab_id) VALUES"
            " ('github','a','https://github.com/anthropics/anthropic-sdk-python"
            "/releases.atom',1)")
        conn.commit()
        self.assertEqual(repo_slugs(conn),
                         [("anthropics/anthropic-sdk-python", "Anthropic")])

    def test_a_source_with_no_lab_is_skipped(self):
        """A repo we cannot attribute cannot tell us who works where."""
        conn = memory_db()
        conn.execute("INSERT INTO sources (source_type,name,url) VALUES"
                     " ('github','x','https://github.com/foo/bar/releases.atom')")
        conn.commit()
        self.assertEqual(repo_slugs(conn), [])


if __name__ == "__main__":
    unittest.main()
