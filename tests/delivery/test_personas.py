"""fli.delivery.personas — the rendered judgement per audience.

The parser is where a bad reading gets stopped. `reasoning` is NOT NULL in the
schema because it is shown to the reader verbatim, and a direction from the
wrong audience's vocabulary means the model answered a different question than
the one asked.
"""
import unittest

from fli.delivery.personas import _DIRECTIONS, _parse, build_system, parse


class TestParserRejectsWhatAReaderCannotUse(unittest.TestCase):
    GOOD = ('{"hypothesis": "OpenAI entering consumer health erodes HNGE\'s '
            'funnel", "tickers": ["HNGE"], "direction": "threat", '
            '"confidence": "medium", "time_horizon": "quarters", '
            '"reasoning": "The quote states Health is launching to US users '
            'with medical-record connection, which is HNGE\'s entry point."}')

    def test_a_complete_verdict_parses(self):
        v = _parse(self.GOOD, "investment")
        self.assertEqual(v["direction"], "threat")
        self.assertEqual(v["tickers"], ["HNGE"])

    def test_fenced_json_parses(self):
        self.assertIsNotNone(_parse(f"```json\n{self.GOOD}\n```", "investment"))

    def test_missing_reasoning_is_unusable(self):
        """The schema makes reasoning NOT NULL because the reader is shown it.
        A hypothesis with no working is an assertion."""
        bad = self.GOOD.replace('"reasoning": "The quote states Health is '
                                'launching to US users with medical-record '
                                'connection, which is HNGE\'s entry point."',
                                '"reasoning": ""')
        self.assertIsNone(_parse(bad, "investment"))

    def test_missing_hypothesis_is_unusable(self):
        self.assertIsNone(_parse('{"direction":"threat","reasoning":"x"}',
                                 "investment"))

    def test_malformed_json_is_unusable_not_an_exception(self):
        self.assertIsNone(_parse("not json at all", "investment"))


class TestAFailureSaysWhyItFailed(unittest.TestCase):
    """A rejected reply that only reports "unusable" cannot be acted on.

    Two events failed this way on real data. The cause — a reply cut off at
    the token ceiling mid-JSON — was only found by inspecting the payload by
    hand, and the three distinct failure modes below are fixed in different
    places: raise the budget, tighten the prompt, or correct the persona.
    """

    def test_a_truncated_reply_is_named_as_truncation(self):
        v, why = parse('{"hypothesis": "h", "direction": "threat", '
                       '"reasoning": "the quote states', "investment")
        self.assertIsNone(v)
        self.assertIn("TRUNCATED", why)

    def test_genuinely_malformed_json_is_not_blamed_on_truncation(self):
        """A complete but invalid object needs a prompt fix, not more tokens."""
        v, why = parse('{"hypothesis": "h" "direction": "threat"}', "investment")
        self.assertIsNone(v)
        self.assertNotIn("TRUNCATED", why)

    def test_the_wrong_vocabulary_is_named_as_the_wrong_audience(self):
        v, why = parse('{"hypothesis":"h","direction":"adopt","reasoning":"r"}',
                       "investment")
        self.assertIsNone(v)
        self.assertIn("investment", why)

    def test_a_good_reply_carries_no_reason(self):
        self.assertEqual(
            parse(TestParserRejectsWhatAReaderCannotUse.GOOD, "investment")[1],
            "")


class TestPersonasCannotBorrowEachOthersVocabulary(unittest.TestCase):
    """An `adopt` verdict on the investment persona means the model answered
    the engineering question. Silently storing it would put the wrong reading
    in front of a PM."""

    def test_ai_team_direction_is_rejected_for_investment(self):
        bad = ('{"hypothesis":"h","direction":"adopt","reasoning":"r"}')
        self.assertIsNone(_parse(bad, "investment"))

    def test_investment_direction_is_rejected_for_ai_team(self):
        bad = ('{"hypothesis":"h","direction":"tailwind","reasoning":"r"}')
        self.assertIsNone(_parse(bad, "ai_team"))

    def test_the_two_vocabularies_do_not_overlap(self):
        self.assertFalse(_DIRECTIONS["investment"] & _DIRECTIONS["ai_team"])


class TestInvestmentCandidateSelection(unittest.TestCase):
    """The investment persona must see events whose direction is UNRESOLVED.

    An earlier version selected only events the deterministic layer had already
    signed — 1 of 54. Since a demand channel carries no sign by design, that
    excluded every event actually needing a reader: a 10 MW datacenter build,
    Project Camellia, and a model matching a larger one on less memory.
    """

    def setUp(self):
        import datetime

        from tests.helpers import memory_db
        self.conn = memory_db()
        c = self.conn
        recent = (datetime.date.today() - datetime.timedelta(days=3)).isoformat()
        stale = (datetime.date.today() - datetime.timedelta(days=400)).isoformat()
        c.execute("INSERT INTO sources (source_type,name,url) VALUES ('blog','s','u')")
        for did, pub in ((1, recent), (2, stale)):
            c.execute("INSERT INTO raw_documents (id,source_id,source_type,url,"
                      "content_hash,raw_content,retrieved_at,published_at)"
                      " VALUES (?,1,'blog',?,?,'x','t',?)",
                      (did, f"u{did}", f"h{did}", pub))
            c.execute("INSERT INTO evidence (id,document_id,locator,"
                      "verbatim_content,verification) VALUES (?,?,'{}','q','exact')",
                      (did, did))
        # events 1-3 recent, event 4 published over a year ago
        for i, ev in ((1, 1), (2, 1), (3, 1), (4, 2)):
            c.execute("INSERT INTO insights (id,evidence_id,event_type,claim,"
                      "score,created_at) VALUES (?,?,'release',?,?,'t')",
                      (i, ev, f"claim {i}", 10 - i))
        # 1 signed, 2 exposed-with-mechanism but unsigned, 3 no exposure,
        # 4 exposed with a mechanism but far outside the window
        for eid, ch, d in [(1, "competitive_displacement", "threat"),
                           (2, "energy_datacenter", "unclear"),
                           (4, "energy_datacenter", "unclear")]:
            c.execute("INSERT INTO event_positions (event_id,ticker,direction,"
                      "channel,rationale,evidence_id,policy_version,created_at)"
                      " VALUES (?,'IREN',?,?,'r',1,3,'t')", (eid, d, ch))
        c.commit()

    def test_unsigned_but_exposed_events_are_candidates(self):
        from fli.delivery.personas import _candidates
        got = _candidates(self.conn, "investment", k=0)
        self.assertIn(2, got, "an exposed event with no direction yet was skipped")
        self.assertIn(1, got)

    def test_events_with_no_exposure_are_not_pulled_in_by_exposure(self):
        from fli.delivery.personas import _candidates
        self.assertNotIn(3, _candidates(self.conn, "investment", k=0))

    def test_exposure_does_not_reach_past_the_window(self):
        """A digest reports on a period. Position exposure earns an event a
        reading, but not the right to lead this week's report from 2024."""
        from fli.delivery.personas import _candidates
        self.assertNotIn(4, _candidates(self.conn, "investment", k=0))


class TestSystemPromptsDiffer(unittest.TestCase):
    class _Policy:
        positions = ()

    def test_the_engineering_prompt_carries_no_holdings(self):
        """The AI team prompt must contain no fund content at all — otherwise
        the audience split is cosmetic."""
        sys_ai = build_system("ai_team", self._Policy())
        for word in ("HNGE", "ticker", "portfolio manager", "holdings"):
            self.assertNotIn(word.lower(), sys_ai.lower(), word)

    def test_the_investment_prompt_bans_commercial_irrelevance_nowhere(self):
        """Conversely the engineering prompt explicitly rules commercial
        consequence out of scope."""
        sys_ai = build_system("ai_team", self._Policy())
        self.assertIn("irrelevant", sys_ai.lower())

    def test_both_prompts_forbid_going_beyond_the_quote(self):
        for p in ("investment", "ai_team"):
            self.assertIn("only what is in the claim and the quote",
                          build_system(p, self._Policy()).lower())


if __name__ == "__main__":
    unittest.main()
