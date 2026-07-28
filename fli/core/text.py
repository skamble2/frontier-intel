"""Pure text primitives: no I/O, no state, no clock.

Everything here is a deterministic function of its arguments, which is
why `norm` can be shared by extraction and by the checks battery - a
split-brain between those two once caused a false C2 failure."""
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
    """Shared normalization for all text matching: NFKC, punctuation fold
    (curly quotes/dashes -> ASCII; NFKC leaves these alone), casefold,
    collapse whitespace. One function, so 'match' means exactly one thing."""
    return " ".join(
        unicodedata.normalize("NFKC", s).translate(_PUNCT_FOLD).casefold().split())


def contains_verbatim(haystack: str, needle: str) -> bool:
    """Exact containment under the shared normalization. No fuzzy matching."""
    return norm(needle) in norm(haystack)


def fold_accents(s: str) -> str:
    """Strip combining marks: 'Timothée' -> 'timothee'.

    Deliberately NOT part of `norm`. `norm` backs the verbatim-quote invariant,
    where folding would let a quote 'match' bytes it does not equal. Names are
    the opposite problem: the SAME person is written both ways depending on
    whether the source could be bothered with the diacritic, and treating those
    as two people is an entity-resolution failure.
    """
    return "".join(ch for ch in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(ch))


def name_key(name: str) -> str:
    """Order-insensitive, accent-insensitive name key: normalized tokens,
    sorted. Handles surname-first vs Western order ('Liang Wenfeng' ==
    'Wenfeng Liang') and diacritic drift ('Timothee' == 'Timothée').

    WHY THE ACCENT FOLD: 'Timothée Lacroix' was in the register and an X
    profile lookup for 'Timothee Lacroix' failed to find him, so a known,
    already-evidenced person was rejected as a stranger. arXiv author strings,
    X display names and lab pages disagree about diacritics constantly, and
    every disagreement was silently becoming a second identity.
    """
    return " ".join(sorted(fold_accents(norm(name)).split()))
