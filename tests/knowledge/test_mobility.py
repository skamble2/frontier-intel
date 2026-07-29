"""fli.knowledge.register.mobility — affiliation history -> personnel events.

The schema has always promised that a person with rows at two labs inside a
window is a mobility event; these tests hold the code that keeps the promise
to its rules: strict succession, the pairing window, idempotency, and the
locator tag that keeps synthesized events separable from extracted ones.
"""
import contextlib
import io
import json

from fli import storage
from fli.knowledge.register.mobility import detect_mobility_events
from tests.helpers import DBTestCase


class MobilityCase(DBTestCase):
    """Scaffolding: two labs, one person, dated page-verbatim affiliations."""

    def setUp(self):
        super().setUp()
        self.conn.execute("INSERT INTO labs (id, name) VALUES (1,'LabA'),(2,'LabB')")
        self.conn.execute(
            "INSERT INTO people (id, canonical_name, discovered_via)"
            " VALUES (1, 'Alice Research', 'seed')")

    def affiliate(self, person_id: int, lab_id: int, observed_at: str,
                  basis: str = "page_verbatim") -> int:
        """One dated observation, evidenced by a lab page that really carries
        the person's name — so C2-style re-verification would pass."""
        url = f"http://lab{lab_id}.test/team/{observed_at}"
        sid = storage.upsert_source(self.conn, "blog", f"lab{lab_id} page {observed_at}",
                                    url, purpose="register")
        doc, _ = storage.store_document(self.conn, sid, "blog", url,
                                        "Team: Alice Research works here.", None)
        ev = storage.insert_evidence(
            self.conn, doc, json.dumps({"kind": "page_text", "match": "Alice Research"}),
            "Alice Research", "exact", 1.0)
        self.conn.execute(
            "INSERT INTO affiliations (person_id, lab_id, basis, observed_at, evidence_id)"
            " VALUES (?,?,?,?,?)", (person_id, lab_id, basis, observed_at, ev))
        return ev

    def detect(self, **kw) -> dict:
        with contextlib.redirect_stdout(io.StringIO()):
            return detect_mobility_events(self.conn, **kw)

    def personnel_events(self):
        return self.conn.execute(
            "SELECT i.*, e.locator FROM insights i JOIN evidence e"
            " ON e.id=i.evidence_id WHERE i.event_type='personnel'").fetchall()


class TestMoveDetection(MobilityCase):

    def test_clean_move_emits_one_event_with_mover_roles(self):
        self.affiliate(1, 1, "2026-06-01T00:00:00Z")
        self.affiliate(1, 2, "2026-07-01T00:00:00Z")
        out = self.detect(window_days=90)
        self.assertEqual(out["created"], 1)

        events = self.personnel_events()
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev["attributed_person_id"], 1)
        self.assertEqual(ev["attributed_lab_id"], 2)
        self.assertIn("mobility_synthesis", ev["locator"])
        self.assertIn("LabA", ev["claim"])
        self.assertIn("LabB", ev["claim"])

        roles = {r["role"]: r["lab_id"] for r in self.conn.execute(
            "SELECT role, lab_id FROM event_entities WHERE event_id=?"
            " AND role IN ('mover_from','mover_to')", (ev["id"],))}
        self.assertEqual(roles, {"mover_from": 1, "mover_to": 2})

    def test_event_is_dated_by_arrival_observation(self):
        """The digest's undated rule must not hide a move: the locator carries
        the arrival date, which is the event's honest date."""
        self.affiliate(1, 1, "2026-06-01T00:00:00Z")
        self.affiliate(1, 2, "2026-07-01T00:00:00Z")
        self.detect(window_days=90)
        loc = json.loads(self.personnel_events()[0]["locator"])
        self.assertEqual(loc["to_first_observed"], "2026-07-01T00:00:00Z")

    def test_idempotent_across_reruns(self):
        self.affiliate(1, 1, "2026-06-01T00:00:00Z")
        self.affiliate(1, 2, "2026-07-01T00:00:00Z")
        self.assertEqual(self.detect(window_days=90)["created"], 1)
        second = self.detect(window_days=90)
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["skipped_existing"], 1)
        self.assertEqual(len(self.personnel_events()), 1)

    def test_same_lab_reobservation_is_not_a_move(self):
        self.affiliate(1, 1, "2026-06-01T00:00:00Z")
        self.affiliate(1, 1, "2026-07-01T00:00:00Z")
        self.assertEqual(self.detect(window_days=90)["created"], 0)
        self.assertEqual(self.personnel_events(), [])

    def test_overlapping_stints_are_dual_affiliation_not_a_move(self):
        """Seen at B while still being re-observed at A: concurrent, no event."""
        self.affiliate(1, 1, "2026-06-01T00:00:00Z")
        self.affiliate(1, 2, "2026-06-10T00:00:00Z")
        self.affiliate(1, 1, "2026-06-20T00:00:00Z")   # still at A after B began
        out = self.detect(window_days=90)
        self.assertEqual(out["created"], 0)
        self.assertEqual(out["skipped_overlap"], 1)

    def test_pairing_window_rejects_stale_pairs_not_fresh_ones(self):
        self.affiliate(1, 1, "2025-01-01T00:00:00Z")
        self.affiliate(1, 2, "2026-07-01T00:00:00Z")   # 546 days later
        out = self.detect(window_days=90)
        self.assertEqual(out["created"], 0)
        self.assertEqual(out["skipped_window"], 1)
        # the same gap is a move under a wide-enough window: bound, not delay
        self.assertEqual(self.detect(window_days=600)["created"], 1)

    def test_coauthor_inference_never_pairs_into_a_move(self):
        """An inferred link is a mention, not presence."""
        self.affiliate(1, 1, "2026-06-01T00:00:00Z", basis="coauthor_inference")
        self.affiliate(1, 2, "2026-07-01T00:00:00Z")
        self.assertEqual(self.detect(window_days=90)["created"], 0)

    def test_entities_are_source_inferred_so_scoring_downweights(self):
        self.affiliate(1, 1, "2026-06-01T00:00:00Z")
        self.affiliate(1, 2, "2026-07-01T00:00:00Z")
        self.detect(window_days=90)
        bases = [r["basis"] for r in self.conn.execute(
            "SELECT basis FROM event_entities WHERE role IN ('mover_from','mover_to')")]
        self.assertEqual(bases, ["source_inferred", "source_inferred"])
