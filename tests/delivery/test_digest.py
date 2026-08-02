"""fli.delivery.digest — the reader-facing report.

The failures that matter here are failures of honesty, not of formatting: an
item shown without the reading it appears to have, a period that silently
widens, or a coverage claim the document does not support.
"""
import datetime as dt
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fli.delivery import digest
from tests.helpers import memory_db


def _seed(conn, days_ago=(2, 3, 200)):
    """Three scored events, all touching a holding, at chosen ages."""
    conn.execute("INSERT INTO sources (source_type,name,url) VALUES ('blog','s','u')")
    for i, _ in enumerate(days_ago, 1):
        # One lab each: the slate caps how many items a single lab may occupy,
        # so a fixture where everything is unattributed tests the cap instead
        # of the period.
        conn.execute("INSERT INTO labs (id,name) VALUES (?,?)", (i, f"Lab {i}"))
    for i, ago in enumerate(days_ago, 1):
        pub = (dt.date.today() - dt.timedelta(days=ago)).isoformat()
        conn.execute("INSERT INTO raw_documents (id,source_id,source_type,url,"
                     "content_hash,raw_content,retrieved_at,published_at)"
                     " VALUES (?,1,'blog',?,?,'x','t',?)",
                     (i, f"https://lab.example/{i}", f"h{i}", pub))
        conn.execute("INSERT INTO evidence (id,document_id,locator,"
                     "verbatim_content,verification) VALUES (?,?,'{}',?,'exact')",
                     (i, i, f"verbatim quote {i} copied word for word from the "
                            f"stored source document"))
        conn.execute("INSERT INTO insights (id,evidence_id,attributed_lab_id,"
                     "event_type,claim,score,created_at)"
                     " VALUES (?,?,?,'release',?,?,'t')",
                     (i, i, i, f"claim {i}", 5.0 - i))
        # A per-rubric ranking is what the digest reads; insights.score alone
        # is the primary rubric's copy and would silently give the engineering
        # audience the investment ordering.
        for rubric in ("investment", "technical"):
            conn.execute(
                "INSERT INTO event_scores (event_id,model,score,rank,"
                "components,created_at,policy_version) VALUES"
                " (?,?,?,?,'{\"winner\": true}','t',3)",
                (i, f"{rubric}:logistic", 5.0 - i, i))
    conn.commit()


class TestThePeriodIsRealNotDecorative(unittest.TestCase):
    def setUp(self):
        self.conn = memory_db()
        _seed(self.conn)

    def test_an_event_outside_the_period_is_not_published(self):
        b, stats = digest.blocks(self.conn, "investment", days=7)
        body = digest.to_markdown(b)
        self.assertIn("claim 1", body)
        self.assertNotIn("claim 3", body)      # 200 days old
        self.assertEqual(stats["items"], 2)

    def test_widening_the_period_admits_the_older_event(self):
        b, _ = digest.blocks(self.conn, "investment", days=365)
        self.assertIn("claim 3", digest.to_markdown(b))

    def test_an_empty_period_says_so_rather_than_reaching_further_back(self):
        # The window is anchored to the newest DOCUMENT, so an empty period
        # requires the corpus to have moved on: a fresh eventless doc (a feed
        # fetch that yielded nothing scoreable) pushes the anchor past every
        # scored event.
        self.conn.execute(
            "INSERT INTO raw_documents (id,source_id,source_type,url,"
            "content_hash,raw_content,retrieved_at,published_at)"
            " VALUES (99,1,'blog','https://lab.example/fresh','h99','x','t',?)",
            (dt.date.today().isoformat(),))
        self.conn.commit()
        b, stats = digest.blocks(self.conn, "investment", days=1)
        self.assertEqual(stats["items"], 0)
        self.assertIn("Nothing to report", digest.to_markdown(b))


class TestCoverageIsStatedNotImplied(unittest.TestCase):
    """A reader must be able to tell a read item from a merely ranked one.
    Dropping the unread ones instead would make the report look complete."""

    def setUp(self):
        self.conn = memory_db()
        _seed(self.conn)
        self.conn.execute(
            "INSERT INTO hypotheses (insight_id,persona,hypothesis,direction,"
            "confidence,time_horizon,reasoning) VALUES"
            " (1,'investment','h one','threat','high','now','because the quote')")
        self.conn.commit()

    def test_the_header_counts_readings(self):
        _, stats = digest.blocks(self.conn, "investment", days=7)
        self.assertEqual((stats["items"], stats["with_reading"]), (2, 1))

    def test_an_item_with_no_reading_is_shown_and_labelled(self):
        body = digest.to_markdown(digest.blocks(self.conn, "investment", 7)[0])
        self.assertIn("claim 2", body)
        self.assertIn("No reading rendered", body)

    def test_the_reading_and_its_working_are_both_published(self):
        body = digest.to_markdown(digest.blocks(self.conn, "investment", 7)[0])
        self.assertIn("h one", body)
        self.assertIn("because the quote", body)

    def test_a_reading_written_for_the_other_audience_is_not_shown(self):
        """The investment hypothesis must not leak into the engineering
        digest — the two answer different questions."""
        body = digest.to_markdown(digest.blocks(self.conn, "ai_team", 7)[0])
        self.assertNotIn("h one", body)


class TestEveryClaimCarriesItsEvidence(unittest.TestCase):
    def setUp(self):
        self.conn = memory_db()
        _seed(self.conn)

    def test_each_item_is_published_with_its_quote_and_source(self):
        b, stats = digest.blocks(self.conn, "investment", days=7)
        body = digest.to_markdown(b)
        self.assertEqual(sum(1 for s, _ in b if s == "quote"), stats["items"])
        for i in (1, 2):
            self.assertIn(f"verbatim quote {i}", body)
            self.assertIn(f"https://lab.example/{i}", body)


class TestBothRenderersSeeTheSameDocument(unittest.TestCase):
    def setUp(self):
        self.conn = memory_db()
        _seed(self.conn)
        self.tmp = TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_markdown_and_pdf_are_written_from_one_block_list(self):
        stats = digest.write(self.conn, "investment", days=7,
                             out_dir=Path(self.tmp.name), verbose=False)
        self.assertEqual(len(stats["files"]), 2)
        md, pdf_path = (Path(p) for p in stats["files"])
        self.assertTrue(md.exists() and pdf_path.exists())
        try:
            import pypdf
        except ImportError:                              # pragma: no cover
            self.skipTest("pypdf not installed")
        got = "\n".join(p.extract_text()
                        for p in pypdf.PdfReader(str(pdf_path)).pages)
        for claim in ("claim 1", "claim 2"):
            self.assertIn(claim, md.read_text())
            self.assertIn(claim, got)

    def test_markdown_markup_does_not_leak_into_the_pdf(self):
        b = [("p", "`unclear` is an answer")]
        self.assertEqual(digest._for_pdf(b), [("p", "unclear is an answer")])


if __name__ == "__main__":
    unittest.main()
