"""fli.delivery.alerts — the push path.

An alert interrupts, so the tests are about restraint: what must NOT fire, and
what must not fire twice.
"""
import datetime as dt
import unittest

from fli.delivery import alerts
from tests.helpers import memory_db


def _event(conn, eid, days_ago=2, claim=None):
    pub = (dt.date.today() - dt.timedelta(days=days_ago)).isoformat()
    conn.execute("INSERT INTO raw_documents (id,source_id,source_type,url,"
                 "content_hash,raw_content,retrieved_at,published_at)"
                 " VALUES (?,1,'blog',?,?,'x','t',?)",
                 (eid, f"https://lab.example/{eid}", f"h{eid}", pub))
    conn.execute("INSERT INTO evidence (id,document_id,locator,verbatim_content,"
                 "verification) VALUES (?,?,'{}','q','exact')", (eid, eid))
    conn.execute("INSERT INTO insights (id,evidence_id,event_type,claim,score,"
                 "created_at) VALUES (?,?,'release',?,1.0,'t')",
                 (eid, eid, claim or f"claim {eid}"))


def _edge(conn, eid, direction, channel="competitive_displacement",
          ticker="HNGE"):
    conn.execute("INSERT INTO event_positions (event_id,ticker,direction,"
                 "channel,rationale,evidence_id,policy_version,created_at)"
                 " VALUES (?,?,?,?,'r',?,3,'t')",
                 (eid, ticker, direction, channel, eid))


def _reading(conn, eid, persona, direction, confidence):
    conn.execute("INSERT INTO hypotheses (insight_id,persona,hypothesis,"
                 "direction,confidence,time_horizon,reasoning)"
                 " VALUES (?,?,'h',?,?,'now','r')",
                 (eid, persona, direction, confidence))


class TestWhatDoesNotFire(unittest.TestCase):
    def setUp(self):
        self.conn = memory_db()
        self.conn.execute("INSERT INTO sources (source_type,name,url)"
                          " VALUES ('blog','s','u')")

    def _rules(self, days=7):
        return sorted(a["rule"] for a in alerts.candidates(self.conn, days))

    def test_an_unclear_edge_is_not_pushed(self):
        """57 of 59 real edges are `unclear`. Pushing exposure-without-a-
        mechanism would make the channel worthless within a week."""
        _event(self.conn, 1)
        _edge(self.conn, 1, "unclear", channel="energy_datacenter")
        self.assertEqual(self._rules(), [])

    def test_a_low_confidence_reading_is_not_pushed(self):
        """Low confidence is the reader saying the evidence is thin. That
        belongs in the digest, not in an interruption."""
        _event(self.conn, 1)
        _reading(self.conn, 1, "investment", "threat", "low")
        self.assertEqual(self._rules(), [])

    def test_a_monitor_or_investigate_reading_is_not_pushed(self):
        _event(self.conn, 1)
        _reading(self.conn, 1, "ai_team", "investigate", "high")
        self.assertEqual(self._rules(), [])

    def test_an_old_event_is_not_pushed(self):
        """A signed reading on a 2024 post is a backfill artifact, not news.
        "Old" is measured against the newest document the corpus holds (the
        slate anchor), so the corpus must contain a fresher document for the
        2024 post to be old relative to."""
        _event(self.conn, 1, days_ago=400)
        _event(self.conn, 2, days_ago=1)
        _edge(self.conn, 1, "threat")
        _reading(self.conn, 1, "investment", "threat", "high")
        self.assertEqual(self._rules(), [])

    def test_a_high_score_alone_is_not_a_reason(self):
        """THE RULE THIS EXISTS FOR. The obvious trigger — a top-decile score —
        is wrong: the one event the system calls a threat to a holding scores
        below the median, because the rubric rewards specificity, not portfolio
        consequence."""
        _event(self.conn, 1)
        self.conn.execute("UPDATE insights SET score = 99.0 WHERE id = 1")
        self.assertEqual(self._rules(), [])


class TestWhatFires(unittest.TestCase):
    def setUp(self):
        self.conn = memory_db()
        self.conn.execute("INSERT INTO sources (source_type,name,url)"
                          " VALUES ('blog','s','u')")
        _event(self.conn, 1)
        _edge(self.conn, 1, "threat", ticker="HNGE")
        _edge(self.conn, 1, "threat", ticker="OSCR")
        _reading(self.conn, 1, "investment", "threat", "medium")
        self.conn.commit()

    def test_a_signed_edge_and_a_signed_reading_are_separate_rules(self):
        """They are independent evidence: one is deterministic, one is read.
        Collapsing them would hide which of the two actually fired."""
        self.assertEqual(sorted(a["rule"] for a in
                                alerts.candidates(self.conn, 7)),
                         ["signed_position", "signed_reading"])

    def test_one_event_touching_two_holdings_is_one_alert(self):
        """Two interruptions carrying one fact. The UNIQUE key would have
        recorded only the first, so the send would have been noisier than the
        record."""
        pos = [a for a in alerts.candidates(self.conn, 7)
               if a["rule"] == "signed_position"]
        self.assertEqual(len(pos), 1)
        self.assertIn("HNGE", pos[0]["reason"])
        self.assertIn("OSCR", pos[0]["reason"])

    def test_an_alert_fires_exactly_once(self):
        first = alerts.run(self.conn, days=7, sink="null", verbose=False)
        second = alerts.run(self.conn, days=7, sink="null", verbose=False)
        self.assertEqual(first["fired"], 2)
        self.assertEqual(second["fired"], 0)
        self.assertEqual(second["suppressed"], 2)

    def test_a_dry_run_records_nothing(self):
        alerts.run(self.conn, days=7, dry_run=True, verbose=False)
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM alerts").fetchone()[0], 0)

    def test_the_delivery_channel_is_recorded(self):
        """`stdout` is a real sink. Which one ran has to be on the row, or a
        failed webhook looks exactly like a suppressed duplicate."""
        alerts.run(self.conn, days=7, sink="null", verbose=False)
        vias = {r[0] for r in self.conn.execute(
            "SELECT delivered_via FROM alerts")}
        self.assertEqual(vias, {"null"})


class TestSlackSink(unittest.TestCase):
    def test_refuses_to_run_without_a_webhook_url(self):
        """A silently dropped alert is worse than a crash: the row would say
        delivered when nothing was."""
        import os
        old = os.environ.pop("SLACK_WEBHOOK_URL", None)
        try:
            with self.assertRaises(RuntimeError):
                alerts._sink_slack({"rule": "signed_reading",
                                    "persona": "investment", "event_id": 1,
                                    "reason": "r", "claim": "c", "url": "u"})
        finally:
            if old is not None:
                os.environ["SLACK_WEBHOOK_URL"] = old

    def test_posts_the_alert_as_a_json_text_payload(self):
        """Assert on the request actually built — URL, method, body — without
        touching the network."""
        import json
        import os
        from unittest import mock
        seen = {}

        class _Resp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_urlopen(req, timeout=None):
            seen["url"] = req.full_url
            seen["method"] = req.get_method()
            seen["body"] = json.loads(req.data.decode("utf-8"))
            return _Resp()

        with mock.patch.dict(os.environ,
                             {"SLACK_WEBHOOK_URL": "https://hooks.example/x"}):
            with mock.patch("urllib.request.urlopen", fake_urlopen):
                alerts._sink_slack({"rule": "signed_position",
                                    "persona": "investment", "event_id": 408,
                                    "reason": "HNGE threat via product_overlap",
                                    "claim": "OpenAI launches Health",
                                    "url": "https://openai.com/health"})
        self.assertEqual(seen["url"], "https://hooks.example/x")
        self.assertEqual(seen["method"], "POST")
        self.assertIn("HNGE threat", seen["body"]["text"])
        self.assertIn("event 408", seen["body"]["text"])


if __name__ == "__main__":
    unittest.main()
