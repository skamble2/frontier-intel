"""fli.delivery.pdf — the dependency-free PDF writer.

A hand-written PDF that a viewer silently refuses to open is worse than no
export at all, so these tests do not check the bytes we produced against
themselves. They read the file back with an independent parser and assert on
what a READER would see.
"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fli.delivery import pdf


def _pypdf():
    """The independent parser, or a skip. The tests must not require a
    dependency the module itself avoids."""
    try:
        import pypdf
    except ImportError:                                  # pragma: no cover
        raise unittest.SkipTest("pypdf not installed; round-trip skipped")
    return pypdf


def _text(path: Path) -> str:
    """Read the file back with an independent parser."""
    pypdf = _pypdf()
    return "\n".join(p.extract_text() for p in pypdf.PdfReader(str(path)).pages)


class TestMeasurement(unittest.TestCase):
    """Wrapping is only as good as the metrics, and the metrics are the whole
    reason a line does not run off the page."""

    def test_bold_is_wider_than_regular(self):
        self.assertGreater(pdf.width_of("Frontier", "bold", 10),
                           pdf.width_of("Frontier", "reg", 10))

    def test_width_scales_with_size(self):
        self.assertAlmostEqual(pdf.width_of("abc", "reg", 20),
                               2 * pdf.width_of("abc", "reg", 10), places=6)

    def test_no_wrapped_line_exceeds_the_measured_width(self):
        text = ("Gemini 3.6 Flash reduces output token usage by 17% compared "
                "to 3.5 Flash — priced at $1.50 per million input tokens.") * 4
        for ln in pdf.wrap(text, "reg", 10.0, pdf.TEXT_W):
            self.assertLessEqual(pdf.width_of(ln, "reg", 10.0), pdf.TEXT_W)

    def test_an_unbreakable_url_is_broken_rather_than_overflowing(self):
        url = "https://deepmind.google/blog/" + "a" * 400
        for ln in pdf.wrap(url, "reg", 8.5, pdf.TEXT_W):
            self.assertLessEqual(pdf.width_of(ln, "reg", 8.5), pdf.TEXT_W)


class TestTheFileIsReadable(unittest.TestCase):
    BLOCKS = [("h1", "Frontier Lab Intelligence"),
              ("meta", "policy v3 · rubric investment r1"),
              ("rule", None),
              ("h2", "1. OpenAI is launching Health in ChatGPT"),
              ("quote", "“Starting today — medical records can be connected…”"),
              ("link", ("openai.com/index/health",
                        "https://openai.com/index/health"))]

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.path = pdf.render(Path(self.tmp.name) / "d.pdf", self.BLOCKS,
                               footer="frontier-intel")

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_text_survives_the_round_trip(self):
        got = _text(self.path)
        self.assertIn("Frontier Lab Intelligence", got)
        self.assertIn("OpenAI is launching Health", got)

    def test_typographic_punctuation_is_not_mangled(self):
        """Lab posts are full of curly quotes, em dashes and ellipses. Losing
        them to '?' would corrupt the verbatim quote, which is the one thing in
        the document that must be exact."""
        got = _text(self.path)
        for ch in ("—", "…", "“"):
            self.assertIn(ch, got, ch)

    def test_the_source_link_is_clickable(self):
        pypdf = _pypdf()
        page = pypdf.PdfReader(str(self.path)).pages[0]
        uris = [a.get_object()["/A"]["/URI"] for a in page.get("/Annots", [])]
        self.assertIn("https://openai.com/index/health", uris)

    def test_long_documents_paginate(self):
        pypdf = _pypdf()
        long_doc = [("p", "body text " * 60) for _ in range(30)]
        p = pdf.render(Path(self.tmp.name) / "long.pdf", long_doc)
        self.assertGreater(len(pypdf.PdfReader(str(p)).pages), 1)

    def test_characters_with_no_winansi_form_do_not_corrupt_the_file(self):
        p = pdf.render(Path(self.tmp.name) / "cjk.pdf",
                       [("p", "深度学習 and 🙂 and ordinary text")])
        self.assertIn("ordinary text", _text(p))

    def test_a_parenthesis_does_not_break_the_content_stream(self):
        """Unescaped ( ) end a PDF string early and corrupt everything after
        it — and every second claim in this corpus contains a parenthesis."""
        p = pdf.render(Path(self.tmp.name) / "paren.pdf",
                       [("p", "Gemma 4 (12B) matches a larger model \\ here")])
        self.assertIn("(12B)", _text(p))


if __name__ == "__main__":
    unittest.main()
