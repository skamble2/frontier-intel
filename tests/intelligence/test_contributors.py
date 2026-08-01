"""Contributor scoring inherits, never invents.

The module's whole defense is "zero new parameters: percentile of a validated
event score × the recency decay the features already use". These tests pin
that arithmetic, the dedup across the two attribution channels, and the
view-like recompute (stale people vanish; the table never outlives the
bake-off run it came from).
"""
import contextlib
import io
import json
import unittest
from datetime import datetime, timedelta, timezone

from fli.intelligence.contributors import compute, tier_mix, top
from tests.helpers import DBTestCase


def _now_iso(days_ago=0):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


class TestContributorScores(DBTestCase):
    def setUp(self):
        super().setUp()
        c = self.conn
        c.execute("INSERT INTO labs (id,name) VALUES (1,'LabA')")
        for pid, name, tier in [(1, "Top Author", "research_lead"),
                                (2, "Weak Author", "ic"),
                                (3, "Both Channels", "founder")]:
            c.execute("INSERT INTO people (id,canonical_name,seniority_tier)"
                      " VALUES (?,?,?)", (pid, name, tier))
        c.execute("INSERT INTO sources (id,source_type,name,url,lab_id)"
                  " VALUES (1,'blog','s','u',1)")
        c.execute("INSERT INTO raw_documents (id,source_id,source_type,url,"
                  "content_hash,raw_content,retrieved_at,published_at)"
                  " VALUES (1,1,'blog','u','h','x','t',?)", (_now_iso(0),))
        c.execute("INSERT INTO evidence (id,document_id,locator,"
                  "verbatim_content,verification)"
                  " VALUES (1,1,'{}','q','exact')")
        # four scored events, dense ranks 1..4 under the winning model
        for eid in (1, 2, 3, 4):
            c.execute("INSERT INTO insights (id,evidence_id,event_type,claim,"
                      "created_at) VALUES (?,1,'research',?,'t')",
                      (eid, f"claim {eid}"))
            c.execute("INSERT INTO event_scores (event_id,model,score,rank,"
                      "components,policy_version,created_at)"
                      " VALUES (?,'investment:logistic',?,?,?,0,'t')",
                      (eid, 1.0 / eid, eid, '{"winner": true}'))
        # a non-winner model that must be invisible to the aggregation
        c.execute("INSERT INTO event_scores (event_id,model,score,rank,"
                  "components,policy_version,created_at)"
                  " VALUES (1,'investment:gbm',9.9,1,NULL,0,'t')")
        # person 1 -> rank-1 event; person 2 -> rank-4 event
        c.execute("INSERT INTO event_entities (event_id,entity_kind,person_id,"
                  "role,evidence_id) VALUES (1,'person',1,'author',1)")
        c.execute("INSERT INTO event_entities (event_id,entity_kind,person_id,"
                  "role,evidence_id) VALUES (4,'person',2,'author',1)")
        # person 3 -> event 2 via BOTH channels (entity row + attributed cache)
        c.execute("INSERT INTO event_entities (event_id,entity_kind,person_id,"
                  "role,evidence_id) VALUES (2,'person',3,'releaser',1)")
        c.execute("UPDATE insights SET attributed_person_id=3 WHERE id=2")
        c.commit()

    def test_score_is_percentile_times_recency_with_no_other_terms(self):
        compute(self.conn, "investment")
        rows = {r["canonical_name"]: r for r in top(self.conn, "investment", 10)}
        # published today -> recency ~1.0; rank 1 of 4 -> percentile 1.0
        self.assertAlmostEqual(rows["Top Author"]["score"], 1.0, places=2)
        # rank 4 of 4 -> percentile 0.25
        self.assertAlmostEqual(rows["Weak Author"]["score"], 0.25, places=2)
        self.assertGreater(rows["Top Author"]["score"],
                           rows["Weak Author"]["score"])

    def test_dual_channel_link_counts_the_event_once(self):
        compute(self.conn, "investment")
        both = next(r for r in top(self.conn, "investment", 10)
                    if r["canonical_name"] == "Both Channels")
        self.assertEqual(both["n_events"], 1)
        # rank 2 of 4 -> percentile 0.75, once — not 1.5 from double counting
        self.assertAlmostEqual(both["score"], 0.75, places=2)

    def test_components_decompose_into_the_events_behind_the_number(self):
        compute(self.conn, "investment")
        r = top(self.conn, "investment", 1)[0]
        evs = json.loads(r["components"])["top_events"]
        self.assertEqual(evs[0]["event_id"], 1)
        self.assertAlmostEqual(evs[0]["percentile"], 1.0)

    def test_recompute_is_a_view_stale_people_vanish(self):
        compute(self.conn, "investment")
        self.conn.execute("DELETE FROM event_entities WHERE person_id=1")
        compute(self.conn, "investment")
        names = [r["canonical_name"] for r in top(self.conn, "investment", 10)]
        self.assertNotIn("Top Author", names)

    def test_missing_bakeoff_refuses_instead_of_scoring_from_nothing(self):
        with self.assertRaises(SystemExit):
            compute(self.conn, "ai_team")

    def test_tier_mix_names_untiered_rather_than_dropping_it(self):
        self.conn.execute("UPDATE people SET seniority_tier=NULL WHERE id=2")
        compute(self.conn, "investment")
        with contextlib.redirect_stdout(io.StringIO()):
            mix = tier_mix(top(self.conn, "investment", 10))
        self.assertIn("untiered", mix)


if __name__ == "__main__":
    unittest.main()
