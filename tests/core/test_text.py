"""fli.core.text - pure text primitives (no DB, no network)."""
import unittest

from fli.core.text import contains_verbatim, html_to_text, name_key, norm


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
