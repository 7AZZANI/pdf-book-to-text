#!/usr/bin/env python3
"""
pdf_book_to_text.py - Extract clean, well-structured plain text from PDF books.

Designed especially for PROGRAMMING books:

  * Chapters / sections are detected from the PDF bookmarks (outline) when
    available, otherwise from font sizes / bold styling, and rendered as clear
    text headings (===== / ----- / ~~~~~ underlines).
  * Code listings are detected by their monospaced font and reproduced with
    their ORIGINAL INDENTATION and internal alignment (rebuilt from character
    positions), blank lines inside listings are kept, listings broken across
    pages are re-joined, and typographic quotes inside code are straightened.
  * Running headers, footers and page numbers are removed automatically.
  * Paragraphs are re-flowed (lines joined, hyphenation repaired using a
    vocabulary built from the book itself), across page breaks too.
  * Bullet / numbered lists keep their structure.
  * Two-column layouts are detected and read in the correct order.
  * Optional: tables as ASCII grids, figure placeholders, page markers,
    OCR for scanned pages, one file per chapter.

Usage
-----
    python pdf_book_to_text.py book.pdf                 # -> book.txt next to the PDF
    python pdf_book_to_text.py book.pdf -o out.txt
    python pdf_book_to_text.py ~/Books/ -r              # every PDF in a folder
    python pdf_book_to_text.py book.pdf --split-chapters --tables
    python pdf_book_to_text.py                          # asks for the path interactively

Requires:  pip install pymupdf
"""
from __future__ import annotations

import argparse
import difflib
import html
import re
import statistics
import sys
import textwrap
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

try:
    import pymupdf  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover
    try:
        import fitz as pymupdf  # older PyMuPDF
    except ImportError:
        sys.exit("ERROR: PyMuPDF is required.  Install it with:  pip install pymupdf")

__version__ = "1.0.0"

# --------------------------------------------------------------------------- #
#  Constants & regular expressions
# --------------------------------------------------------------------------- #
TEXT_FLAGS = getattr(pymupdf, "TEXT_PRESERVE_WHITESPACE", 2) | getattr(pymupdf, "TEXT_MEDIABOX_CLIP", 64)

# Regex to detect Right-to-Left scripts (Arabic, Hebrew, Farsi, Urdu, etc.)
RTL_RE = re.compile(r"[\u0590-\u08ff\ufb1d-\ufdff\ufe70-\ufefc]")


def is_rtl_text(s: str) -> bool:
    return bool(RTL_RE.search(s))


def sanitize_text(s: str) -> str:
    """Strip null bytes, repair byte-swapped UTF-16, unescape HTML entities, and eliminate unprintable control chars.

    Prevents Windows Notepad / editors from misdetecting English or Arabic text as UTF-16
    and displaying Chinese/CJK ideographs (Mojibake).
    """
    if not s:
        return ""
    if "&" in s and ";" in s:
        s = html.unescape(s)
    chars = []
    for c in s:
        code = ord(c)
        if code == 0:
            continue
        # Repair byte-swapped ASCII characters (e.g. from corrupt PDF bookmarks like 0x5000 -> 'P')
        if (code & 0xFF) == 0 and 0x20 <= (code >> 8) <= 0x7E:
            chars.append(chr(code >> 8))
        # Drop non-printable control characters except tab, newline, carriage return
        elif code < 32 and code not in (9, 10, 13):
            continue
        else:
            chars.append(c)
    return "".join(chars)


# Font names that indicate a monospaced (code) font.
MONO_FONT_RE = re.compile(
    r"mono|courier|consol|menlo|monaco|inconsolata|fixed|typewriter|(?<!en)code|"
    r"(?:^|[^a-z])(?:cm|sf|lm|ec|tc|tx|rm)?tt\d*(?:[^a-z]|$)|cmtt|sftt|lmtt|txtt|pcrr|ucrr|"
    r"andale|hack|letter ?gothic|prestige|ocr-?[ab]|nimbus ?mon|lucida ?console|anonymous|"
    r"terminus|profont|iosevka|cascadia|everson|fira ?(?:code|mono)|jetbrains|ubuntu ?mono|"
    r"source ?code|ptmono|pt ?mono|sometype|plex ?mono|go ?mono|input ?mono|"
    r"mplus|1mn|dejavu ?sans ?mono|liberation ?mono|noto ?(?:sans ?)?mono|pragmata|sarasa|"
    r"commit ?mono|intel ?one ?mono|victor ?mono|envy ?code|agave|dank ?mono",
    re.I,
)
BOLD_FONT_RE = re.compile(r"bold|black|heavy|semibold|demi|extrab", re.I)

CHAR_MAP = {
    "\x00": "", "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl",
    "\ufb05": "st", "\ufb06": "st", "\t": " ", "\r": "", "\n": "",
}
for _c in "\u00a0\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000":
    CHAR_MAP[_c] = " "
for _c in "\u200b\u200c\u200d\u2060\ufeff":
    CHAR_MAP[_c] = ""
for _c in "\uf0b7\uf0a7\uf0d8\uf076\uf0fc\uf0a8\uf06e\uf0e0\uf075\uf0d7\uf09f\uf0a2\u2219\u25cf":
    CHAR_MAP[_c] = "•"
CHAR_TRANS = str.maketrans(CHAR_MAP)

CODE_TRANS = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u2033": '"',
    "\u2212": "-", "\u2010": "-", "\u2011": "-", "\u00ad": "-",
})

TERMINAL_RE = re.compile(r"[.!?:;][\"'\u201d\u2019)\]]*$")
STRONG_BULLET_RE = re.compile(r"^([•○◦▪▫■□►▸‣⁃✓✔➢➤❖◆◇])\s*(?=\S)")
WEAK_BULLET_RE = re.compile(r"^([-–—*])\s+(?=\S)")
NUM_LIST_RE = re.compile(r"^(\(?\d{1,3}[.)]|\(?[a-z][.)]|\(?(?:i|ii|iii|iv|v|vi|vii|viii|ix|x)[.)]|\(?[A-Z]\))\s+(?=\S)")
PAGE_NUM_RE = re.compile(r"^[-–—\s]*(?:page\s*)?(?:\d{1,4}|[ivxlcdm]{1,7})(?:\s*(?:/|of)\s*\d{1,4})?[-–—\s]*$", re.I)
LABEL_RE = re.compile(r"^(?:chapter|part|appendix|lesson|module|unit|section|book|day|hour|step)?\s*"
                      r"(?:\d{1,3}|[ivxlcdm]{1,6}|[a-z])[.:]?$", re.I)
CHAPTER_RE = re.compile(r"^(?:chapter|part|appendix)\s+(?:\d{1,3}|[ivxlcdm]{1,6}|[a-z]|one|two|three|four|"
                        r"five|six|seven|eight|nine|ten)\b", re.I)
LEADER_RE = re.compile(r"(?:\s*\.){4,}\s*")
WORD_RE = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)*")


# --------------------------------------------------------------------------- #
#  Data structures
# --------------------------------------------------------------------------- #
class Seg:
    """A run of text in one font, with whitespace trimmed and positions kept."""
    __slots__ = ("x0", "x1", "text", "size", "mono", "bold", "cw", "base", "nchars")

    def __init__(self, x0, x1, text, size, mono, bold, cw, base):
        self.x0, self.x1, self.text, self.size = x0, x1, text, size
        self.mono, self.bold, self.cw, self.base = mono, bold, cw, base
        self.nchars = len(text.replace(" ", ""))


class Line:
    """A visual line of text (one or more segments on the same baseline)."""
    __slots__ = ("x0", "y0", "x1", "y1", "segs", "base", "size", "mono_ratio", "bold_ratio",
                 "cw", "kind", "level", "text", "col", "nchars", "tabular", "block", "dropcap")

    def __init__(self, segs, y0, y1):
        if any(is_rtl_text(s.text) for s in segs):
            self.segs = list(segs)
        else:
            self.segs = sorted(segs, key=lambda s: s.x0)
        self.y0, self.y1 = y0, y1
        self.kind, self.level, self.text, self.col = "text", 0, "", 0
        self.tabular, self.block, self.dropcap = False, None, False
        self.refresh()

    def refresh(self):
        segs = self.segs
        self.x0 = min(s.x0 for s in segs)
        self.x1 = max(s.x1 for s in segs)
        n = sum(s.nchars for s in segs) or 1
        self.nchars = n
        self.mono_ratio = sum(s.nchars for s in segs if s.mono) / n
        self.bold_ratio = sum(s.nchars for s in segs if s.bold) / n
        dom = max(segs, key=lambda s: s.nchars)
        sizes = Counter()
        for s in segs:
            sizes[round(s.size, 1)] += s.nchars
        self.size = sizes.most_common(1)[0][0]
        self.base = dom.base
        cws = [s.cw for s in segs if s.mono and s.cw > 0]
        self.cw = statistics.median(cws) if cws else 0.0

    @property
    def plain(self) -> str:
        return " ".join(s.text for s in self.segs)

    @property
    def h(self) -> float:
        return max(self.y1 - self.y0, 1.0)

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass
class PageData:
    pno: int
    label: str
    width: float
    height: float
    lines: list
    blocks: list            # (y0, x0, kind, text) for tables / figures
    scanned: bool = False
    garbled: bool = False
    ncols: int = 1
    gutter: float = 0.0
    colm: dict = None
    toc_unmatched: list = None


class Heading:
    __slots__ = ("level", "text", "from_font")

    def __init__(self, level, text, from_font=False):
        self.level, self.text, self.from_font = level, text, from_font


class Para:
    __slots__ = ("lines", "marker", "list_level", "marker_x0", "first_x0", "rest_x0")

    def __init__(self, text, marker=None, list_level=0, x0=0.0):
        self.lines = [text]
        self.marker, self.list_level = marker, list_level
        self.marker_x0 = self.first_x0 = x0
        self.rest_x0 = None


class Code:
    __slots__ = ("lines",)  # [indent_cols, text, blank_lines_before]

    def __init__(self):
        self.lines = []


class Pre:
    __slots__ = ("lines",)

    def __init__(self, lines):
        self.lines = list(lines)


class PageMark:
    __slots__ = ("label",)

    def __init__(self, label):
        self.label = label


@dataclass
class Options:
    width: int = 100
    code_style: str = "fence"
    pages: str = None
    page_markers: bool = False
    contents: bool = True
    contents_depth: int = 3
    strip_headers: bool = True
    columns: str = "auto"
    tables: bool = False
    figures: bool = False
    ocr: bool = False
    ocr_lang: str = "eng"
    password: str = None
    split_chapters: bool = False
    use_bookmarks: bool = True
    fix_code_quotes: bool = True
    mark_inline_code: bool = False
    quiet: bool = False


# --------------------------------------------------------------------------- #
#  Small helpers
# --------------------------------------------------------------------------- #
def pct(sorted_vals, q):
    if not sorted_vals:
        return 0.0
    k = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[k]


def mode_of(values, ndigits=1, default=None):
    if not values:
        return default
    return Counter(round(v, ndigits) for v in values).most_common(1)[0][0]


def norm_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def clean_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def log(msg, opts, end="\n"):
    if not opts.quiet:
        print(msg, file=sys.stderr, end=end, flush=True)


# --------------------------------------------------------------------------- #
#  Low level extraction: PyMuPDF rawdict -> Seg / Line
# --------------------------------------------------------------------------- #
def make_seg(span, mono_fonts=None):
    chars = span.get("chars") or []
    if not chars:
        return None
    font = (span.get("font") or "").split("+")[-1]
    flags = span.get("flags", 0)
    mono = bool(flags & 8) or bool(MONO_FONT_RE.search(font)) or (mono_fonts is not None and font in mono_fonts)
    bold = bool(flags & 16) or bool(BOLD_FONT_RE.search(font))
    size = float(span.get("size", 0)) or 1.0

    items = []  # (x_start, x_end, char)
    for ch in chars:
        c = sanitize_text(ch["c"].translate(CHAR_TRANS))
        if not c:
            continue
        ox = ch["origin"][0]
        bx1 = ch["bbox"][2]
        items.append((ox, max(bx1, ox), c))
    # trim whitespace on both ends
    while items and items[0][2].isspace():
        items.pop(0)
    while items and items[-1][2].isspace():
        items.pop()
    if not items:
        return None

    widths = [x1 - x0 for x0, x1, c in items if not c.isspace() and len(c) == 1 and x1 > x0]
    cw = statistics.median(widths) if widths else size * 0.6
    if cw <= 0.05 * size:
        cw = size * 0.6

    if mono:
        # Rebuild the string from character positions -> exact internal spacing.
        ref = items[0][0]
        out, col = [], 0
        for x0, _x1, c in items:
            if c.isspace():
                continue
            target = int(round((x0 - ref) / cw))
            if target > col:
                out.append(" " * (target - col))
                col = target
            out.append(c)
            col += len(c)
        text = "".join(out)
    else:
        text = re.sub(r" {2,}", " ", "".join(c for _a, _b, c in items))

    text = sanitize_text(text)
    min_x = min(min(ox, bx1) for ox, bx1, _c in items)
    max_x = max(max(ox, bx1) for ox, bx1, _c in items)
    return Seg(min_x, max_x, text, size, mono, bold, cw, span["origin"][1])


def extract_raw_lines(page, textpage=None, mono_fonts=None):
    d = page.get_text("rawdict", flags=TEXT_FLAGS, textpage=textpage)
    out = []
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            dx, dy = ln.get("dir", (1, 0))
            if abs(dy) > 0.10:      # rotated / vertical text (watermarks, side tabs)
                continue
            segs = [s for s in (make_seg(sp, mono_fonts) for sp in ln.get("spans", [])) if s]
            if not segs:
                continue
            bb = ln["bbox"]
            out.append(Line(segs, bb[1], bb[3]))
    return out


def merge_line_group(group):
    is_rtl = any(is_rtl_text(s.text) for ln in group for s in ln.segs)
    if is_rtl:
        sorted_group = sorted(group, key=lambda l: -l.x1)
        segs = []
        for ln in sorted_group:
            segs.extend(ln.segs)
    else:
        segs = []
        for ln in group:
            segs.extend(ln.segs)
        segs.sort(key=lambda s: s.x0)
    # remove duplicated text (fake-bold PDFs draw the same text twice)
    dedup = []
    for s in segs:
        if dedup and s.text == dedup[-1].text and abs(s.x0 - dedup[-1].x0) < 1.5:
            continue
        dedup.append(s)
    return Line(dedup, min(l.y0 for l in group), max(l.y1 for l in group))


def merge_baselines(lines):
    """Combine fragments that sit on the same visual line, return top-to-bottom order."""
    if not lines:
        return []
    lines = sorted(lines, key=lambda l: (l.yc, l.x0))
    groups = []
    for ln in lines:
        if groups:
            g = groups[-1]
            ref = g[0]
            tol = 0.45 * max(ln.h, ref.h)
            if abs(ln.yc - ref.yc) < tol and all(ln.x0 >= o.x1 - 1.0 or ln.x1 <= o.x0 + 1.0 for o in g):
                g.append(ln)
                continue
        groups.append([ln])
    merged = [merge_line_group(g) if len(g) > 1 else g[0] for g in groups]
    merged.sort(key=lambda l: (l.y0, l.x0))
    return merged


def render_table(rows, maxw=40):
    rows = [[clean_ws(str(c)) if c is not None else "" for c in r] for r in rows]
    ncols = max(len(r) for r in rows)
    rows = [r + [""] * (ncols - len(r)) for r in rows]
    keep = [i for i in range(ncols) if any(r[i] for r in rows)]
    rows = [[r[i] for i in keep] for r in rows]
    if not rows or not rows[0]:
        return []
    widths = [max(3, min(maxw, max(len(r[i]) for r in rows))) for i in range(len(rows[0]))]
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    hsep = "+" + "+".join("=" * (w + 2) for w in widths) + "+"
    out = [sep]
    for ri, r in enumerate(rows):
        cells = [textwrap.wrap(c, w, break_long_words=True) or [""] for c, w in zip(r, widths)]
        for k in range(max(len(c) for c in cells)):
            out.append("| " + " | ".join((c[k] if k < len(c) else "").ljust(w)
                                         for c, w in zip(cells, widths)) + " |")
        out.append(hsep if ri == 0 and len(rows) > 1 else sep)
    return out


# --------------------------------------------------------------------------- #
#  The extractor
# --------------------------------------------------------------------------- #
class BookExtractor:
    def __init__(self, path: Path, opts: Options):
        self.path = Path(path)
        self.opts = opts
        self.pages: list[PageData] = []
        self.warnings: list[str] = []
        self.toc_entries = []
        self.toc_used = False
        self.toc_max = 0
        self.vocab = set()
        self.mono_fonts = set()

    # ------------------------------------------------------------------ #
    def run(self):
        opts = self.opts
        doc = pymupdf.open(self.path)
        if doc.needs_pass:
            if not doc.authenticate(opts.password or ""):
                raise RuntimeError("PDF is password protected - use --password")
        self.doc = doc
        n = doc.page_count
        if opts.pages:
            indices = parse_page_ranges(opts.pages, n)
        else:
            indices = list(range(n))
        if not indices:
            raise RuntimeError("No pages selected")

        t0 = time.time()
        self._profile_document_fonts()
        self._read_pages(indices)
        self._compute_font_stats()
        if opts.strip_headers:
            self._remove_headers_footers()
        for pd in self.pages:
            self._order_page(pd)
            for ln in pd.lines:
                self._classify(ln)
            self._insert_blocks(pd)
        self._compute_layout_stats()
        self._build_vocab()
        if opts.use_bookmarks:
            self._apply_bookmarks()
        self._assign_font_heading_levels()
        page_elements = [(pd, self._build_page_elements(pd)) for pd in self.pages]
        elements = self._assemble(page_elements)
        elements = self._post_process(elements)
        self.elements = elements
        self.elapsed = time.time() - t0
        return elements

    # ------------------------------------------------------------------ #
    def _profile_document_fonts(self):
        """Pre-screen document fonts and detect monospaced fonts by name and character metrics."""
        mono_fonts = set()
        prop_fonts = set()
        font_widths = defaultdict(dict)
        sample_pages = min(25, len(self.doc))
        for pno in range(sample_pages):
            p = self.doc[pno]
            d = p.get_text("rawdict", flags=TEXT_FLAGS)
            for b in d.get("blocks", []):
                if b.get("type") != 0:
                    continue
                for l in b.get("lines", []):
                    for s in l.get("spans", []):
                        fn = (s.get("font") or "").split("+")[-1]
                        flags = s.get("flags", 0)
                        if flags & 8 or MONO_FONT_RE.search(fn):
                            mono_fonts.add(fn)
                            continue
                        if fn in mono_fonts or fn in prop_fonts:
                            continue
                        sz = float(s.get("size", 1)) or 1.0
                        for c in s.get("chars", []):
                            ch = c["c"]
                            if ch.isalpha() and ch not in font_widths[fn]:
                                font_widths[fn][ch] = round((c["bbox"][2] - c["origin"][0]) / sz, 3)
        for fn, w in font_widths.items():
            if "i" in w and "m" in w and abs(w["i"] - w["m"]) < 0.05:
                mono_fonts.add(fn)
            elif "l" in w and "w" in w and abs(w["l"] - w["w"]) < 0.05:
                mono_fonts.add(fn)
            elif len(w) >= 8 and len(set(round(v, 2) for v in w.values())) <= 2:
                mono_fonts.add(fn)
            else:
                prop_fonts.add(fn)
        self.mono_fonts = mono_fonts

    # ------------------------------------------------------------------ #
    def _read_pages(self, indices):
        opts, doc = self.opts, self.doc
        total = len(indices)
        ocr_failed = False
        for k, pno in enumerate(indices, 1):
            page = doc[pno]
            if k == 1 or k % 10 == 0 or k == total:
                log(f"\r  Reading page {k}/{total} ...", opts, end="")
            try:
                label = page.get_label() or str(pno + 1)
            except Exception:
                label = str(pno + 1)
            W, H = page.rect.width, page.rect.height
            lines = extract_raw_lines(page, mono_fonts=self.mono_fonts)
            scanned = False
            if not lines or sum(l.nchars for l in lines) < 15:
                if page.get_images(full=False):
                    scanned = True
                    if opts.ocr and not ocr_failed:
                        try:
                            tp = page.get_textpage_ocr(flags=TEXT_FLAGS, language=opts.ocr_lang, dpi=300, full=True)
                            lines = extract_raw_lines(page, textpage=tp, mono_fonts=self.mono_fonts)
                            scanned = False
                        except Exception as exc:  # tesseract missing etc.
                            ocr_failed = True
                            self.warnings.append(f"OCR unavailable ({exc}); scanned pages were skipped.")
            # garbled text check (fonts without unicode mapping)
            allchars = "".join(s.text for l in lines for s in l.segs)
            bad = sum(1 for c in allchars if c == "\ufffd" or "\ue000" <= c <= "\uf8ff")
            garbled = len(allchars) > 40 and bad / len(allchars) > 0.15

            blocks = []
            if opts.tables and lines:
                try:
                    for t in page.find_tables().tables:
                        data = t.extract()
                        if len(data) < 2 or max(len(r) for r in data) < 2:
                            continue
                        r = pymupdf.Rect(t.bbox)
                        rendered = render_table(data)
                        if not rendered:
                            continue
                        lines = [l for l in lines if not r.contains(pymupdf.Point((l.x0 + l.x1) / 2, l.yc))]
                        blocks.append((r.y0, r.x0, "table", rendered))
                except Exception:
                    pass
            if opts.figures:
                try:
                    for info in page.get_image_info():
                        r = pymupdf.Rect(info["bbox"])
                        if r.width > 0.2 * W and r.height > 0.08 * H and not scanned:
                            blocks.append((r.y0, r.x0, "figure", ["[Figure]"]))
                except Exception:
                    pass
            self.pages.append(PageData(pno, label, W, H, lines, blocks, scanned, garbled))
        log("", opts)
        sc = sum(p.scanned for p in self.pages)
        gb = sum(p.garbled for p in self.pages)
        if sc:
            self.warnings.append(f"{sc} page(s) look scanned (image only, no text layer)."
                                 + ("" if opts.ocr else " Use --ocr to OCR them (needs Tesseract)."))
        if gb:
            self.warnings.append(f"{gb} page(s) contain garbled text (fonts without Unicode mapping);"
                                 " --ocr may give better results for those.")

    # ------------------------------------------------------------------ #
    def _compute_font_stats(self):
        body, mono = Counter(), Counter()
        for pd in self.pages:
            for l in pd.lines:
                for s in l.segs:
                    (mono if s.mono else body)[round(s.size * 2) / 2] += s.nchars
        self.body_size = body.most_common(1)[0][0] if body else (mono.most_common(1)[0][0] if mono else 10.0)
        self.mono_size = mono.most_common(1)[0][0] if mono else self.body_size
        self.has_mono = sum(mono.values()) > 0

    # ------------------------------------------------------------------ #
    def _remove_headers_footers(self):
        zone = 0.12
        seen = defaultdict(set)
        top_y_counts = Counter()
        bot_y_counts = Counter()

        def key(t):
            return re.sub(r"\s+", " ", re.sub(r"\d+", "#", t.lower())).strip()

        def in_zone(l, pd):
            return l.y1 < pd.height * zone or l.y0 > pd.height * (1 - zone)

        for i, pd in enumerate(self.pages):
            for l in pd.lines:
                if in_zone(l, pd) and l.mono_ratio < 0.8:
                    seen[key(l.plain)].add(i)
                    if l.y1 < pd.height * zone:
                        top_y_counts[round(l.y1, 0)] += 1
                    elif l.y0 > pd.height * (1 - zone):
                        bot_y_counts[round(l.y0, 0)] += 1

        min_rep = 3 if len(self.pages) >= 6 else 10 ** 9
        repeating = {k for k, v in seen.items() if len(v) >= min_rep and re.search(r"[a-z]", k)}

        min_band = max(4, int(len(self.pages) * 0.05)) if len(self.pages) >= 8 else 10 ** 9
        top_bands = {y for y, c in top_y_counts.items() if c >= min_band}
        bot_bands = {y for y, c in bot_y_counts.items() if c >= min_band}

        for pd in self.pages:
            kept = []
            for l in pd.lines:
                if in_zone(l, pd) and l.mono_ratio < 0.8:
                    t = l.plain.strip()
                    if PAGE_NUM_RE.match(t) and l.size <= self.body_size * 1.25:
                        continue
                    if key(t) in repeating:
                        continue
                    if l.size <= self.body_size * 1.15:
                        if any(abs(round(l.y1, 0) - ty) <= 3.0 for ty in top_bands):
                            continue
                        if any(abs(round(l.y0, 0) - by) <= 3.0 for by in bot_bands):
                            continue
                kept.append(l)
            pd.lines = kept

    # ------------------------------------------------------------------ #
    def _order_page(self, pd: PageData):
        """Detect columns, order lines in reading order, merge same-baseline fragments."""
        lines = pd.lines
        body = self.body_size
        # pull out drop caps (huge single initial letter)
        dropcaps = [l for l in lines if len(l.plain) == 1 and l.plain.isalpha() and l.size >= body * 1.8]
        for d in dropcaps:
            d.dropcap = True
        lines = [l for l in lines if not l.dropcap]

        mode = self.opts.columns
        ordered = None
        if mode != "1" and len(lines) >= 8:
            W = pd.width
            best = None
            for x in range(int(W * 0.30), int(W * 0.70), 2):
                crossing = sum(1 for l in lines if l.x0 < x - 1 and l.x1 > x + 1)
                score = (crossing, abs(x - W / 2))
                if best is None or score < best[0]:
                    best = (score, x)
            gx = best[1]
            left = [l for l in lines if l.x1 <= gx + 1]
            right = [l for l in lines if l.x0 >= gx - 1]
            full = [l for l in lines if l not in left and l not in right]
            ok = False
            if len(left) >= 4 and len(right) >= 4 and len(full) <= 0.25 * len(lines):
                mono_right = sum(1 for l in right if l.mono_ratio >= 0.8) / len(right)
                overlap = sum(1 for r in right if any(a.y0 < r.y1 and r.y0 < a.y1 for a in left)) / len(right)
                ok = mode == "2" or (mono_right < 0.4 and overlap >= 0.5
                                     and statistics.median(l.x1 - l.x0 for l in left) > W * 0.2
                                     and statistics.median(l.x1 - l.x0 for l in right) > W * 0.2)
            if ok:
                pd.ncols, pd.gutter = 2, gx
                full.sort(key=lambda l: l.y0)
                ordered, prev_y = [], -1e9
                bounds = [l.y0 for l in full] + [1e9]
                fi = 0
                for b in bounds:
                    band_l = [l for l in left if prev_y <= l.yc < b]
                    band_r = [l for l in right if prev_y <= l.yc < b]
                    for l in band_l:
                        l.col = 0
                    for l in band_r:
                        l.col = 1
                    ordered += merge_baselines(band_l) + merge_baselines(band_r)
                    if fi < len(full):
                        fl = full[fi]
                        fl.col = 0
                        ordered.append(fl)
                        fi += 1
                    prev_y = b
                # merged lines lost col attribute -> recompute
                for l in ordered:
                    l.col = 1 if l.x0 >= gx - 1 else 0
        if ordered is None:
            ordered = merge_baselines(lines)
            for l in ordered:
                l.col = 0
        # re-insert drop caps before the first line they overlap
        for d in dropcaps:
            d.kind = "dropcap"
            idx = len(ordered)
            for i, l in enumerate(ordered):
                if l.y1 > d.y0 and l.x0 > d.x0:
                    idx = i
                    break
            d.col = ordered[idx].col if idx < len(ordered) else 0
            ordered.insert(idx, d)
        pd.lines = ordered

    # ------------------------------------------------------------------ #
    def _compose(self, ln: Line, code: bool) -> str:
        out, prev = [], None
        mark = self.opts.mark_inline_code and not code
        in_code = False
        is_rtl = any(is_rtl_text(s.text) for s in ln.segs)
        for s in ln.segs:
            if prev is not None:
                gap = (prev.x0 - s.x1) if is_rtl else (s.x0 - prev.x1)
                if code:
                    w = ln.cw or s.cw or s.size * 0.6
                    n = int(round(gap / w)) if gap > 0 else 0
                    if n == 0 and gap > 0.3 * w:
                        n = 1
                    out.append(" " * n)
                else:
                    if gap > 2.5 * max(s.size, prev.size):
                        ln.tabular = True
                        out.append("\x00")
                    elif gap > 0.12 * min(s.size, prev.size) or is_rtl:
                        out.append(" ")
            if mark:
                if s.mono and not in_code:
                    out.append("`")
                    in_code = True
                elif not s.mono and in_code:
                    # put closing tick before the separating space
                    if out and out[-1] == " ":
                        out.insert(len(out) - 1, "`")
                    else:
                        out.append("`")
                    in_code = False
            out.append(s.text)
            prev = s
        if mark and in_code:
            out.append("`")
        text = "".join(out)
        if code:
            return sanitize_text(text.rstrip())
        text = re.sub(r" {2,}", " ", text).strip()
        if LEADER_RE.search(text) and re.search(r"(?:\.\s*){4,}\s*\S{1,8}$", text):
            ln.tabular = True
            text = LEADER_RE.sub(" .... ", text)
        return sanitize_text(text.replace("\x00", "    "))

    def _classify(self, ln: Line):
        if ln.kind == "dropcap":
            ln.text = ln.plain
            return
        body = self.body_size
        plain = clean_ws(ln.plain)
        is_mono = ln.mono_ratio >= 0.8
        head_ref = max(body, self.mono_size) if is_mono else body
        if ln.size >= head_ref * 1.15 and len(plain) <= 200 and re.search(r"\w", plain):
            ln.kind = "heading"
            ln.text = plain
        elif is_mono:
            ln.kind = "code"
            ln.text = self._compose(ln, code=True)
        else:
            ln.kind = "text"
            ln.text = self._compose(ln, code=False)

    def _insert_blocks(self, pd: PageData):
        for y0, x0, kind, text in sorted(pd.blocks):
            fake = Line([Seg(x0, x0 + 1, "x", self.body_size, False, False, 1, y0)], y0, y0 + 1)
            fake.kind, fake.block = kind, text
            col = 1 if pd.ncols == 2 and x0 >= pd.gutter - 1 else 0
            fake.col = col
            idx = len(pd.lines)
            for i, l in enumerate(pd.lines):
                if l.col == col and l.y0 >= y0:
                    idx = i
                    break
            pd.lines.insert(idx, fake)

    # ------------------------------------------------------------------ #
    def _compute_layout_stats(self):
        body = self.body_size
        bp, cp = [], []
        acc = defaultdict(lambda: ([], []))
        for pd in self.pages:
            ls = pd.lines
            for a, b in zip(ls, ls[1:]):
                if a.col != b.col:
                    continue
                d = b.base - a.base
                if a.kind == b.kind == "text" and abs(a.size - body) < 0.6 and abs(b.size - body) < 0.6 \
                        and 0 < d < 3 * body:
                    bp.append(d)
                elif a.kind == b.kind == "code" and 0 < d < 3 * max(a.size, b.size):
                    cp.append(d)
            # column metrics
            pd.colm = {}
            bycol = defaultdict(list)
            for l in ls:
                if l.kind == "text":
                    bycol[l.col].append(l)
            for col, lst in bycol.items():
                xs0 = sorted(l.x0 for l in lst)
                xs1 = sorted(l.x1 for l in lst)
                k = (pd.pno % 2, col, pd.ncols)
                acc[k][0].extend(xs0)
                acc[k][1].extend(xs1)
                if len(lst) >= 8:
                    pd.colm[col] = (pct(xs0, 0.15), pct(xs1, 0.85))
        self.body_pitch = mode_of(bp, 1, body * 1.2)
        self.code_pitch = mode_of(cp, 1, self.mono_size * 1.2)
        docm = {k: (pct(sorted(a), 0.15), pct(sorted(b), 0.85)) for k, (a, b) in acc.items() if a}
        for pd in self.pages:
            for col in {l.col for l in pd.lines}:
                if col in pd.colm:
                    continue
                k = (pd.pno % 2, col, pd.ncols)
                if k in docm:
                    pd.colm[col] = docm[k]
                else:
                    cl = [l for l in pd.lines if l.col == col] or pd.lines
                    pd.colm[col] = (min(l.x0 for l in cl), max(l.x1 for l in cl))

    def _build_vocab(self):
        vocab = set()
        for pd in self.pages:
            for l in pd.lines:
                if l.kind != "text":
                    continue
                words = WORD_RE.findall(l.text)
                for w in words[:-1]:
                    vocab.add(w.lower())
        self.vocab = vocab

    # ------------------------------------------------------------------ #
    #  Bookmarks (PDF outline) -> authoritative chapter / section headings
    # ------------------------------------------------------------------ #
    def _heading_like(self, l: Line) -> bool:
        return l.kind == "heading" or l.bold_ratio >= 0.8 or l.size >= self.body_size * 1.08

    def _similarity(self, nw, nt, heading_like):
        if nw == nt:
            return 1.0
        sm = difflib.SequenceMatcher(None, nw, nt, autojunk=False)
        r = sm.ratio() if sm.real_quick_ratio() >= 0.6 and sm.quick_ratio() >= 0.6 else 0.0
        if heading_like and len(nw) >= 3:
            lo, hi = sorted((len(nw), len(nt)))
            if lo / hi >= 0.35 and (nt.endswith(nw) or nw.endswith(nt) or nt.startswith(nw)):
                r = max(r, 0.86)
        return r

    def _match_title(self, pd: PageData, title: str):
        nt = norm_key(title)
        if not nt:
            return None
        lines = pd.lines
        n = len(lines)
        skip = ("absorbed", "toc_heading", "table", "figure", "code", "dropcap")
        best = None
        for i in range(n):
            if lines[i].kind in skip:
                continue
            texts, hl = [], True
            for k in range(3):
                j = i + k
                if j >= n or lines[j].kind in skip:
                    break
                texts.append(lines[j].text or lines[j].plain)
                hl = hl and self._heading_like(lines[j])
                if k > 0 and not hl:
                    break
                nw = norm_key(" ".join(texts))
                if not nw:
                    continue
                if len(nw) > 1.5 * len(nt) + 8:
                    break
                score = self._similarity(nw, nt, hl)
                # Penalize lines in the top margin with body font size (likely running headers)
                if lines[i].y1 < pd.height * 0.10 and lines[i].size <= self.body_size * 1.15:
                    score *= 0.5
                thr = 0.80 if hl else 0.93
                if score >= thr and (best is None or score > best[0] + 1e-9):
                    best = (score, i, j)
            if best and best[0] >= 0.999:
                break
        return best

    def _apply_bookmarks(self):
        try:
            toc = self.doc.get_toc(simple=True)
        except Exception:
            toc = []
        page_map = {pd.pno: pd for pd in self.pages}
        entries = []
        for lvl, title, page in toc:
            title = sanitize_text(clean_ws(str(title)))
            if title and 1 <= page <= self.doc.page_count:
                entries.append([lvl, title, page - 1])
        if len(entries) < 2:
            return
        top_page_count = Counter(e[2] for e in entries).most_common(1)[0][1]
        if len(entries) > 4 and top_page_count > 0.6 * len(entries):
            return  # broken outline (everything points to the same page)
        # single wrapping root entry (e.g. the book title) -> promote children
        lv = Counter(e[0] for e in entries)
        minlv = min(lv)
        if lv[minlv] == 1 and len(entries) > 3:
            for e in entries:
                if e[0] > minlv:
                    e[0] -= 1
        base = min(e[0] for e in entries)
        for e in entries:
            e[0] = min(4, e[0] - base + 1)
        self.toc_entries = entries
        self.toc_used = True
        self.toc_max = max(e[0] for e in entries)

        for pd in self.pages:
            pd.toc_unmatched = []
        for lvl, title, pno in entries:
            pd = page_map.get(pno)
            if pd is None:
                continue
            m = self._match_title(pd, title)
            if m is None:
                pd.toc_unmatched.append((lvl, sanitize_text(title)))
                continue
            _score, i, j = m
            lines = pd.lines
            for t in range(i + 1, j + 1):
                lines[t].kind = "absorbed"
            # If the matched line on the page already contains the printed title
            # with a section prefix (e.g. "3.1 Function calls"), preserve the printed text!
            page_text = clean_ws(" ".join((lines[t].text or lines[t].plain) for t in range(i, j + 1)))
            if norm_key(title) in norm_key(page_text) and len(page_text) <= len(title) + 25:
                if re.match(r"^(?:\d{1,3}(?:\.\d{1,3})*|[A-Z]\.\d{1,3}|Chapter\s+\w+[:.]?)\s+", page_text, re.I):
                    title = page_text
            # absorb a short label right above it ("CHAPTER 3", "Part II", "3")
            nt = norm_key(title)
            t, absorbed = i - 1, 0
            while t >= 0 and absorbed < 2:
                lt = lines[t]
                lab = clean_ws(lt.plain)
                if lt.kind == "heading" and len(lab) <= 25 and (LABEL_RE.match(lab) or norm_key(lab) in nt):
                    lt.kind = "absorbed"
                    if norm_key(lab) not in nt:
                        lab = lab.rstrip(".:")
                        if re.fullmatch(r"\d{1,3}|[IVXLCivxlc]{1,6}", lab):
                            title = f"{lab}. {title}"            # bare number  -> "3. Functions"
                        else:
                            lab = lab.title() if lab.isupper() else lab
                            title = f"{lab}: {title}"            # "CHAPTER 3"  -> "Chapter 3: Functions"
                        nt = norm_key(title)
                    t -= 1
                    absorbed += 1
                else:
                    break
            lines[i].kind = "toc_heading"
            lines[i].level = lvl
            lines[i].text = sanitize_text(title)

    # ------------------------------------------------------------------ #
    def _assign_font_heading_levels(self):
        sizes = Counter()
        for pd in self.pages:
            for l in pd.lines:
                if l.kind == "heading":
                    sizes[round(l.size * 2) / 2] += 1
        clusters = []
        for s in sorted(sizes, reverse=True):
            if clusters and clusters[-1][0] - s <= 0.75:
                clusters[-1][1].append(s)
            else:
                clusters.append((s, [s]))
        level_of = {}
        offset = self.toc_max if self.toc_used else 0
        for idx, (_head, members) in enumerate(clusters):
            for s in members:
                level_of[s] = min(4, offset + idx + 1)
        for pd in self.pages:
            for l in pd.lines:
                if l.kind == "heading":
                    l.level = level_of.get(round(l.size * 2) / 2, 3)
                    if not self.toc_used and CHAPTER_RE.match(l.text):
                        l.level = 1

    # ------------------------------------------------------------------ #
    #  Page -> elements
    # ------------------------------------------------------------------ #
    def _list_marker(self, text, prev_line, prev_ends_par):
        m = STRONG_BULLET_RE.match(text)
        if m:
            return "•", text[m.end():]
        m = WEAK_BULLET_RE.match(text) or NUM_LIST_RE.match(text)
        if m and (prev_line is None or prev_ends_par):
            mk = m.group(1)
            return ("•" if mk in "-–—*" else mk), text[m.end():]
        return None, text

    def _expected_pitch(self, a, b):
        return self.body_pitch * (max(a.size, b.size) / self.body_size)

    def _is_break(self, a: Line, b: Line, cur: Para, pd: PageData) -> bool:
        if a is None or a.col != b.col or a.kind != "text":
            return True
        if a.tabular or b.tabular:
            return True
        d = b.base - a.base
        if d <= 0 or d > self._expected_pitch(a, b) * 1.15 + 0.3:
            return True
        if abs(a.size - b.size) > 1.2:
            return True
        em = self.body_size
        left, right = pd.colm.get(b.col, (a.x0, a.x1))
        width = max(right - left, 1.0)
        if cur.marker:
            if b.x0 > cur.marker_x0 + 0.3 * em:
                return False
            return bool(TERMINAL_RE.search(a.text))
        ends = bool(TERMINAL_RE.search(a.text))
        if a.x1 < right - 0.40 * width:
            return True
        if ends and a.x1 < right - 0.12 * width:
            return True
        if b.x0 > a.x0 + 0.8 * em and a.x0 < left + 0.5 * em:
            return True                                    # first-line indent of new paragraph
        if cur.rest_x0 is not None and b.x0 < cur.rest_x0 - 0.8 * em:
            return True                                    # end of an indented block
        return False

    def _build_page_elements(self, pd: PageData):
        els = []
        for lvl, title in (pd.toc_unmatched or []):
            els.append(Heading(lvl, title))
        lines = pd.lines
        cur = None            # Para | Code | Pre
        prev = None           # previous Line
        last_heading_line = None
        pending_drop = ""
        em = self.body_size

        def flush():
            nonlocal cur
            if cur is not None:
                els.append(cur)
                cur = None

        for idx, ln in enumerate(lines):
            k = ln.kind
            if k == "absorbed":
                continue
            if k == "dropcap":
                pending_drop = ln.text
                continue
            if k in ("table", "figure"):
                flush()
                els.append(Pre(ln.block))
                prev = None
                continue
            if k == "toc_heading":
                flush()
                els.append(Heading(ln.level, ln.text))
                prev, last_heading_line = ln, None
                continue
            if k == "heading":
                flush()
                h = els[-1] if els else None
                if (isinstance(h, Heading) and h.from_font and last_heading_line is not None
                        and prev is last_heading_line and ln.col == prev.col
                        and 0 < ln.y0 - prev.y1 < 1.5 * max(ln.size, prev.size)):
                    if h.level == ln.level:
                        h.text = f"{h.text} {ln.text}"
                    elif len(h.text) <= 25 or LABEL_RE.match(h.text):
                        lab = h.text.title() if h.text.isupper() and CHAPTER_RE.match(h.text) else h.text
                        h.text = f"{lab.rstrip('.:')}: {ln.text}"
                        h.level = min(h.level, ln.level)
                    else:
                        els.append(Heading(ln.level, ln.text, True))
                else:
                    els.append(Heading(ln.level, ln.text, True))
                prev = last_heading_line = ln
                continue
            if k == "code":
                left = pd.colm.get(ln.col, (ln.x0, ln.x1))[0]
                cw = ln.cw or self.mono_size * 0.6
                indent = max(0, int(round((ln.x0 - left) / cw)))
                if isinstance(cur, Code) and prev is not None and prev.kind == "code" and prev.col == ln.col:
                    d = ln.base - prev.base
                    ratio = d / self.code_pitch if self.code_pitch else 1
                    if 0 < ratio <= 3.6:
                        cur.lines.append([indent, ln.text, max(0, min(2, int(round(ratio)) - 1))])
                        prev = ln
                        continue
                flush()
                cur = Code()
                cur.lines.append([indent, ln.text, 0])
                prev = ln
                continue

            # ---- ordinary text line ----
            text = ln.text
            if pending_drop:
                text = pending_drop + text
                ln.text = text
                pending_drop = ""
            if not text:
                continue
            nxt = lines[idx + 1] if idx + 1 < len(lines) else None
            if ln.tabular:
                if isinstance(cur, Pre) and prev is not None and prev.tabular:
                    cur.lines.append(text)
                else:
                    flush()
                    cur = Pre([text])
                prev = ln
                continue

            # bold stand-alone line -> minor heading
            gap_above = prev is None or prev.kind != "text" or prev.col != ln.col or \
                (ln.base - prev.base) > self._expected_pitch(prev, ln) * 1.15 + 0.3
            if (ln.bold_ratio >= 0.95 and len(text) <= 90 and len(text.split()) <= 14
                    and not re.search(r"[.,;]$", text) and not text[0].islower()
                    and re.search(r"[A-Za-z]{2}", text) and gap_above
                    and not STRONG_BULLET_RE.match(text)
                    and not (nxt is not None and nxt.kind == "text" and nxt.bold_ratio >= 0.95
                             and nxt.col == ln.col and 0 < nxt.base - ln.base <= self._expected_pitch(ln, nxt) * 1.15 + 0.3)):
                flush()
                els.append(Heading(4, text, True))
                prev, last_heading_line = ln, None
                continue

            prev_ends = (cur is None or not isinstance(cur, Para) or prev is None or prev.kind != "text"
                         or bool(TERMINAL_RE.search(prev.text)) or cur.marker is not None
                         or self._is_break(prev, ln, Para(""), pd))
            marker, rest = self._list_marker(text, prev if isinstance(cur, Para) else None, prev_ends)
            if marker is not None:
                flush()
                left = pd.colm.get(ln.col, (ln.x0, ln.x1))[0]
                lvl = max(0, min(3, int(round((ln.x0 - left) / (2 * em)))))
                cur = Para(rest, marker, lvl, ln.x0)
            elif isinstance(cur, Para) and not self._is_break(prev, ln, cur, pd):
                cur.lines.append(text)
                if cur.rest_x0 is None:
                    cur.rest_x0 = ln.x0
            else:
                flush()
                cur = Para(text, None, 0, ln.x0)
            prev = ln
        flush()
        return els

    # ------------------------------------------------------------------ #
    def _assemble(self, page_elements):
        out = []
        for pd, els in page_elements:
            marker = PageMark(pd.label) if self.opts.page_markers else None
            last = next((e for e in reversed(out) if not isinstance(e, PageMark)), None)
            merged = False
            if els and last is not None:
                first = els[0]
                if isinstance(last, Code) and isinstance(first, Code):
                    first.lines[0][2] = 0
                    last.lines.extend(first.lines)
                    merged = True
                elif (isinstance(last, Para) and isinstance(first, Para) and first.marker is None
                      and last.lines and not TERMINAL_RE.search(last.lines[-1])
                      and not re.search(r"[\"\u201d]$", last.lines[-1])):
                    last.lines.extend(first.lines)
                    merged = True
            if merged:
                els = els[1:]
            if marker:
                out.append(marker)
            out.extend(els)
        return out

    def _post_process(self, els):
        result = []
        for i, e in enumerate(els):
            if isinstance(e, Heading):
                e.text = clean_ws(e.text)
                if len(e.text) > 250:                  # large-print paragraph, not a heading
                    result.append(Para(e.text))
                    continue
                if e.from_font and re.fullmatch(r"[\divxlcIVXLC.:\-–— ]{1,5}", e.text):
                    continue                             # decorative chapter number alone
                if not e.text:
                    continue
            if isinstance(e, Code):
                if not any(t.strip() for _i, t, _b in e.lines):
                    continue
            result.append(e)
        return result

    # ------------------------------------------------------------------ #
    #  Rendering
    # ------------------------------------------------------------------ #
    def _join_para(self, lines):
        out = ""
        for nxt in lines:
            nxt = nxt.strip()
            if not nxt:
                continue
            if not out:
                out = nxt
                continue
            if is_rtl_text(out) or is_rtl_text(nxt):
                out = out + " " + nxt
                continue
            if out.endswith("\u00ad"):
                out = out[:-1] + nxt
                continue
            m = re.search(r"([A-Za-z]+)-$", out)
            n = re.match(r"([a-z]+)", nxt)
            if m and n and not out.endswith("--"):
                a, b = m.group(1).lower(), n.group(1).lower()
                if f"{a}-{b}" in self.vocab and f"{a}{b}" not in self.vocab:
                    out = out + nxt                      # genuine compound word: keep hyphen
                else:
                    out = out[:-1] + nxt                 # soft line-break hyphen: remove
            elif re.search(r"(?:https?://|www\.)\S*[/._-]$", out) or out.endswith(("\u2014", "/")):
                out = out + nxt
            else:
                out = out + " " + nxt
        return sanitize_text(out.replace("\u00ad", ""))

    def render(self, elements, include_header=True):
        opts = self.opts
        W = opts.width if opts.width and opts.width > 0 else 0
        rule = W or 80
        out: list[str] = []

        def blank(n):
            if not out:
                return
            k = 0
            for s in reversed(out):
                if s == "":
                    k += 1
                else:
                    break
            out.extend([""] * max(0, n - k))

        def wrap(text, init="", sub=""):
            if not W:
                return [init + text]
            return textwrap.wrap(text, W, initial_indent=init, subsequent_indent=sub,
                                 break_long_words=False, break_on_hyphens=False) or [init]

        if include_header:
            out += self._header_block(rule)

        prev = None
        for e in elements:
            if isinstance(e, Heading):
                lines = wrap(e.text)
                if e.level == 1:
                    blank(3)
                    out += ["=" * rule] + lines + ["=" * rule]
                elif e.level == 2:
                    blank(2)
                    out += lines + ["-" * min(rule, max(len(x) for x in lines))]
                elif e.level == 3:
                    blank(2)
                    out += lines + ["~" * min(rule, max(len(x) for x in lines))]
                else:
                    blank(1)
                    out += wrap(e.text, "» ", "  ")
                blank(1)
            elif isinstance(e, Para):
                text = self._join_para(e.lines)
                if not text:
                    continue
                if e.marker:
                    if not (isinstance(prev, Para) and prev.marker):
                        blank(1)
                    pad = "    " * e.list_level
                    init = f"{pad}{e.marker} "
                    out += wrap(text, init, " " * len(init))
                else:
                    blank(1)
                    out += wrap(text)
                    blank(1)
            elif isinstance(e, Code):
                rows = [(i, t) for i, t, _b in e.lines]
                base = min((i for i, t in rows if t.strip()), default=0)
                body = []
                for i, t, b in e.lines:
                    body += [""] * b
                    if opts.fix_code_quotes:
                        t = t.translate(CODE_TRANS)
                    body.append((" " * (i - base) + t).rstrip() if t.strip() else "")
                while body and not body[-1]:
                    body.pop()
                blank(1)
                if opts.code_style == "fence":
                    out += ["```"] + body + ["```"]
                elif opts.code_style == "indent":
                    out += [("    " + x) if x else "" for x in body]
                else:
                    out += body
                blank(1)
            elif isinstance(e, Pre):
                blank(1)
                out += [x.rstrip() for x in e.lines]
                blank(1)
            elif isinstance(e, PageMark):
                blank(1)
                out.append(f"[Page {e.label}]")
                blank(1)
            prev = e

        text = "\n".join(x.rstrip() for x in out)
        text = re.sub(r"\n{5,}", "\n\n\n\n", text)
        return sanitize_text(text.strip("\n") + "\n")

    def _find_cover_title(self) -> str | None:
        """Scan first 4 pages for dominant headline text on the book cover/title page."""
        candidates = []
        for pno in range(min(4, len(self.doc))):
            d = self.doc[pno].get_text("rawdict", flags=TEXT_FLAGS)
            for b in d.get("blocks", []):
                if b.get("type") != 0:
                    continue
                for l in b.get("lines", []):
                    txt = " ".join("".join(c["c"] for c in s.get("chars", [])) for s in l.get("spans", [])).strip()
                    txt = clean_ws(txt)
                    if not txt or len(txt) < 3 or len(txt) > 120:
                        continue
                    if re.search(r"copyright|isbn|all rights|version|http|\.com|edition|license", txt, re.I):
                        continue
                    sz = max((float(s.get("size", 0)) for s in l.get("spans", [])), default=0)
                    candidates.append((sz, pno, l["bbox"][1], txt))
        if not candidates:
            return None
        candidates.sort(key=lambda c: (-c[0], c[1], c[2]))
        top_sz = candidates[0][0]
        title = candidates[0][3]
        top_pno = candidates[0][1]
        top_y = candidates[0][2]
        for sz, pno, y, txt in candidates[1:]:
            if pno == top_pno and 0 < y - top_y < 80 and sz >= top_sz * 0.45 and sz >= 14:
                if not re.search(r"^by\b|^author\b", txt, re.I) and len(title) + len(txt) < 90:
                    title = f"{title}: {txt}"
                break
        return sanitize_text(title) if title else None

    def book_title(self):
        meta = self.doc.metadata or {}
        t = sanitize_text(clean_ws(meta.get("title") or ""))
        stem_norm = norm_key(self.path.stem)
        t_norm = norm_key(t)
        is_bad = (
            not t or len(t) < 3 or
            (t.isupper() and len(t) <= 25) or
            t_norm == stem_norm or
            re.search(r"untitled|microsoft word|^document\d*$|\.(indd|docx?|pdf|tex|qxd|dvi)$", t, re.I)
        )
        if is_bad:
            cover = self._find_cover_title()
            if cover:
                return cover
            t = re.sub(r"[_]+", " ", self.path.stem).strip()
        return sanitize_text(t)

    def _header_block(self, rule):
        meta = self.doc.metadata or {}
        lines = ["=" * rule]
        lines += textwrap.wrap(self.book_title().upper(), rule) or [""]
        author = sanitize_text(clean_ws(meta.get("author") or ""))
        if author:
            lines.append(f"by {author}")
        lines.append("=" * rule)
        lines.append(f"Source file : {self.path.name}")
        lines.append(f"Pages       : {len(self.pages)}" + (f" (of {self.doc.page_count})" if len(self.pages) != self.doc.page_count else ""))
        lines.append(f"Extracted   : {datetime.now():%Y-%m-%d %H:%M} with pdf_book_to_text.py v{__version__}")
        lines.append("=" * rule)
        if self.opts.contents:
            toc = self._contents_lines(rule)
            if toc:
                lines += ["", "", "TABLE OF CONTENTS", "-----------------", ""] + toc + ["", "=" * rule]
        return lines

    def _contents_lines(self, rule):
        depth = self.opts.contents_depth
        items = []
        if self.toc_used:
            labels = {pd.pno: pd.label for pd in self.pages}
            for lvl, title, pno in self.toc_entries:
                if lvl <= depth and pno in labels:
                    items.append((lvl, title, labels[pno]))
        else:
            for e in self.elements:
                if isinstance(e, Heading) and e.level <= min(depth, 2):
                    items.append((e.level, e.text, ""))
        out = []
        for lvl, title, page in items:
            ind = "    " * (lvl - 1)
            left = f"{ind}{title}"
            if page:
                room = rule - len(left) - len(page) - 2
                if room >= 3:
                    out.append(f"{left} {'.' * room} {page}")
                else:
                    out.append(f"{left}  {page}")
            else:
                out.append(left)
        return out

    def chapters(self):
        """Split elements at level-1 headings -> list of (title, elements)."""
        parts, cur_title, cur = [], "Front Matter", []
        for e in self.elements:
            if isinstance(e, Heading) and e.level == 1:
                if any(not isinstance(x, PageMark) for x in cur):
                    parts.append((cur_title, cur))
                cur_title, cur = e.text, [e]
            else:
                cur.append(e)
        if any(not isinstance(x, PageMark) for x in cur):
            parts.append((cur_title, cur))
        return parts

    def stats(self):
        c = Counter(type(e).__name__ for e in self.elements)
        return c


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
def parse_page_ranges(spec: str, n: int):
    pages = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            a = int(a) if a.strip() else 1
            b = int(b) if b.strip() else n
        else:
            a = b = int(part)
        pages.update(range(max(1, a) - 1, min(n, b)))
    return sorted(pages)


def safe_filename(s: str, maxlen=70) -> str:
    s = sanitize_text(s)
    s = re.sub(r"[\\/:*?\"<>|\x00-\x1f\ufffd]+", " ", s)
    return clean_ws(s)[:maxlen].rstrip(". ") or "untitled"


def process_file(pdf: Path, out_path: Path | None, opts: Options) -> bool:
    log(f"\n📖  {pdf}", opts)
    try:
        ex = BookExtractor(pdf, opts)
        ex.run()
    except Exception as exc:
        log(f"  ✗ Failed: {exc}", opts)
        return False

    if out_path is None:
        out_path = pdf.with_suffix(".txt")
    elif out_path.is_dir():
        out_path = out_path / (pdf.stem + ".txt")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    text = ex.render(ex.elements)
    out_path.write_text(text, encoding="utf-8-sig")

    if opts.split_chapters:
        folder = out_path.with_name(out_path.stem + "_chapters")
        folder.mkdir(exist_ok=True)
        for i, (title, els) in enumerate(ex.chapters()):
            fn = folder / f"{i:02d} - {safe_filename(title)}.txt"
            fn.write_text(ex.render(els, include_header=False), encoding="utf-8-sig")
        log(f"  ✓ Chapters written to: {folder}", opts)

    st = ex.stats()
    log(f"  ✓ Saved: {out_path}", opts)
    log(f"    {len(ex.pages)} pages | {st.get('Heading', 0)} headings | {st.get('Para', 0)} paragraphs | "
        f"{st.get('Code', 0)} code blocks | {len(text):,} chars | "
        f"headings from {'PDF bookmarks' if ex.toc_used else 'font analysis'} | {ex.elapsed:.1f}s", opts)
    for w in ex.warnings:
        log(f"  ⚠ {w}", opts)
    ex.doc.close()
    return True


def build_arg_parser():
    p = argparse.ArgumentParser(
        prog="pdf_book_to_text.py",
        description="Extract clean, structured text (chapters, paragraphs, code listings) from PDF books.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  %(prog)s book.pdf\n"
               "  %(prog)s book.pdf -o book.txt --width 0\n"
               "  %(prog)s ~/Books -r --split-chapters\n"
               "  %(prog)s scanned.pdf --ocr --ocr-lang eng\n",
    )
    p.add_argument("inputs", nargs="*", help="PDF file(s) or folder(s). If omitted you will be asked.")
    p.add_argument("-o", "--output", help="Output .txt file (single input) or output folder.")
    p.add_argument("-r", "--recursive", action="store_true", help="Search folders recursively for PDFs.")
    p.add_argument("-w", "--width", type=int, default=100,
                   help="Wrap paragraphs at N characters (0 = one line per paragraph). Code is never wrapped. Default 100.")
    p.add_argument("--code-style", choices=["fence", "indent", "plain"], default="fence",
                   help="How code blocks are marked: ``` fences (default), 4-space indent, or plain.")
    p.add_argument("--pages", help="Page range(s) to extract, e.g. '1-50' or '10-20,25,40-'.")
    p.add_argument("--page-markers", action="store_true", help="Insert [Page N] markers.")
    p.add_argument("--no-contents", dest="contents", action="store_false", help="Do not write a table of contents at the top.")
    p.add_argument("--contents-depth", type=int, default=3, help="Depth of the generated table of contents (default 3).")
    p.add_argument("--keep-headers", dest="strip_headers", action="store_false",
                   help="Keep running headers/footers and page numbers.")
    p.add_argument("--columns", choices=["auto", "1", "2"], default="auto", help="Column layout detection (default auto).")
    p.add_argument("--tables", action="store_true", help="Detect ruled tables and render them as ASCII grids (slower).")
    p.add_argument("--figures", action="store_true", help="Insert [Figure] placeholders where images are.")
    p.add_argument("--ocr", action="store_true", help="OCR scanned pages (requires Tesseract installed).")
    p.add_argument("--ocr-lang", default="eng", help="Tesseract language(s), e.g. 'eng' or 'eng+deu'.")
    p.add_argument("--password", help="Password for encrypted PDFs.")
    p.add_argument("--split-chapters", action="store_true", help="Also write one .txt per chapter into <name>_chapters/.")
    p.add_argument("--ignore-bookmarks", dest="use_bookmarks", action="store_false",
                   help="Ignore the PDF outline; detect headings from fonts only.")
    p.add_argument("--keep-code-quotes", dest="fix_code_quotes", action="store_false",
                   help="Do not convert curly quotes/dashes to ASCII inside code.")
    p.add_argument("--mark-inline-code", action="store_true", help="Wrap inline monospace words in `backticks`.")
    p.add_argument("-q", "--quiet", action="store_true", help="No progress output.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def collect_pdfs(inputs, recursive):
    found = []
    for raw in inputs:
        s = raw.strip().strip("'\"")
        if s.startswith("file://"):
            s = s[7:]
        path = Path(s).expanduser()
        if not path.exists():
            parent = path.parent
            if parent.is_dir():
                clean_target = sanitize_text(unicodedata.normalize("NFC", path.name)).replace("\ufffd", "").strip()
                for cand in parent.glob("*.pdf"):
                    clean_cand = sanitize_text(unicodedata.normalize("NFC", cand.name)).replace("\ufffd", "").strip()
                    if clean_target and clean_cand == clean_target:
                        path = cand
                        break
        if path.is_dir():
            it = path.rglob("*") if recursive else path.glob("*")
            found += sorted(p for p in it if p.is_file() and p.suffix.lower() == ".pdf")
        elif path.is_file():
            found.append(path)
        else:
            print(f"⚠ Not found: {path}", file=sys.stderr)
    return found


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    inputs = args.inputs
    if not inputs:
        try:
            s = input("Enter the path to a PDF book (or a folder of PDFs): ").strip()
        except (EOFError, KeyboardInterrupt):
            return 1
        if not s:
            return 1
        inputs = [s]
    pdfs = collect_pdfs(inputs, args.recursive)
    if not pdfs:
        print("No PDF files found.", file=sys.stderr)
        return 1

    opts = Options(
        width=args.width, code_style=args.code_style, pages=args.pages, page_markers=args.page_markers,
        contents=args.contents, contents_depth=args.contents_depth, strip_headers=args.strip_headers,
        columns=args.columns, tables=args.tables, figures=args.figures, ocr=args.ocr, ocr_lang=args.ocr_lang,
        password=args.password, split_chapters=args.split_chapters, use_bookmarks=args.use_bookmarks,
        fix_code_quotes=args.fix_code_quotes, mark_inline_code=args.mark_inline_code, quiet=args.quiet,
    )
    out = Path(args.output).expanduser() if args.output else None
    if out is not None and len(pdfs) > 1:
        out.mkdir(parents=True, exist_ok=True)

    ok = 0
    for pdf in pdfs:
        target = out
        if out is not None and len(pdfs) > 1:
            target = out / (pdf.stem + ".txt")
        ok += process_file(pdf, target, opts)
    if len(pdfs) > 1:
        log(f"\nDone: {ok}/{len(pdfs)} file(s) converted.", opts)
    return 0 if ok == len(pdfs) else 2


if __name__ == "__main__":
    sys.exit(main())
