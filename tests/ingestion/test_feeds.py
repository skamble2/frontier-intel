"""fli.ingestion.feeds - LAYER 1 feed/sitemap parsing and body hydration."""
import unittest

from fli.core.http import FetchError
from fli.ingestion import feeds
from fli.ingestion.feeds import parse_feed


class TestHydratedBody(unittest.TestCase):
    """Blog teasers get the linked article fetched; other cases don't."""

    def setUp(self):
        self._orig = feeds.http_get

    def tearDown(self):
        feeds.http_get = self._orig

    def test_thin_blog_fetches_article(self):
        feeds.http_get = lambda u: (
            "<html><body>" + "Full article body. " * 50 + "</body></html>", "")
        body = feeds._hydrated_body({"content": "teaser", "link": "http://x/a"}, "blog")
        self.assertIn("Full article body.", body)
        self.assertGreater(len(body), 900)

    def test_full_blog_not_fetched(self):
        def boom(u):
            raise AssertionError("should not fetch a full-body feed")
        feeds.http_get = boom
        self.assertEqual(
            feeds._hydrated_body({"content": "x" * 2000, "link": "http://x/a"}, "blog"),
            "x" * 2000)

    def test_github_not_fetched(self):
        def boom(u):
            raise AssertionError("release notes are the content")
        feeds.http_get = boom
        self.assertEqual(
            feeds._hydrated_body({"content": "short release", "link": "http://x/r"}, "github"),
            "short release")

    def test_fetch_failure_keeps_teaser(self):
        def boom(u):
            raise FetchError("down")
        feeds.http_get = boom
        self.assertEqual(
            feeds._hydrated_body({"content": "teaser", "link": "http://x/a"}, "blog"),
            "teaser")


class TestParseFeed(unittest.TestCase):
    RSS = """<rss version="2.0"><channel>
        <item><title>T1</title><link>http://x/1</link>
        <pubDate>Mon, 21 Jul 2026 10:00:00 GMT</pubDate>
        <description>D1</description></item>
        </channel></rss>"""
    ATOM = """<feed xmlns="http://www.w3.org/2005/Atom">
        <entry><title>A1</title><link href="http://y/1"/>
        <published>2026-07-21T10:00:00Z</published>
        <content>C1</content></entry></feed>"""

    def test_rss(self):
        (e,) = parse_feed(self.RSS)
        self.assertEqual((e["title"], e["link"], e["content"]),
                         ("T1", "http://x/1", "D1"))
        self.assertTrue(e["published"].startswith("2026-07-21T10:00:00"))

    def test_atom(self):
        (e,) = parse_feed(self.ATOM)
        self.assertEqual((e["title"], e["link"], e["content"]),
                         ("A1", "http://y/1", "C1"))
