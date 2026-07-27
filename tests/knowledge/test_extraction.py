"""fli.knowledge.extraction - LAYER 2 extraction, quote verification, lab
attribution. Runs against a fake LLM: no API key, no network.

Grouped by the property each protects. Four tests here are regressions for
bugs that were found and measured — each names the bug and the number it moved.
"""
import json
import sqlite3
import unittest
from pathlib import Path

from fli import storage
from fli.knowledge.extraction import (extract, readable_text, resolve_lab,
                                      run_stage2, verify_quote)


class TestQuoteVerification(unittest.TestCase):
    """The evidence invariant: a quote is kept only if it re-matches the stored
    document bytes."""

    def test_verify_quote(self):
        cases = [
            ("exact substring", "the model is fast", "model is", ("exact", 1.0)),
            # REGRESSION: extraction had a private curly-quote canonicaliser that
            # checks.py's shared norm() lacked, so C2 failed on rows that were fine.
            ("curly quotes and dashes fold",
             "it’s fast — very", "it's fast - very", ("exact", 1.0)),
            # stage 2 reads visible text; a quote may span an HTML tag boundary
            ("matches across HTML tags", "<p>The model</p><p>is fast</p>",
             "model is fast", ("exact", 1.0)),
            ("absent quote fails", "the model is fast", "the model is slow", None),
        ]
        for name, haystack, needle, expected in cases:
            with self.subTest(name):
                self.assertEqual(expected, verify_quote(haystack, needle))

    def test_readable_text(self):
        raw_plain = "Title\nhttp://x\n\nplain body"
        for name, given, expected in [
                ("strips stored HTML", "<html><body><p>Hi there</p></body></html>", "Hi there"),
                ("passes plaintext through", raw_plain, raw_plain)]:
            with self.subTest(name):
                self.assertEqual(expected, readable_text(given))


class TestLabResolution(unittest.TestCase):
    """Token-subset matching: suffixes like 'AI' must not block a match, but
    ambiguous or unknown names must resolve to None rather than guess."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(Path(storage.SCHEMA_PATH).read_text())
        for i, n in enumerate(["OpenAI", "Meta AI", "Mistral", "Google DeepMind",
                               "DeepSeek", "Qwen"], 1):
            self.conn.execute("INSERT INTO labs (id, name) VALUES (?,?)", (i, n))

    def test_resolve_lab(self):
        cases = [
            ("bare name", "Meta", 2), ("with suffix", "Meta AI", 2),
            ("added suffix", "Mistral AI", 3), ("dropped prefix", "DeepMind", 4),
            # REGRESSION: whitespace-only tokenizing left hyphenated names
            # unmatched, so DeepSeek events fell back to the publishing source.
            # DeepSeek source_inferred attribution: 87.1% -> 0.0%.
            ("hyphenated collective", "DeepSeek-AI", 5),
            ("hyphenated version", "DeepSeek-V4", 5),
            ("hyphenated product", "Meta-Llama", 2),
            ("hyphenated tool", "Qwen-Agent", 6),
            ("untracked lab", "Cohere", None),
            ("no name at all", None, None),
            ("stopword only carries no signal", "AI", None),
        ]
        for name, given, expected in cases:
            with self.subTest(name):
                self.assertEqual(expected, resolve_lab(self.conn, given))


class TestEventEntities(unittest.TestCase):
    """`insights` is the event; `event_entities` carries its 0..N entities.
    Every attribution is evidence-backed and carries a basis."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(Path(storage.SCHEMA_PATH).read_text())
        self.conn.execute("INSERT INTO labs (id, name) VALUES (1, 'OpenAI')")
        self.conn.execute("INSERT INTO people (id, canonical_name) VALUES (2, 'Jane Doe')")
        sid = storage.upsert_source(self.conn, "blog", "s", "http://f")
        self.doc, _ = storage.store_document(
            self.conn, sid, "blog", "http://d", "OpenAI ships a model.", None)
        self.ev = storage.insert_evidence(
            self.conn, self.doc, "{}", "OpenAI ships a model.", "exact", 1.0)

    def _entities(self, eid):
        return self.conn.execute(
            "SELECT * FROM event_entities WHERE event_id=?", (eid,)).fetchall()

    def test_insert_insight_mirrors_attribution(self):
        cases = [
            ("attributed lab creates one row, basis defaults to model_asserted",
             {"attributed_lab_id": 1}, ("lab", 1, None, "subject", "model_asserted")),
            ("explicit basis is threaded through",
             {"attributed_lab_id": 1, "basis": "source_inferred"},
             ("lab", 1, None, "subject", "source_inferred")),
        ]
        for name, kwargs, expected in cases:
            with self.subTest(name):
                eid = storage.insert_insight(self.conn, self.ev, "release", "c", **kwargs)
                (row,) = self._entities(eid)
                self.assertEqual(expected, (row["entity_kind"], row["lab_id"],
                                            row["person_id"], row["role"], row["basis"]))

        with self.subTest("no attribution creates no row"):
            eid = storage.insert_insight(self.conn, self.ev, "other", "c")
            self.assertEqual([], self._entities(eid))

    def test_schema_rejects_an_entity_that_is_both_lab_and_person(self):
        eid = storage.insert_insight(self.conn, self.ev, "other", "c")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO event_entities (event_id, entity_kind, person_id,"
                " lab_id, role, evidence_id) VALUES (?,?,?,?,?,?)",
                (eid, "lab", 2, 1, "subject", self.ev))

    def test_backfills_are_idempotent(self):
        """Both run every pipeline pass, so a second run must be a no-op."""
        with self.subTest("attribution from an official source"):
            sid = storage.upsert_source(self.conn, "blog", "Meta", "http://meta/feed",
                                        lab_id=1, channel="official")
            doc, _ = storage.store_document(self.conn, sid, "blog", "http://meta/p",
                                            "Meta shipped something.", None)
            ev = storage.insert_evidence(self.conn, doc, "{}", "Meta shipped something.",
                                         "exact", 1.0)
            self.conn.execute(
                "INSERT INTO insights (id, evidence_id, event_type, claim, created_at)"
                " VALUES (7, ?, 'release', 'c', '2026-01-01')", (ev,))
            self.conn.commit()
            self.assertEqual(1, storage.backfill_attribution_from_source(self.conn))
            self.assertEqual((1, "source_inferred"), tuple(self.conn.execute(
                "SELECT lab_id, basis FROM event_entities WHERE event_id=7").fetchone()))
            self.assertEqual(0, storage.backfill_attribution_from_source(self.conn))

        with self.subTest("event_entities from the attributed_* cache"):
            self.conn.execute(
                "INSERT INTO insights (id, evidence_id, attributed_lab_id, event_type,"
                " claim, created_at) VALUES (9, ?, 1, 'release', 'c', '2026-01-01')",
                (self.ev,))
            self.conn.commit()
            self.assertEqual(1, storage.backfill_event_entities(self.conn))
            self.assertEqual(0, storage.backfill_event_entities(self.conn))


class _FakeLLM:
    """Canned classify/extract JSON, so stage 2 is testable with no API key."""

    def __init__(self, classify_json, extract_json):
        self._c, self._e = classify_json, extract_json

    def call(self, task, system, user, max_tokens=1024):
        return self._c if task == "classify" else self._e


def _insights(*specs):
    return json.dumps({"insights": [
        {"claim": c, "quote": q, "event_type": t,
         "attributed_lab": lab, "attributed_person": person}
        for c, q, t, lab, person in specs]})


CLASSIFY = json.dumps({"event_type": "release", "substantive": True, "reason": "launch"})


class TestStage2(unittest.TestCase):
    """One document yields several quote-verified events, each separately
    attributed — and unverifiable quotes are COUNTED, never silently dropped."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(Path(storage.SCHEMA_PATH).read_text())
        self.conn.execute("INSERT INTO labs (id, name) VALUES (1, 'OpenAI')")
        self.conn.execute("INSERT INTO people (id, canonical_name) VALUES (2, 'Sam Altman')")
        sid = storage.upsert_source(self.conn, "blog", "s", "http://feed",
                                    lab_id=1, channel="official")
        body = ("GPT-5.6 launch\nhttp://p\n\nWe release GPT-5.6 today. "
                "Pricing drops to two dollars per million tokens. "
                "Sam Altman said the model tops the benchmark. "
                + "This post describes the launch in extensive detail. " * 100)
        self.doc, _ = storage.store_document(self.conn, sid, "blog", "http://p", body, None)

    def _count(self, table, where="", *args):
        return self.conn.execute(
            f"SELECT count(*) FROM {table} {where}", args).fetchone()[0]

    def test_one_document_yields_several_attributed_events(self):
        ext = _insights(
            ("OpenAI released GPT-5.6", "We release GPT-5.6 today", "release", "OpenAI", None),
            ("Price cut", "Pricing drops to two dollars per million tokens",
             "commercial", "OpenAI", None),
            ("Benchmark lead", "Sam Altman said the model tops the benchmark",
             "benchmark", None, "Sam Altman"))
        ids = run_stage2(self.conn, _FakeLLM(CLASSIFY, ext), self.doc)

        self.assertEqual(3, len(ids))
        # 2 model-asserted labs + 1 filled in from the official channel
        self.assertEqual(3, self._count("event_entities", "WHERE entity_kind='lab'"))
        self.assertEqual(1, self._count("event_entities", "WHERE basis='source_inferred'"))
        # person attribution resolved and stored
        self.assertEqual(1, self._count("event_entities", "WHERE entity_kind='person'"))
        self.assertEqual(2, self.conn.execute(
            "SELECT person_id FROM event_entities WHERE entity_kind='person'").fetchone()[0])
        # idempotent: re-running the same document adds nothing
        self.assertEqual(3, len(run_stage2(self.conn, _FakeLLM(CLASSIFY, ext), self.doc)))
        self.assertEqual(3, self._count("insights"))

    def test_extraction_cap_scales_with_document_length(self):
        """REGRESSION: a flat cap let arXiv abstracts over-produce — 4.17
        insights per 2.8k-char doc versus 3.23 from an 11.7k-char blog post.
        A length-proportional cap brought arXiv to 2.00."""
        llm = _FakeLLM("", _insights(*[(f"c{i}", f"q{i}", "release", None, None)
                                       for i in range(5)]))
        for name, length, expected in [("teaser", 297, 1), ("release note", 642, 1),
                                       ("abstract", 2842, 2), ("article", 11729, 5)]:
            with self.subTest(name):
                self.assertEqual(expected, len(extract(llm, "x" * length)))

    def test_unverifiable_quotes_are_counted_not_silently_dropped(self):
        """REGRESSION: an unverified quote hit `continue` with no log_rejection,
        so the denominator shrank and D1 read a fake 99.8%. Counting the failures
        moved the honest number to 95.5%."""
        with self.subTest("some quotes fail: the rest are kept, failures counted"):
            ext = _insights(
                ("a", "We release GPT-5.6 today", "release", "OpenAI", None),
                ("b", "Pricing drops to two dollars per million tokens",
                 "commercial", "OpenAI", None),
                ("c", "a fabricated sentence not in the document", "benchmark",
                 "OpenAI", None))
            self.assertEqual(2, len(run_stage2(self.conn, _FakeLLM(CLASSIFY, ext), self.doc)))
            self.assertEqual(1, self._count(
                "rejections", "WHERE reason='quote_unverified'"))
            # the document DID produce insights, so it is not a no_verified_insight
            self.assertEqual(0, self._count(
                "rejections", "WHERE reason='no_verified_insight'"))

        self.setUp()
        with self.subTest("every quote fails: per-insight AND document rows"):
            ext = _insights(("c", "a sentence that is not in the document at all",
                             "release", "OpenAI", None))
            self.assertEqual([], run_stage2(self.conn, _FakeLLM(CLASSIFY, ext), self.doc))
            self.assertEqual(1, self._count(
                "rejections", "WHERE document_id=? AND reason='quote_unverified'", self.doc))
            self.assertEqual(1, self._count(
                "rejections", "WHERE document_id=? AND reason='no_verified_insight'", self.doc))


if __name__ == "__main__":
    unittest.main()
