"""Pure text primitives: no I/O, no state, no clock."""
import re
import unicodedata
from html.parser import HTMLParser


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "template"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0 and data.strip():
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Visible text of an HTML document. Pure and deterministic."""
    p = _TextExtractor()
    p.feed(html)
    return " ".join(" ".join(part.split()) for part in p.parts)


_PUNCT_FOLD = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"',
                             "—": "-", "–": "-"})


def norm(s: str) -> str:
    """Shared normalization for all text matching: NFKC, punctuation fold (curly
    quotes/dashes -> ASCII; NFKC leaves these alone), casefold, collapse
    whitespace."""
    return " ".join(
        unicodedata.normalize("NFKC", s).translate(_PUNCT_FOLD).casefold().split())


def contains_verbatim(haystack: str, needle: str) -> bool:
    """Exact containment under the shared normalization. No fuzzy matching."""
    return norm(needle) in norm(haystack)


def fold_accents(s: str) -> str:
    """Strip combining marks: 'Timothée' -> 'timothee'."""
    return "".join(ch for ch in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(ch))


def name_key(name: str) -> str:
    """Order- and accent-insensitive name key: normalized tokens, sorted."""
    return " ".join(sorted(fold_accents(norm(name)).split()))


_DATE_PATTERNS = [
    re.compile(r'"datePublished"\s*:\s*"(\d{4}-\d{2}-\d{2})'),
    re.compile(r'property="article:published_time"\s+content="(\d{4}-\d{2}-\d{2})'),
    re.compile(r'<time[^>]+datetime="(\d{4}-\d{2}-\d{2})'),
]
_VISIBLE_DATE = re.compile(
    r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+"
    r"\d{1,2},\s+(?:19|20)\d{2})")
_H1 = re.compile(r"<h1[\s>]")
_MONTH_NUM = {m: i for i, m in enumerate(
    "jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
_H1_WINDOW = 400


def page_published(html: str) -> str | None:
    """The page's own publication date as YYYY-MM-DD, or None."""
    for pat in _DATE_PATTERNS:
        m = pat.search(html)
        if m:
            return m.group(1)
    h1 = _H1.search(html)
    if h1:
        m = _VISIBLE_DATE.search(html, h1.start(), h1.start() + _H1_WINDOW)
        if m:
            mon, day, year = re.match(
                r"([A-Za-z]+)\.?\s+(\d{1,2}),\s+(\d{4})", m.group(1)).groups()
            month = _MONTH_NUM.get(mon[:3].lower())
            if month:
                return f"{int(year):04d}-{month:02d}-{int(day):02d}"
    return None
