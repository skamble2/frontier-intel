"""fli.ingestion.feeds - LAYER 1 feed/sitemap parsing and body hydration."""
import unittest
from unittest import mock

from fli.core.http import FetchError
from fli.ingestion import feeds
from fli.ingestion.feeds import parse_feed


class TestHydratedBody(unittest.TestCase):
    """Blog teasers get the linked article fetched; other cases don't.
    `mock.patch` rather than hand-swapped globals: a mid-test failure must
    never leak a fake http_get into the tests that run after it."""

    def test_thin_blog_fetches_article(self):
        with mock.patch.object(feeds, "http_get", return_value=(
                "<html><body>" + "Full article body. " * 50 + "</body></html>", "")):
            body = feeds._hydrated_body({"content": "teaser", "link": "http://x/a"}, "blog")
        self.assertIn("Full article body.", body)
        self.assertGreater(len(body), 900)

    def test_full_blog_not_fetched(self):
        with mock.patch.object(feeds, "http_get",
                               side_effect=AssertionError("should not fetch a full-body feed")):
            self.assertEqual(
                feeds._hydrated_body({"content": "x" * 2000, "link": "http://x/a"}, "blog"),
                "x" * 2000)

    def test_github_not_fetched(self):
        with mock.patch.object(feeds, "http_get",
                               side_effect=AssertionError("release notes are the content")):
            self.assertEqual(
                feeds._hydrated_body({"content": "short release", "link": "http://x/r"}, "github"),
                "short release")

    def test_fetch_failure_keeps_teaser(self):
        with mock.patch.object(feeds, "http_get", side_effect=FetchError("down")):
            self.assertEqual(
                feeds._hydrated_body({"content": "teaser", "link": "http://x/a"}, "blog"),
                "teaser")


class TestJsWallFallback(unittest.TestCase):
    """JS-walled domains (config.JS_WALLED_DOMAINS) serve a shell to direct
    fetches; the rendering proxy recovers the body. The longest candidate
    always wins, so the fallback can never make a document poorer."""

    WALLED = {"content": "teaser only", "link": "https://openai.com/index/story"}

    def test_thin_shell_falls_back_to_rendered_text(self):
        article = "Rendered article body. " * 100
        with mock.patch.object(feeds, "http_get",
                               return_value=("<html><body>shell</body></html>", "")), \
             mock.patch.object(feeds, "http_get_rendered", return_value=article):
            self.assertEqual(feeds._hydrated_body(dict(self.WALLED), "blog"), article)

    def test_rich_direct_fetch_skips_the_proxy(self):
        html = "<html><body>" + "Full body. " * 200 + "</body></html>"
        with mock.patch.object(feeds, "http_get", return_value=(html, "")), \
             mock.patch.object(feeds, "http_get_rendered",
                               side_effect=AssertionError("proxy must not be used")):
            self.assertIn("Full body.", feeds._hydrated_body(dict(self.WALLED), "blog"))

    def test_unwalled_domain_never_uses_the_proxy(self):
        entry = {"content": "teaser", "link": "https://example.com/post"}
        with mock.patch.object(feeds, "http_get",
                               return_value=("<html><body>thin</body></html>", "")), \
             mock.patch.object(feeds, "http_get_rendered",
                               side_effect=AssertionError("proxy must not be used")):
            self.assertEqual(feeds._hydrated_body(entry, "blog"), "teaser")

    def test_proxy_failure_keeps_best_candidate(self):
        with mock.patch.object(feeds, "http_get",
                               return_value=("<html><body>shell but longer</body></html>", "")), \
             mock.patch.object(feeds, "http_get_rendered", side_effect=FetchError("429")):
            self.assertEqual(feeds._hydrated_body(dict(self.WALLED), "blog"),
                             "shell but longer")

    def test_subdomain_counts_as_walled(self):
        self.assertTrue(feeds._js_walled("https://www.openai.com/x"))
        self.assertTrue(feeds._js_walled("https://openai.com/x"))
        self.assertFalse(feeds._js_walled("https://notopenai.com/x"))
        self.assertFalse(feeds._js_walled("https://openai.com.evil.example/x"))


class TestSitemapDates(unittest.TestCase):
    """Sitemap ingestion must store the page's own date, not lastmod: lastmod
    is when the page last changed, and a template rerender once re-dated an
    8-month-old announcement into the digest window."""

    PAGE = ('<html><h1>Introducing Opus</h1><div>Nov 24, 2025</div>'
            '<p>' + 'body text. ' * 100 + '</p></html>')
    SITEMAP = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <url><loc>https://www.anthropic.com/news/opus</loc>
        <lastmod>2026-07-23</lastmod></url></urlset>"""

    def test_page_byline_beats_lastmod(self):
        from tests.helpers import memory_db
        conn = memory_db()
        try:
            fetches = {"https://www.anthropic.com/sitemap.xml": self.SITEMAP,
                       "https://www.anthropic.com/news/opus": self.PAGE}
            with mock.patch.object(feeds, "http_get",
                                   side_effect=lambda u, **kw: (fetches[u], "")):
                feeds.ingest_sitemap(conn, "Anthropic",
                                     "https://www.anthropic.com/sitemap.xml")
            row = conn.execute(
                "SELECT published_at FROM raw_documents WHERE url LIKE '%/news/opus'"
            ).fetchone()
            self.assertEqual(row["published_at"], "2025-11-24")
        finally:
            conn.close()


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
