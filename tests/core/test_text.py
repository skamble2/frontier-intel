"""fli.core.text - pure text primitives (no DB, no network)."""
import unittest

from fli.core.text import (contains_verbatim, html_to_text, name_key, norm,
                           page_published)


class TestPagePublished(unittest.TestCase):
    """The page's own date, extracted in priority order. The failure this
    guards: a sitemap lastmod re-dated an 8-month-old release announcement to
    'last week' and put it back on the digest."""

    def test_jsonld_wins(self):
        html = ('<html><script type="application/ld+json">'
                '{"datePublished": "2025-11-24T09:00:00Z"}</script>'
                '<h1>Old news</h1><div>Jul 23, 2026</div></html>')
        self.assertEqual(page_published(html), "2025-11-24")

    def test_meta_published_time(self):
        html = ('<meta property="article:published_time" '
                'content="2026-04-16T12:00:00Z"/><h1>T</h1>')
        self.assertEqual(page_published(html), "2026-04-16")

    def test_time_tag(self):
        html = '<article><time datetime="2026-05-28">May 28</time></article>'
        self.assertEqual(page_published(html), "2026-05-28")

    def test_visible_byline_near_headline(self):
        html = ('<h1>Introducing Claude Opus 4.5</h1>'
                '<div class="agate">Nov 24, 2025</div>')
        self.assertEqual(page_published(html), "2025-11-24")

    def test_visible_date_far_from_headline_is_not_a_byline(self):
        html = ('<h1>Fresh story</h1>' + '<p>body</p>' * 200 +
                '<aside>Related: Jan 2, 2019</aside>')
        self.assertIsNone(page_published(html))

    def test_no_date_anywhere(self):
        self.assertIsNone(page_published("<html><h1>Undated</h1></html>"))


class TestNorm(unittest.TestCase):
    def test_casefold_and_whitespace(self):
        self.assertEqual(norm("  Liang   Wenfeng "), "liang wenfeng")

    def test_nfkc(self):
        self.assertEqual(norm("Ｔｉｍｏｔｈｅｅ"), "timothee")

    def test_name_key_order_insensitive(self):
        self.assertEqual(name_key("Liang Wenfeng"), name_key("Wenfeng Liang"))

    def test_name_key_distinct_names_differ(self):
        self.assertNotEqual(name_key("Mark Chen"), name_key("Mark Zhang"))

    def test_contains_verbatim(self):
        self.assertTrue(contains_verbatim("Dr. Jane Q. Doe leads the team",
                                          "jane q. doe"))
        self.assertFalse(contains_verbatim("Jane Doe leads", "John Doe"))


class TestHtmlToText(unittest.TestCase):
    def test_strips_script_and_style(self):
        html = "<p>Hello</p><script>var x=1;</script><style>p{}</style><p>World</p>"
        self.assertEqual(html_to_text(html), "Hello World")
