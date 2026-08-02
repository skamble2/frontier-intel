"""fli.intelligence.scoring / features - LAYER 3 scoring units."""
import math
import unittest


class TestScoringUnits(unittest.TestCase):
    """Pure scoring helpers, one table of cases per helper."""

    def test_scoring_helpers(self):
        with self.subTest("jaccard"):
            from fli.intelligence.clustering import jaccard
            self.assertEqual(jaccard(frozenset("ab"), frozenset("ab")), 1.0)
            self.assertEqual(jaccard(frozenset("ab"), frozenset("cd")), 0.0)
            self.assertAlmostEqual(jaccard(frozenset({"a", "b"}), frozenset({"a", "c"})), 1 / 3)

        with self.subTest("specificity"):
            from fli.intelligence.features import _specificity
            self.assertEqual(_specificity("frontier intelligence"), 0.0)
            self.assertEqual(_specificity("$5 per 2 million tokens (10%)"), 5.0)  # 5,2,10 + $ + %

        with self.subTest("recency is neutral on a null date"):
            from datetime import datetime, timezone
            from fli.intelligence.features import _recency, NEUTRAL_RECENCY
            self.assertEqual(_recency(None, datetime.now(timezone.utc)), NEUTRAL_RECENCY)

        with self.subTest("pairwise accuracy"):
            import numpy as np
            from fli.intelligence.scoring import _pairwise_accuracy
            scores = np.array([2.0, 1.0])           # event 10 outranks event 20
            row = {10: 0, 20: 1}
            self.assertEqual(_pairwise_accuracy([(10, 20, "a")], scores, row), 1.0)
            self.assertEqual(_pairwise_accuracy([(10, 20, "b")], scores, row), 0.0)
            self.assertTrue(math.isnan(_pairwise_accuracy([(10, 20, "tie")], scores, row)))


class TestSlateFilter(unittest.TestCase):
    """What a READER is shown, which is a different question from what scores
    highest. Three tests, one per rule that depends on the slate so far — the
    per-event rules (window, undated) are plain comparisons and not worth a test.
    """

    # Deliberately unbalanced: "released" appears everywhere so it must never
    # bind two items together; "3.6" appears twice so it must.
    CORPUS = ([f"Google DeepMind released model number {i}" for i in range(20)]
              + ["Gemini 3.6 Flash cuts output tokens by 17%",
                 "Google DeepMind is rolling out Gemini 3.6 Flash"])

    def _policy(self, **over):
        from fli.core.policy import Policy
        base = dict(version=1, owner="test", effective_from="2026-01-01",
                    source="test", channels={"c": ("x",)}, event_type_prior={},
                    slate_k=5, hand_weights={}, window_days=3650,
                    max_per_lab=2, story_rare_df=0.10, story_days=7)
        return Policy(**{**base, **over})

    @staticmethod
    def _row(i, lab, claim, day="2026-07-21T00:00:00+00:00", cluster=None):
        return {"id": i, "lab": lab, "claim": claim, "published_at": day,
                "cluster_id": cluster if cluster is not None else i}

    def test_story_rule_uses_rare_tokens_not_common_ones(self):
        """The Gemini case: two claims about one launch, too dissimilar to
        cluster (measured Jaccard 0.125 against a 0.4 threshold), caught here
        because they share the version token. The control matters as much — a
        boilerplate word shared by the whole corpus must NOT merge two events."""
        from fli.intelligence.scoring import SlateFilter
        f = SlateFilter(self._policy(), self.CORPUS)
        self.assertTrue(f.accept(self._row(1, "DeepMind",
                                           "Gemini 3.6 Flash cuts output tokens by 17%")))
        self.assertFalse(f.accept(self._row(2, "DeepMind",
                                            "Google DeepMind is rolling out Gemini 3.6 Flash")))
        self.assertEqual(f.dropped["same_story"], 1)
        # same lab, same day, only the ubiquitous word in common -> distinct news
        self.assertTrue(f.accept(self._row(3, "DeepMind",
                                           "Google DeepMind released model number 4")))

    def test_lab_cap_bounds_one_lab_and_leaves_others_room(self):
        from fli.intelligence.scoring import SlateFilter
        f = SlateFilter(self._policy(story_rare_df=0.0), self.CORPUS)
        taken = [f.accept(self._row(i, "DeepMind", f"unrelated claim alpha {i}"))
                 for i in range(1, 5)]
        self.assertEqual(taken, [True, True, False, False])
        self.assertEqual(f.dropped["lab_cap"], 2)
        self.assertTrue(f.accept(self._row(9, "Mistral", "unrelated claim beta")))

    def test_unattributed_events_are_never_story_merged(self):
        """`lab` is the anchor of the story rule. With no lab there is no anchor,
        and merging on text alone would suppress unrelated events that happen to
        share a rare word — so the rule abstains rather than guesses."""
        from fli.intelligence.scoring import SlateFilter
        f = SlateFilter(self._policy(max_per_lab=0), self.CORPUS)
        claim = "Gemini 3.6 Flash cuts output tokens by 17%"
        self.assertTrue(f.accept(self._row(1, "(unattributed)", claim)))
        self.assertTrue(f.accept(self._row(2, "(unattributed)", claim)))
        self.assertEqual(f.dropped["same_story"], 0)

    def test_mechanism_gate_drops_quotes_without_a_channel(self):
        """The f16 failure class: a vendor case study scores well (official
        source, high specificity) but names no transmission mechanism, so an
        investment slate must not carry it. Gate ON = quotes the channel
        classifier POSITIVELY verdicted 'none' are out; an unclassified quote
        passes, because a missing cache entry is not evidence about the event.
        Gate OFF (None) = the rule does not exist, which is every other
        persona."""
        from fli.intelligence.scoring import SlateFilter
        no_mech = {"Acme Corp ships 40% faster with GPT"}
        f = SlateFilter(self._policy(), self.CORPUS, no_mech_quotes=no_mech)
        good = {**self._row(1, "OpenAI", "OpenAI contracts 900MW"),
                "quote": "900 megawatts contracted in Abilene"}
        vendor = {**self._row(2, "OpenAI", "Acme Corp ships 40% faster with GPT"),
                  "quote": "Acme Corp ships 40% faster with GPT"}
        unseen = {**self._row(3, "OpenAI", "a quote the classifier never saw"),
                  "quote": "brand new quote"}
        self.assertTrue(f.accept(good))
        self.assertFalse(f.accept(vendor))
        self.assertTrue(f.accept(unseen))
        self.assertEqual(f.dropped["no_mechanism"], 1)
        off = SlateFilter(self._policy(), self.CORPUS, no_mech_quotes=None)
        self.assertTrue(off.accept(dict(vendor)))

    def test_not_entailed_insights_never_render_for_any_persona(self):
        """An insight whose claim its own verified quote does not support
        (faithfulness check f15) is out before any other rule runs — a slate
        that cites the quote as support for the claim would be lying."""
        from fli.intelligence.scoring import SlateFilter
        f = SlateFilter(self._policy(), self.CORPUS, not_entailed={7})
        self.assertFalse(f.accept(self._row(7, "OpenAI", "an overclaimed thing")))
        self.assertEqual(f.dropped["not_entailed"], 1)
        self.assertTrue(f.accept(self._row(8, "OpenAI", "a faithful thing")))

    def test_thin_quotes_are_dropped_except_synthesized_moves(self):
        """The 2026-08-02 failure class: GitHub release feeds serve one-line
        changelog fragments ("support mcp sdk v2 alongside v1") whose quotes sit
        below the extractor's own 10-60-word contract, and one topped the
        engineering digest. Gate ON = a sub-floor quote is out; a synthesized
        mobility event is exempt, because its evidence is a name on a lab page,
        not an extracted quote. Gate at 0 = the rule does not exist."""
        from fli.intelligence.scoring import SlateFilter
        f = SlateFilter(self._policy(), self.CORPUS, min_quote_words=10)
        changelog = {**self._row(1, "Anthropic", "SDK adds MCP v2 support"),
                     "quote": "support mcp sdk v2 alongside v1"}
        full = {**self._row(2, "OpenAI", "OpenAI contracts 900MW in Abilene"),
                "quote": "we have contracted a further nine hundred megawatts "
                         "of capacity at the Abilene site through 2028"}
        move = {**self._row(3, "Mistral", "Alice moved from LabA to LabB"),
                "quote": "Alice Research", "synth": 1}
        self.assertFalse(f.accept(changelog))
        self.assertTrue(f.accept(full))
        self.assertTrue(f.accept(move))
        self.assertEqual(f.dropped["thin_quote"], 1)
        off = SlateFilter(self._policy(), self.CORPUS)
        self.assertTrue(off.accept(dict(changelog)))

    def test_no_holding_link_gate_drops_events_the_reading_disowned(self):
        """The other 2026-08-02 failure class: an investment digest item whose
        own persona reading said "this touches no holding" was still delivered
        at the top of the slate. Events in the no-link set (reading unclear,
        no ticker, no exposure edge — computed by top_events) are out; events
        without a reading pass, because a missing reading is not evidence."""
        from fli.intelligence.scoring import SlateFilter
        f = SlateFilter(self._policy(), self.CORPUS, no_holding_link={4})
        self.assertFalse(f.accept(self._row(4, "Anthropic",
                                            "a partnership touching no holding")))
        self.assertTrue(f.accept(self._row(5, "OpenAI", "a health launch")))
        self.assertEqual(f.dropped["no_holding_link"], 1)

    def test_window_is_anchored_not_wall_clocked(self):
        """A committed corpus must render the same slate whenever it is
        cloned. Anchored to the newest document, a 7-day window keeps an
        event 5 days older than that document forever; anchored to the wall
        clock, the same event would silently expire as the demo sat."""
        from datetime import datetime, timedelta, timezone
        from fli.intelligence.scoring import SlateFilter
        anchor = datetime(2026, 7, 28, tzinfo=timezone.utc)
        f = SlateFilter(self._policy(window_days=7), self.CORPUS,
                        anchor=anchor)
        self.assertTrue(f.accept(self._row(1, "OpenAI", "inside the window",
                                           day="2026-07-23")))
        self.assertFalse(f.accept(self._row(2, "Meta", "before the window",
                                            day="2026-07-19")))
        self.assertEqual(f.dropped["outside_window"], 1)
        # no anchor -> wall clock, the live-feed behaviour
        g = SlateFilter(self._policy(window_days=7), self.CORPUS)
        fresh = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.assertTrue(g.accept(self._row(3, "OpenAI", "fresh", day=fresh)))
        self.assertFalse(g.accept(self._row(4, "Meta", "stale",
                                            day="2026-07-19")))

    def test_slate_anchor_is_the_newest_document(self):
        from fli.intelligence.scoring import slate_anchor
        from tests.helpers import memory_db
        conn = memory_db()
        conn.execute("INSERT INTO sources (source_type,name,url)"
                     " VALUES ('blog','s','u')")
        for i, pub in enumerate(("2026-07-01", "2026-07-28", None), 1):
            conn.execute(
                "INSERT INTO raw_documents (id,source_id,source_type,url,"
                "content_hash,raw_content,retrieved_at,published_at)"
                " VALUES (?,1,'blog',?,?,'x','t',?)",
                (i, f"u{i}", f"h{i}", pub))
        self.assertEqual(slate_anchor(conn).date().isoformat(), "2026-07-28")


class TestReaderRank(unittest.TestCase):
    """The persisted rank is the ranking a reviewer opens. Before this class
    existed, plain score order put a two-year-old event at rank 1 of a
    recent-frontier-activity product (the raw technical top-25 was 100%
    outside the 90-day window)."""

    def _db(self):
        import numpy as np
        from tests.helpers import memory_db
        conn = memory_db()
        conn.execute("INSERT INTO sources (source_type,name,url)"
                     " VALUES ('blog','s','u')")
        # anchor doc: newest is 2026-07-28; policy window is 90d
        rows = [
            # id, published_at, cluster, score
            (1, "2024-05-24", None, 9.0),   # ancient, highest score
            (2, "2026-07-20", 7,    5.0),   # in window, cluster 7 primary
            (3, "2026-07-21", 7,    4.0),   # in window, cluster 7 duplicate
            (4, "2026-07-01", None, 1.0),   # in window, low score
            (5, None,         None, 8.0),   # undated
        ]
        for i, pub, cl, _ in rows:
            conn.execute(
                "INSERT INTO raw_documents (id,source_id,source_type,url,"
                "content_hash,raw_content,retrieved_at,published_at)"
                " VALUES (?,1,'blog',?,?,'x','t',?)", (i, f"u{i}", f"h{i}", pub))
            conn.execute(
                "INSERT INTO evidence (id,document_id,locator,verbatim_content,"
                "verification) VALUES (?,?,'{}','q','exact')", (i, i))
            conn.execute(
                "INSERT INTO insights (id,evidence_id,event_type,claim,"
                "cluster_id,created_at) VALUES (?,?,'release',?,?,'t')",
                (i, i, f"claim {i}", cl))
        ids = [r[0] for r in rows]
        scores = np.array([r[3] for r in rows])
        return conn, ids, scores

    def test_in_window_primaries_outrank_duplicates_and_the_archive(self):
        from fli.intelligence.scoring import _reader_rank
        conn, ids, scores = self._db()
        rank = {ids[i]: r for i, r in enumerate(_reader_rank(conn, ids, scores))}
        # band 0 (in-window primaries, score order): 2 then 4
        # band 1 (in-window duplicate): 3 — cluster 7 already represented
        # band 2 (archive + undated, score order): 1 then 5
        self.assertEqual([rank[2], rank[4], rank[3], rank[1], rank[5]],
                         [1, 2, 3, 4, 5])

    def test_scores_are_not_touched_only_their_order_of_presentation(self):
        from fli.intelligence.scoring import _reader_rank
        conn, ids, scores = self._db()
        before = scores.copy()
        _reader_rank(conn, ids, scores)
        self.assertTrue((scores == before).all())
