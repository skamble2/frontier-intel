"""fli.knowledge.register - LAYER 2 candidate approval and per-lab balance.

Co-author expansion finds far more candidates for prolific labs, so
approval must cap per lab or the register skews to whoever publishes most.
"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fli import storage
from fli.knowledge.register import approval, reporting
from fli.knowledge.register.approval import valid_candidate_name


class TestCandidateName(unittest.TestCase):
    """Author strings from mega-author paper metadata are frequently junk."""

    def test_name_hygiene(self):
        self.assertTrue(valid_candidate_name("Boaz Barak"))
        for bad, why in [(" OpenAI", "leading space"), (" :", "punctuation only"),
                         ("Xia", "single token"), ("OpenAI", "a lab, not a person"),
                         ("a 1 2", "numeric tokens")]:
            with self.subTest(why):
                self.assertFalse(valid_candidate_name(bad))


class TestDeskew(unittest.TestCase):
    """Per-lab slates surface non-dominant labs; the override file wins."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(Path(storage.SCHEMA_PATH).read_text())
        self.conn.execute("INSERT INTO labs (id, name) VALUES (1,'OpenAI'),(2,'Mistral')")
        self._seed(10, "Owen Openai", 1)
        self._seed(20, "Manon Mistral", 2)
        sid = storage.upsert_source(self.conn, "arxiv", "s", "http://ax", purpose="register")
        self.doc, _ = storage.store_document(self.conn, sid, "arxiv", "http://ax",
                                             "paper", None)
        # Generate slate_k + 2 candidates for the prolific lab, so the cap is
        # exercised at whatever config/policy.yml currently says. Hardcoding 7
        # meant this test broke — and looked like a regression — the moment the
        # owner raised slate_k, which is a policy decision, not a code change.
        from fli.core.policy import load_policy
        self.k = load_policy().slate_k
        self.n_prolific = self.k + 2
        for i in range(self.n_prolific):
            self._cand(f"O{i} Author", 10, 1, self.n_prolific - i)
        for i in range(2):
            self._cand(f"M{i} Author", 20, 2, 2 - i)
        # Point overrides at a temp dir. A test must never write into the
        # project's real config/ — an earlier version did, and left a stray
        # _test_overrides.yml behind in the repo.
        self._tmp = tempfile.TemporaryDirectory()
        self._ovr = Path(self._tmp.name) / "overrides.yml"
        self._saved_path = approval.OVERRIDES_PATH
        approval.OVERRIDES_PATH = self._ovr

    def tearDown(self):
        approval.OVERRIDES_PATH = self._saved_path
        self._tmp.cleanup()

    def _seed(self, pid, name, lab_id):
        self.conn.execute(
            "INSERT INTO people (id,canonical_name,discovered_via,seniority_tier)"
            " VALUES (?,?,?,?)", (pid, name, "seed", "research_lead"))
        sid = storage.upsert_source(self.conn, "blog", "p", f"http://p{pid}",
                                    lab_id=lab_id, channel="official", purpose="register")
        did, _ = storage.store_document(self.conn, sid, "blog", f"http://p{pid}",
                                        name + " bio page", None)
        ev = storage.insert_evidence(self.conn, did, "{}", name, "exact", 1.0)
        self.conn.execute(
            "INSERT INTO affiliations (person_id,lab_id,role,basis,observed_at,evidence_id)"
            " VALUES (?,?,?,'page_verbatim',?,?)",
            (pid, lab_id, None, storage.now_utc(), ev))

    def _cand(self, name, seed_id, seed_lab_id, paper_count):
        ev = storage.insert_evidence(self.conn, self.doc, "{}", name, "structural", None)
        self.conn.execute(
            "INSERT INTO person_candidates (name,discovered_via,paper_count,entry_ids,"
            " seed_person_ids,seed_lab_ids,lab_hint,evidence_id,status,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (name, "coauthor_expansion", paper_count, "[]", json.dumps([seed_id]),
             json.dumps([seed_lab_id]), None, ev, "pending", storage.now_utc()))
        self.conn.commit()

    def test_approval_caps_per_lab_so_a_prolific_lab_cannot_bury_others(self):
        with self.subTest("the slate surfaces both labs"):
            pending = [dict(r) for r in self.conn.execute(
                "SELECT * FROM person_candidates WHERE status='pending'")]
            slate = approval._slate(self.conn, pending)
            # top-k of the prolific lab, plus both Mistral — the small lab is
            # never buried by the big one
            self.assertEqual(self.k + 2, len(slate))
            mistral = {r["id"] for r in self.conn.execute(
                "SELECT id FROM person_candidates WHERE name LIKE 'M%'")}
            self.assertTrue(mistral <= slate)

        with self.subTest("approval honours the cap and records how it happened"):
            approval.auto_approve(self.conn)
            bal = reporting.balance_by_lab(self.conn)
            self.assertEqual(self.k, bal["OpenAI"]["approved"])   # capped at slate_k
            self.assertEqual(2, bal["Mistral"]["approved"])   # surfaced, not buried
            self.assertEqual({"auto_approved"}, {r["discovered_via"] for r in
                             self.conn.execute("SELECT DISTINCT discovered_via FROM people"
                                               " WHERE discovered_via!='seed'")})

    def test_the_override_file_beats_the_rule_in_both_directions(self):
        """The domain owner can always intervene without a code change, and
        without blocking a pipeline run."""
        self._ovr.write_text("approve:\n  - O6 Author\nreject:\n  - O0 Author\n")
        approval.auto_approve(self.conn)
        names = {r["canonical_name"] for r in self.conn.execute(
            "SELECT canonical_name FROM people WHERE discovered_via='auto_approved'")}
        self.assertIn("O6 Author", names)        # forced in despite the lowest rank
        self.assertNotIn("O0 Author", names)     # vetoed despite the top rank
        self.assertEqual("rejected", self.conn.execute(
            "SELECT status FROM person_candidates WHERE name='O0 Author'").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
