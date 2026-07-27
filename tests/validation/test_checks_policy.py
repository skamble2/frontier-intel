"""fli.validation.checks — C17, policy attribution of scores.

A score that cannot be traced to an editorial policy version is not explicable,
and the policy is the one part of this system the engineer does not own.
"""
import sqlite3
import unittest
from pathlib import Path

from fli import storage
from fli.core.policy import parse_policy
from fli.validation.checks import policy_attribution_failures

POLICY = parse_policy({
    "version": 3,
    "owner": "BIT PM — unassigned",
    "effective_from": "2026-07-25",
    "source": "research/facts.md",
    "channels": {"compute_memory": ["gpu"]},
    "event_type_prior": {"release": 3, "research": 2},   # note: no 'benchmark'
    "slate_k": 5,
    "hand_weights": {"recency": 1.0},
})


class TestC17(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(Path(storage.SCHEMA_PATH).read_text())
        self.sid = storage.upsert_source(self.conn, "blog", "s", "http://s")
        self._n = 0

    def tearDown(self):
        self.conn.close()

    def _scored(self, policy_version, event_type="release"):
        self._n += 1
        claim = f"a claim {self._n}"
        doc, _ = storage.store_document(self.conn, self.sid, "blog",
                                        f"http://s/{self._n}", claim + " body", None)
        ev = storage.insert_evidence(self.conn, doc, "{}", claim, "exact", 1.0)
        eid = storage.insert_insight(self.conn, ev, event_type, claim)
        self.conn.execute(
            "INSERT INTO event_scores (event_id, model, score, rank,"
            " policy_version, created_at) VALUES (?,?,?,?,?,?)",
            (eid, "logistic", 1.0, 1, policy_version, storage.now_utc()))

    def test_unattributable_scores_are_caught(self):
        cases = [
            # (name, policy_version on the row, event type, substring expected)
            ("stale policy version", 1, "release", "v1"),
            # rows written before policy.yml existed carry the migration default
            ("pre-policy default 0", 0, "release", "v0"),
            # 'benchmark' is a legal schema value but unranked in this policy,
            # so it would silently receive the lowest prior
            ("event type not ranked", 3, "benchmark", "benchmark"),
        ]
        for name, version, etype, expected in cases:
            with self.subTest(name):
                self.setUp()
                self._scored(version, etype)
                failures = policy_attribution_failures(self.conn, POLICY)
                self.assertEqual(1, len(failures), failures)
                self.assertIn(expected, failures[0])

    def test_current_policy_and_ranked_types_pass(self):
        self._scored(3, "release")
        self._scored(3, "research")
        self.assertEqual([], policy_attribution_failures(self.conn, POLICY))

    def test_passes_vacuously_when_nothing_is_scored(self):
        """The trap worth knowing: a green C17 is NOT evidence the bake-off ran.
        C15 and C16 share this property."""
        self.assertEqual([], policy_attribution_failures(self.conn, POLICY))
        self.assertEqual(0, self.conn.execute(
            "SELECT count(*) FROM event_scores").fetchone()[0])


class TestSchemaMigration(unittest.TestCase):
    """`CREATE TABLE IF NOT EXISTS` cannot add a column to an existing table, so
    a schema change would otherwise strand every database already on disk."""

    def test_column_is_added_and_adding_twice_is_safe(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE event_scores (id INTEGER PRIMARY KEY,"
                     " event_id INTEGER, model TEXT, score REAL, rank INTEGER,"
                     " components TEXT, created_at TIMESTAMP)")
        conn.commit()
        storage.init_db(conn)
        storage.init_db(conn)          # must not raise "duplicate column name"
        cols = {r[1] for r in conn.execute("PRAGMA table_info(event_scores)")}
        self.assertIn("policy_version", cols)


if __name__ == "__main__":
    unittest.main()
