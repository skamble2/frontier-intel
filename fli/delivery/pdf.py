"""A PDF writer with no dependencies, for the exported digest."""
from __future__ import annotations

import zlib
from pathlib import Path

_W_REG = (278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333,
          278, 278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278,
          584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278,
          500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944,
          667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556, 556,
          278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500,
          278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584)
_W_BOLD = (278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333,
           278, 278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 333, 333,
           584, 584, 584, 611, 975, 722, 722, 722, 722, 667, 611, 778, 722, 278,
           556, 722, 611, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944,
           667, 667, 611, 333, 278, 333, 584, 556, 333, 556, 611, 556, 611, 556,
           333, 611, 611, 278, 278, 556, 278, 889, 611, 611, 611, 611, 389, 556,
           333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584)

_WINANSI = {"—": (0x97, 1000), "–": (0x96, 556), "‘": (0x91, 222),
            "’": (0x92, 222), "“": (0x93, 333), "”": (0x94, 333),
            "…": (0x85, 1000), "•": (0x95, 350), " ": (0x20, 278),
            "−": (0x2d, 333), "×": (0xd7, 584), "€": (0x80, 556),
            "·": (0xb7, 278), "°": (0xb0, 400), "™": (0x99, 1000),
            "→": (0x2d, 333), "±": (0xb1, 584), "\u00a0": (0x20, 278)}

FONTS = {"reg": "F1", "bold": "F2", "obl": "F3"}
_BASE = {"F1": "Helvetica", "F2": "Helvetica-Bold", "F3": "Helvetica-Oblique"}

PAGE_W, PAGE_H = 595.28, 841.89
MARGIN_X, MARGIN_TOP, MARGIN_BOT = 56.0, 64.0, 56.0
TEXT_W = PAGE_W - 2 * MARGIN_X

STYLES = {
    "h1":     ("bold", 17.0, 21.0, 0.0, 0.0, 0.0),
    "h2":     ("bold", 12.5, 16.0, 16.0, 0.0, 0.0),
    "h3":     ("bold", 10.5, 14.0, 11.0, 0.0, 0.0),
    "p":      ("reg", 10.0, 13.6, 6.0, 0.0, 0.0),
    "bullet": ("reg", 10.0, 13.6, 2.0, 12.0, 0.0),
    "meta":   ("reg", 8.5, 11.5, 3.0, 0.0, 0.42),
    "quote":  ("obl", 9.5, 13.0, 8.0, 16.0, 0.18),
    "link":   ("reg", 8.5, 11.5, 3.0, 0.0, 0.0),
}


def _encode(s: str) -> tuple[bytes, list[int]]:
    """(WinAnsi bytes, per-glyph width index) — the two things layout needs."""
    out, idx = bytearray(), []
    for ch in s:
        o = ord(ch)
        if 32 <= o <= 126:
            out.append(o)
            idx.append(o - 32)
        elif ch in _WINANSI:
            b, _ = _WINANSI[ch]
            out.append(b)
            idx.append(-ord(ch))
        elif ch == "\t":
            out.extend(b"    ")
            idx.extend([0, 0, 0, 0])
        else:
            out.append(0x3f)
            idx.append(0x3f - 32)
    return bytes(out), idx


def width_of(s: str, font: str, size: float) -> float:
    table = _W_BOLD if font == "bold" else _W_REG
    _, idx = _encode(s)
    total = 0
    for i in idx:
        total += table[i] if i >= 0 else _WINANSI[chr(-i)][1]
    return total * size / 1000.0


def wrap(text: str, font: str, size: float, width: float) -> list[str]:
    """Greedy wrap on measured widths. """
    lines: list[str] = []
    for para in text.split("\n"):
        if not para.strip():
            lines.append("")
            continue
        cur = ""
        for word in para.split(" "):
            trial = f"{cur} {word}".strip()
            if cur and width_of(trial, font, size) > width:
                lines.append(cur)
                cur = word
            else:
                cur = trial
            while width_of(cur, font, size) > width and len(cur) > 1:
                cut = len(cur)
                while cut > 1 and width_of(cur[:cut], font, size) > width:
                    cut -= 1
                lines.append(cur[:cut])
                cur = cur[cut:]
        if cur:
            lines.append(cur)
    return lines


def _esc(b: bytes) -> bytes:
    for a, r in ((b"\\", b"\\\\"), (b"(", b"\\("), (b")", b"\\)")):
        b = b.replace(a, r)
    return b


class _Page:
    def __init__(self) -> None:
        self.ops: list[bytes] = []
        self.annots: list[tuple[float, float, float, float, str]] = []

    def text(self, x: float, y: float, s: str, font: str, size: float,
             grey: float) -> None:
        raw, _ = _encode(s)
        self.ops.append(
            b"BT /%s %.2f Tf %.3f g %.2f %.2f Td (%s) Tj ET"
            % (FONTS[font].encode(), size, grey, x, y, _esc(raw)))

    def line(self, x0: float, y0: float, x1: float, y1: float,
             grey: float = 0.75, w: float = 0.5) -> None:
        self.ops.append(b"%.3f G %.2f w %.2f %.2f m %.2f %.2f l S"
                        % (grey, w, x0, y0, x1, y1))


def render(path: Path, blocks, footer: str = "") -> Path:
    """blocks: iterable of (style, payload)."""
    pages = [_Page()]
    y = PAGE_H - MARGIN_TOP

    def new_page():
        nonlocal y
        pages.append(_Page())
        y = PAGE_H - MARGIN_TOP

    for style, payload in blocks:
        if style == "pagebreak":
            new_page()
            continue
        if style == "rule":
            if y - 12 < MARGIN_BOT:
                new_page()
            y -= 8
            pages[-1].line(MARGIN_X, y, PAGE_W - MARGIN_X, y)
            y -= 6
            continue
        font, size, lead, before, indent, grey = STYLES[style]
        label, url = (payload if style == "link" else (payload, None))
        y -= before
        for i, ln in enumerate(wrap(str(label), font, size, TEXT_W - indent)):
            if y - lead < MARGIN_BOT:
                new_page()
            y -= lead
            x = MARGIN_X + indent
            if style == "quote":
                pages[-1].line(MARGIN_X + 5, y - 2.5, MARGIN_X + 5,
                               y + lead - 3.5, grey=0.6, w=1.2)
            pages[-1].text(x, y, ln, font, size, 0.20 if style == "link" else grey)
            if url:
                w = width_of(ln, font, size)
                pages[-1].annots.append((x, y - 2, x + w, y + size, url))

    if footer:
        for n, pg in enumerate(pages, 1):
            pg.text(MARGIN_X, MARGIN_BOT - 22,
                    f"{footer}   ·   page {n} of {len(pages)}", "reg", 7.5, 0.5)

    return _serialize(path, pages)


def _serialize(path: Path, pages: list[_Page]) -> Path:
    objs: list[bytes] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    font_ids = {}
    for key, base in _BASE.items():
        font_ids[key] = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /%s "
                            b"/Encoding /WinAnsiEncoding >>" % base.encode())
    res = (b"<< /Font << " + b" ".join(
        b"/%s %d 0 R" % (k.encode(), v) for k, v in font_ids.items()) + b" >> >>")

    pages_id = add(b"")
    page_ids, page_objs = [], []
    for pg in pages:
        stream = zlib.compress(b"\n".join(pg.ops))
        sid = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n%s\nendstream"
                  % (len(stream), stream))
        annot_ids = [add(b"<< /Type /Annot /Subtype /Link /Rect [%.2f %.2f %.2f "
                         b"%.2f] /Border [0 0 0] /A << /S /URI /URI (%s) >> >>"
                         % (x0, y0, x1, y1, _esc(url.encode("latin-1", "replace"))))
                     for x0, y0, x1, y1, url in pg.annots]
        body = (b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.2f %.2f] "
                b"/Resources %s /Contents %d 0 R"
                % (pages_id, PAGE_W, PAGE_H, res, sid))
        if annot_ids:
            body += b" /Annots [" + b" ".join(b"%d 0 R" % a
                                              for a in annot_ids) + b"]"
        page_ids.append(add(body + b" >>"))
        page_objs.append(body)
    objs[pages_id - 1] = (b"<< /Type /Pages /Count %d /Kids [%s] >>"
                          % (len(page_ids),
                             b" ".join(b"%d 0 R" % p for p in page_ids)))
    root = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, root, xref))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path
