"""fli.intelligence.clustering - LAYER 3 near-duplicate event clustering."""
import unittest
import sqlite3
from pathlib import Path

from fli import storage


class TestClusterDB(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(Path(storage.SCHEMA_PATH).read_text())
        self.sid = storage.upsert_source(self.conn, "blog", "s", "http://f")

    def _insight(self, claim, event_type):
        body = claim + " " + "x " * 20
        doc, _ = storage.store_document(self.conn, self.sid, "blog",
                                        f"http://d/{claim[:8]}", body, None)
        ev = storage.insert_evidence(self.conn, doc, "{}", claim, "exact", 1.0)
        return storage.insert_insight(self.conn, ev, event_type, claim)

    def test_clusters_merge_near_dup_not_across_type(self):
        from fli.intelligence.clustering import cluster_all
        a = self._insight("OpenAI releases GPT-5.6 today with lower prices", "release")
        b = self._insight("OpenAI releases GPT-5.6 today at lower prices", "release")   # near-dup
        c = self._insight("A totally different benchmark result on math tasks", "benchmark")
        cluster_all(self.conn)
        cid = {i: self.conn.execute("SELECT cluster_id FROM insights WHERE id=?", (i,)).fetchone()[0]
               for i in (a, b, c)}
        self.assertEqual(cid[a], cid[b])       # near-duplicates merge
        self.assertNotEqual(cid[a], cid[c])    # different event_type never merges
        # no cluster spans event types
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM (SELECT cluster_id FROM insights GROUP BY cluster_id"
            " HAVING count(DISTINCT event_type)>1)").fetchone()[0], 0)
