#!/usr/bin/env python3
"""
pdf_book_to_text.py - Extract clean, structured plain text, Markdown, and RAG chunks from PDF books.

Author:  AZZANI (info@7azzani.com / agz97agz97@gmail.com)
Repo:    https://github.com/7AZZANI/pdf-book-to-text
License: AZZANI Non-Commercial Dual License (see LICENSE)

Designed especially for PROGRAMMING & TECHNICAL books:
  * Multi-format export: Clean Text (.txt), GitHub Markdown (.md), and RAG JSONL (.jsonl).
  * True chapter & section hierarchy detected from PDF bookmarks (outline) or font analysis.
  * Pristine code formatting: Preserves original indentation, aligns character grids,
    detects programming languages (Python, Bash, C/C++, Java, JS, SQL, JSON), strips listing
    line numbers, and handles proportional code fonts.
  * Clean text integrity: Preserves box-drawing trees (├──, └──), HTML entities, math & CJK.
  * Bidirectional & Multilingual: Native RTL support for Arabic, Hebrew, Urdu, and Farsi with
    correct BiDi span ordering, punctuation placement, and Unicode NFKC outline matching.
  * Layout aware: Removes repeating headers/footers, joins hyphenated paragraphs across pages,
    detects multi-column spreads, ASCII/GFM tables, and isolates footnotes.
  * Performance: Parallel multi-core batch processing (-j N) with skip-existing caching.

Usage
-----
    pdf-book-to-text book.pdf                    # -> book.txt
    pdf-book-to-text book.pdf --format md        # -> book.md (with fenced ```lang blocks)
    pdf-book-to-text book.pdf --format jsonl     # -> book.jsonl (RAG-ready chunk stream)
    pdf-book-to-text ~/Books/ -r -j 4            # Parallel batch conversion across folders
"""
from __future__ import annotations

import argparse
import collections
from collections import Counter, defaultdict
import concurrent.futures
from dataclasses import dataclass
import difflib
import html
import json
import math
import os
from pathlib import Path
import re
import statistics
import sys
import textwrap
import time
import unicodedata
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple, Union

try:
    import pymupdf  # PyMuPDF 1.24+
except ImportError:
    try:
        import fitz as pymupdf  # Older PyMuPDF alias
    except ImportError:
        sys.exit(
            "Error: PyMuPDF is not installed.\n"
            "Run: pip install pymupdf"
        )

__version__ = "2.0.0"
__author__ = "AZZANI"
__email__ = "info@7azzani.com"

# --------------------------------------------------------------------------- #
#  Text cleansing & sanitization (Safe for code, box-drawing, and Unicode)
# --------------------------------------------------------------------------- #
TEXT_FLAGS = pymupdf.TEXTFLAGS_SEARCH | pymupdf.TEXT_PRESERVE_LIGATURES | pymupdf.TEXT_PRESERVE_WHITESPACE

# Regex for Right-to-Left scripts (Arabic, Hebrew, Farsi, Urdu, Syriac, Thaana)
RTL_RE = re.compile(r"[\u0590-\u08ff\ufb1d-\ufdff\ufe70-\ufefc]")

# Arabic punctuation and digits
ARABIC_TERMINAL_RE = re.compile(r"[.!?:;؟،؛][\"'\u201d\u2019)\]]*$")
ARABIC_DIGITS_TRANS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def count_rtl_chars(s: str) -> int:
    """Return count of Right-to-Left characters in string."""
    return len(RTL_RE.findall(s))


def is_rtl_text(s: str) -> bool:
    """Return True if string contains RTL characters."""
    return bool(RTL_RE.search(s))


def is_predominantly_rtl(s: str) -> bool:
    """Return True if RTL characters outnumber Latin/LTR letters."""
    rtl_count = count_rtl_chars(s)
    ltr_count = len(re.findall(r"[A-Za-z]", s))
    return rtl_count > ltr_count


def clean_text(s: str) -> str:
    """Sanitize document body text safely.

    Strips NUL bytes and ASCII control characters (< 32 except \\t, \\n, \\r).
    NEVER mutates box-drawing characters (e.g. ─ U+2500), math symbols, CJK ideographs,
    and NEVER unescapes HTML entities in code listings.
    """
    if not s:
        return ""
    chars = []
    for c in s:
        code = ord(c)
        if code == 0:
            continue
        if code < 32 and code not in (9, 10, 13):
            continue
        chars.append(c)
    return "".join(chars)


def repair_metadata(s: str) -> str:
    """Repair PDF bookmark titles and document metadata.

    Repairs byte-swapped UTF-16 strings (e.g. 0x00 0x50 -> 'P') and unescapes HTML entities.
    Strictly used for document metadata and outline titles, NEVER on book body text.
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
        # Only repair if byte-swapped ASCII in high byte (e.g., 0x5000 -> 'P')
        if (code & 0xFF) == 0 and 0x20 <= (code >> 8) <= 0x7E:
            chars.append(chr(code >> 8))
        elif code < 32 and code not in (9, 10, 13):
            continue
        else:
            chars.append(c)
    return clean_ws("".join(chars))


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
    "\u00a0": " ", "\u202f": " ", "\u2007": " ", "\u2008": " ", "\u2009": " ",
    "\u200a": " ", "\u2002": " ", "\u2003": " ", "\u2004": " ", "\u2005": " ",
    "\u2006": " ", "\u3000": " ",
}
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

TERMINAL_RE = re.compile(r"[.!?:;؟،؛][\"'\u201d\u2019)\]]*$")
STRONG_BULLET_RE = re.compile(r"^([•○◦▪▫■□►▸‣⁃✓✔➢➤❖◆◇])\s*(?=\S)")
WEAK_BULLET_RE = re.compile(r"^([-–—*])\s+(?=\S)")
NUM_LIST_RE = re.compile(
    r"^(\(?\d{1,3}[.)]|\(?[a-z][.)]|\(?(?:i|ii|iii|iv|v|vi|vii|viii|ix|x)[.)]|\(?[A-Z]\)|\(?[٠-٩]{1,3}[.)])\s+(?=\S)"
)
PAGE_NUM_RE = re.compile(
    r"^[-–—\s]*(?:page\s*)?(?:\d{1,4}|[ivxlcdm]{1,7}|[٠-٩]{1,4})(?:\s*(?:/|of)\s*(?:\d{1,4}|[٠-٩]{1,4}))?[-–—\s]*$",
    re.I,
)
LABEL_RE = re.compile(
    r"^(?:chapter|part|appendix|lesson|module|unit|section|book|day|hour|step|الفصل|الباب|الجزء)?\s*"
    r"(?:\d{1,3}|[ivxlcdm]{1,6}|[a-z]|[٠-٩]{1,3})[.:]?$",
    re.I,
)
CHAPTER_RE = re.compile(
    r"^(?:chapter|part|appendix|الفصل|الباب)\s+(?:\d{1,3}|[ivxlcdm]{1,6}|[a-z]|one|two|three|four|"
    r"five|six|seven|eight|nine|ten|[٠-٩]{1,3}|الاول|الأول|الثاني|الثالث|الرابع|الخامس)\b",
    re.I,
)
LEADER_RE = re.compile(r"(?:\s*\.){4,}\s*")
WORD_RE = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)*")


# --------------------------------------------------------------------------- #
#  Data structures
# --------------------------------------------------------------------------- #
class Seg:
    """A run of text in one font, with whitespace trimmed and positions kept."""
    __slots__ = ("x0", "x1", "text", "size", "mono", "bold", "cw", "base", "nchars")

    def __init__(self, x0: float, x1: float, text: str, size: float, mono: bool, bold: bool, cw: float, base: float):
        self.x0, self.x1, self.text, self.size = x0, x1, text, size
        self.mono, self.bold, self.cw, self.base = mono, bold, cw, base
        self.nchars = len(text.replace(" ", ""))


class Line:
    """A visual line of text (one or more segments on the same baseline)."""
    __slots__ = (
        "x0", "y0", "x1", "y1", "segs", "base", "size", "mono_ratio", "bold_ratio",
        "cw", "kind", "level", "text", "col", "nchars", "tabular", "block", "dropcap", "is_rtl"
    )

    def __init__(self, segs: list[Seg], y0: float, y1: float):
        # BiDi span layout: Check if predominantly RTL
        rtl_c = sum(count_rtl_chars(s.text) for s in segs)
        ltr_c = sum(len(re.findall(r"[A-Za-z0-9]", s.text)) for s in segs)
        self.is_rtl = rtl_c > ltr_c

        if self.is_rtl:
            # Order segments for visual Right-to-Left reading:
            # Sort top-level groups right-to-left, but keep Latin words/numbers left-to-right internally.
            self.segs = self._bidi_sort(segs)
        else:
            self.segs = sorted(segs, key=lambda s: s.x0)

        self.y0, self.y1 = y0, y1
        self.kind, self.level, self.text, self.col = "text", 0, "", 0
        self.tabular, self.block, self.dropcap = False, None, False
        self.refresh()

    @staticmethod
    def _bidi_sort(segs: list[Seg]) -> list[Seg]:
        """Order segments right-to-left while preserving LTR order within Latin/numeric runs."""
        sorted_ltr = sorted(segs, key=lambda s: s.x0)
        groups: list[tuple[bool, list[Seg]]] = []
        curr_group: list[Seg] = []
        curr_is_rtl: Optional[bool] = None

        for s in sorted_ltr:
            s_rtl = is_rtl_text(s.text)
            if curr_is_rtl is None:
                curr_is_rtl = s_rtl
                curr_group = [s]
            elif s_rtl == curr_is_rtl:
                curr_group.append(s)
            else:
                groups.append((curr_is_rtl, curr_group))
                curr_group = [s]
                curr_is_rtl = s_rtl
        if curr_group:
            groups.append((curr_is_rtl, curr_group))

        # Sort the runs by descending physical right-edge (rightmost group first)
        groups.sort(key=lambda g: max(s.x1 for s in g[1]), reverse=True)
        res = []
        for is_rtl, grp in groups:
            if is_rtl:
                grp.sort(key=lambda s: s.x1, reverse=True)
            else:
                grp.sort(key=lambda s: s.x0)
            res.extend(grp)
        return res

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
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def h(self) -> float:
        return self.y1 - self.y0


class PageData:
    pno: int
    label: str
    width: float
    height: float
    lines: list[Line]
    blocks: list            # (y0, x0, kind, text) for tables / figures
    scanned: bool = False
    garbled: bool = False
    ncols: int = 1
    gutter: float = 0.0
    colm: dict = None
    toc_unmatched: list = None


class Heading:
    __slots__ = ("level", "text", "from_font")

    def __init__(self, level: int, text: str, from_font: bool = False):
        self.level, self.text, self.from_font = level, text, from_font


class Para:
    __slots__ = ("lines", "marker", "list_level", "marker_x0", "first_x0", "rest_x0")

    def __init__(self, text: str, marker: str = None, list_level: int = 0, x0: float = 0.0):
        self.lines = [text]
        self.marker, self.list_level = marker, list_level
        self.marker_x0 = self.first_x0 = x0
        self.rest_x0 = None


class Code:
    __slots__ = ("lines", "lang")  # [indent_cols, text, blank_lines_before]

    def __init__(self, lang: str = ""):
        self.lines = []
        self.lang = lang


class Pre:
    __slots__ = ("lines",)

    def __init__(self, lines: list[str]):
        self.lines = list(lines)


class PageMark:
    __slots__ = ("label",)

    def __init__(self, label: str):
        self.label = label


class Footnote:
    __slots__ = ("marker", "text", "page")

    def __init__(self, marker: str, text: str, page: int):
        self.marker, self.text, self.page = marker, text, page


@dataclass
class Options:
    format: str = "txt"              # txt, md, jsonl
    width: int = 100
    code_style: str = "fence"        # fence, indent, plain
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
    detect_lang: bool = True
    strip_line_numbers: bool = True
    footnotes: str = "section"       # section, inline, drop
    no_bom: bool = False
    stdout: bool = False
    jobs: int = 1
    skip_existing: bool = False
    quiet: bool = False


# --------------------------------------------------------------------------- #
#  Utilities & Heuristics
# --------------------------------------------------------------------------- #
def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[k]


def mode_of(values: Sequence[Any], ndigits: int = 1, default: Any = None) -> Any:
    if not values:
        return default
    return Counter(round(v, ndigits) for v in values).most_common(1)[0][0]


def norm_key(s: str) -> str:
    """Normalize text for robust outline and heading comparison across all scripts."""
    s_clean = unicodedata.normalize("NFKC", s).casefold()
    return re.sub(r"[\W_]+", "", s_clean, flags=re.UNICODE)


def clean_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def log(msg: str, opts: Options, end: str = "\n"):
    if not opts.quiet:
        print(msg, file=sys.stderr, end=end, flush=True)


# --------------------------------------------------------------------------- #
#  Code Language & Line-Number Detection
# --------------------------------------------------------------------------- #
def guess_code_language(text: str) -> str:
    """Guess programming language of code block using syntax heuristics."""
    if not text.strip():
        return ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ""

    stripped = text.strip()
    if (stripped.startswith("{") and stripped.endswith("}")) or (stripped.startswith("[") and stripped.endswith("]")):
        try:
            json.loads(stripped)
            return "json"
        except Exception:
            pass

    if re.search(r"^\s*<(!DOCTYPE|html|head|body|div|p|span|table|form|script|style|xml)\b", text, re.I | re.M):
        return "html" if "<html" in text.lower() or "<div" in text.lower() else "xml"

    scores = Counter()

    # Python
    py_patterns = [
        r"\bdef\s+[a-zA-Z_]\w*\s*\(",
        r"\bclass\s+[a-zA-Z_]\w*(?:\s*\(.*?\))?:",
        r"\bimport\s+[a-zA-Z_]\w*",
        r"\bfrom\s+[a-zA-Z_]\w*\s+import\b",
        r"\belif\b",
        r"\bexcept(?:\s+\w+)?\s*:",
        r"\bfinally\s*:",
        r"\bprint\s*\(",
        r"\bprint\s+['\"\(]",
        r"\b__init__\b",
        r"if\s+__name__\s*==\s*['\"]__main__['\"]:",
        r"\bself\.",
        r":\s*$",
    ]
    for pat in py_patterns:
        scores["python"] += len(re.findall(pat, text, re.M)) * 3

    # Bash / Shell
    sh_patterns = [
        r"^\s*\$\s+[a-zA-Z]",
        r"^\s*#\s+(?:sudo|apt|yum|pacman|systemctl|chmod|chown|mkdir|grep|cat|curl|wget|tar|ssh|scp|echo|export|rm|cp|mv)\b",
        r"\b(?:sudo|apt-get|apt|yum|pacman|systemctl|systemd)\b",
        r"\b(?:chmod|chown|mkdir|grep|cat|curl|wget|tar|ssh|scp|export)\b",
        r"#!/bin/(?:bash|sh|zsh)",
        r"\bif\s+\[.*?\];\s*then\b",
        r"\bfi\b",
    ]
    for pat in sh_patterns:
        scores["bash"] += len(re.findall(pat, text, re.M)) * 3

    # C / C++
    c_patterns = [
        r"#include\s+<[\w.]+>",
        r"\bint\s+main\s*\([^)]*\)",
        r"\bprintf\s*\(",
        r"\bcout\s*<<",
        r"\bstd::",
        r"\bnullptr\b",
        r"\btypedef\s+struct\b",
        r"\bconst\s+char\s*\*",
    ]
    for pat in c_patterns:
        scores["c" if "cout" not in text and "std::" not in text else "cpp"] += len(re.findall(pat, text)) * 3

    # JavaScript / TypeScript
    js_patterns = [
        r"\bconsole\.log\s*\(",
        r"\bconst\s+[a-zA-Z_]\w*\s*=",
        r"\blet\s+[a-zA-Z_]\w*\s*=",
        r"\bfunction\s*[a-zA-Z_]\w*\s*\(",
        r"=>\s*\{?",
        r"\bdocument\.getElementById\b",
        r"\bwindow\.",
        r"\bexport\s+(?:default\s+)?(?:class|function|const)\b",
    ]
    for pat in js_patterns:
        scores["javascript"] += len(re.findall(pat, text)) * 2

    # SQL
    sql_patterns = [
        r"\bSELECT\s+.*?\s+FROM\b",
        r"\bINSERT\s+INTO\b",
        r"\bCREATE\s+TABLE\b",
        r"\bUPDATE\s+\w+\s+SET\b",
        r"\bDELETE\s+FROM\b",
        r"\bGROUP\s+BY\b",
        r"\bORDER\s+BY\b",
    ]
    for pat in sql_patterns:
        scores["sql"] += len(re.findall(pat, text, re.I)) * 3

    # Java
    java_patterns = [
        r"\bpublic\s+class\s+\w+",
        r"\bpublic\s+static\s+void\s+main\b",
        r"\bSystem\.out\.println\s*\(",
        r"\bpackage\s+[\w.]+;",
    ]
    for pat in java_patterns:
        scores["java"] += len(re.findall(pat, text)) * 3

    best = scores.most_common(1)
    if best and best[0][1] >= 2:
        return best[0][0]
    return ""


def strip_listing_numbers(lines: list[str]) -> list[str]:
    """Strip leading line numbers (e.g. '1  import os') while preserving code indentation."""
    if len(lines) < 2:
        return lines
    non_empty = [(idx, ln) for idx, ln in enumerate(lines) if ln.strip()]
    if len(non_empty) < 2:
        return lines

    pattern = re.compile(r"^(\s*\d{1,4})(\s+)(.*)$")
    matches = []
    for idx, ln in non_empty:
        m = pattern.match(ln)
        if m:
            matches.append((idx, int(m.group(1).strip()), len(m.group(2)), m.group(3)))

    if len(matches) >= 0.7 * len(non_empty):
        nums = [m[1] for m in matches]
        is_increasing = all(nums[i] < nums[i + 1] for i in range(len(nums) - 1))
        if is_increasing or (nums[0] in (0, 1) and nums[-1] >= len(nums) - 1):
            min_sep = min(m[2] for m in matches)
            new_lines = list(lines)
            for idx, num, sep_len, rest in matches:
                extra_indent = " " * (sep_len - min_sep)
                new_lines[idx] = extra_indent + rest
            return new_lines
    return lines


# --------------------------------------------------------------------------- #
#  Low level extraction: PyMuPDF rawdict -> Seg / Line
# --------------------------------------------------------------------------- #
def make_seg(span: dict, mono_fonts: Optional[Set[str]] = None) -> Optional[Seg]:
    chars = span.get("chars", [])
    if not chars:
        return None
    font = (span.get("font") or "").split("+")[-1]
    flags = span.get("flags", 0)
    mono = bool(flags & 8) or bool(MONO_FONT_RE.search(font)) or (mono_fonts is not None and font in mono_fonts)
    bold = bool(flags & 16) or bool(BOLD_FONT_RE.search(font))
    size = float(span.get("size", 0)) or 1.0

    items = []  # (x_start, x_end, char)
    for ch in chars:
        c = clean_text(ch["c"].translate(CHAR_TRANS))
        if not c:
            continue
        ox = ch["origin"][0]
        bx1 = ch["bbox"][2]
        items.append((ox, max(bx1, ox), c))

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

    text = clean_text(text)
    min_x = min(min(ox, bx1) for ox, bx1, _c in items)
    max_x = max(max(ox, bx1) for ox, bx1, _c in items)
    return Seg(min_x, max_x, text, size, mono, bold, cw, span["origin"][1])


def extract_raw_lines(page: pymupdf.Page, textpage: Any = None, mono_fonts: Optional[Set[str]] = None) -> list[Line]:
    d = page.get_text("rawdict", flags=TEXT_FLAGS, textpage=textpage)
    out = []
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            dx, dy = ln.get("dir", (1, 0))
            if abs(dy) > 0.10:      # rotated / vertical text
                continue
            segs = [s for s in (make_seg(sp, mono_fonts) for sp in ln.get("spans", [])) if s]
            if not segs:
                continue
            bb = ln["bbox"]
            out.append(Line(segs, bb[1], bb[3]))
    return out


def merge_line_group(group: list[Line]) -> Line:
    is_rtl = any(l.is_rtl for l in group)
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
    # Deduplicate fake-bold runs
    dedup = []
    for s in segs:
        if dedup and s.text == dedup[-1].text and abs(s.x0 - dedup[-1].x0) < 1.5:
            continue
        dedup.append(s)
    return Line(dedup, min(l.y0 for l in group), max(l.y1 for l in group))


def merge_baselines(lines: list[Line]) -> list[Line]:
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


def render_table(rows: list[list[Any]], maxw: int = 40) -> list[str]:
    """Render table rows as clean ASCII box grid."""
    rows = [[clean_ws(str(c)) if c is not None else "" for c in r] for r in rows]
    ncols = max(len(r) for r in rows)
    rows = [r + [""] * (ncols - len(r)) for r in rows]
    keep = [i for i in range(ncols) if any(r[i] for r in rows)]
    rows = [[r[i] for i in keep] for r in rows]
    if not rows or not rows[0]:
        return []
    ncols = len(rows[0])
    widths = [min(maxw, max(len(r[c]) for r in rows)) for c in range(ncols)]
    widths = [max(w, 3) for w in widths]

    def sep(ch):
        return "+" + "+".join(ch * (w + 2) for w in widths) + "+"

    out = [sep("-")]
    for r_idx, row in enumerate(rows):
        wrapped = [textwrap.wrap(cell, w, break_long_words=True) or [""] for cell, w in zip(row, widths)]
        nh = max(len(w) for w in wrapped)
        for h in range(nh):
            line = []
            for c_idx in range(ncols):
                val = wrapped[c_idx][h] if h < len(wrapped[c_idx]) else ""
                line.append(f" {val.ljust(widths[c_idx])} ")
            out.append("|" + "|".join(line) + "|")
        out.append(sep("=" if r_idx == 0 and len(rows) > 1 else "-"))
    return out


def render_table_md(rows: list[list[Any]]) -> list[str]:
    """Render table rows as GitHub Flavored Markdown table."""
    rows = [[clean_ws(str(c)) if c is not None else "" for c in r] for r in rows]
    ncols = max(len(r) for r in rows)
    rows = [r + [""] * (ncols - len(r)) for r in rows]
    keep = [i for i in range(ncols) if any(r[i] for r in rows)]
    rows = [[r[i].replace("|", "\\|") for i in keep] for r in rows]
    if not rows or not rows[0]:
        return []
    ncols = len(rows[0])
    header = rows[0]
    separator = ["---"] * ncols
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(separator) + " |",
    ]
    for r in rows[1:]:
        lines.append("| " + " | ".join(r) + " |")
    return lines


# --------------------------------------------------------------------------- #
#  The Core Book Extractor Engine
# --------------------------------------------------------------------------- #
class BookExtractor:
    def __init__(self, path: Path, opts: Options):
        self.path = Path(path)
        self.opts = opts
        self.doc: Optional[pymupdf.Document] = None
        self.metadata: dict = {}
        self.cover_title: Optional[str] = None
        self.pages: list[PageData] = []
        self.warnings: list[str] = []
        self.toc_entries: list = []
        self.toc_used: bool = False
        self.toc_max: int = 0
        self.vocab: Set[str] = set()
        self.mono_fonts: Set[str] = set()
        self.proportional_code_styles: Set[Tuple[str, float]] = set()
        self.body_size: float = 10.0
        self.body_pitch: float = 12.0
        self.footnotes_list: list[Footnote] = []
        self.elements: list = []
        self.elapsed: float = 0.0

    def run(self) -> list:
        opts = self.opts
        doc = pymupdf.open(self.path)
        try:
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
            self.metadata = dict(doc.metadata or {})
            self.cover_title = self._find_cover_title()
            self._profile_document_fonts()
            self._read_pages(indices)
            self._compute_font_stats()
            self._profile_proportional_code()
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
        finally:
            if doc and not doc.is_closed:
                doc.close()

    def _profile_document_fonts(self):
        """Profile fonts across sampled pages covering the whole book."""
        mono_fonts = set()
        prop_fonts = set()
        font_widths = defaultdict(dict)
        n = len(self.doc)
        # Sample strategically: first 10, last 5, and stepped throughout
        sample_indices = set(range(min(10, n))) | set(range(max(0, n - 5), n))
        step = max(1, n // 30)
        for i in range(0, n, step):
            sample_indices.add(i)

        for pno in sorted(sample_indices):
            if pno >= n:
                continue
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

    def _profile_proportional_code(self):
        """Identify proportional fonts used specifically for code listings (e.g. Garamond 13pt)."""
        style_counts = Counter()
        style_code_syntax = Counter()
        code_syntax_re = re.compile(
            r"^\s*(?:import\s+\w+|from\s+\w+|def\s+\w+|class\s+\w+|return\b|\w+\s*=\s*|#\(\d+\))|"
            r"\b(?:user32|kernel32|windll|socket|subprocess|sys|print)\b|[;{}()=]"
        )

        for pd in self.pages:
            for ln in pd.lines:
                if ln.mono_ratio < 0.5:
                    sz = round(ln.size, 1)
                    style_counts[sz] += 1
                    if code_syntax_re.search(ln.plain.strip()):
                        style_code_syntax[sz] += 1

        body_sz = round(self.body_size, 1)
        for sz, total in style_counts.items():
            if total >= 4 and sz != body_sz:
                ratio = style_code_syntax[sz] / total
                if ratio >= 0.35:
                    self.proportional_code_styles.add(sz)

    def _read_pages(self, indices: list[int]):
        opts, doc = self.opts, self.doc
        total = len(indices)
        ocr_failed = False
        for k, pno in enumerate(indices, 1):
            page = doc[pno]
            if k == 1 or k % 15 == 0 or k == total:
                log(f"\r  Reading page {k}/{total} ...", opts, end="")
            try:
                label = page.get_label() or str(pno + 1)
            except Exception:
                label = str(pno + 1)
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
                        except Exception as exc:
                            ocr_failed = True
                            self.warnings.append(f"OCR unavailable ({exc}); scanned pages skipped.")

            # Garbled font detection
            garbled = False
            if lines:
                text_cat = "".join(l.plain for l in lines)
                num_letters = len(re.findall(r"[A-Za-z\u0600-\u06ff]", text_cat))
                num_pua = len(re.findall(r"[\ue000-\uf8ff\ufffd]", text_cat))
                if num_letters > 20 and num_pua / (num_letters + num_pua) > 0.35:
                    garbled = True

            pd = PageData()
            pd.pno, pd.label = pno, label
            pd.width, pd.height = page.rect.width, page.rect.height
            pd.lines = lines
            pd.blocks = []
            pd.scanned = scanned
            pd.garbled = garbled
            self.pages.append(pd)

        if not opts.quiet:
            log("", opts)

    def _compute_font_stats(self):
        sizes = Counter()
        for pd in self.pages:
            for l in pd.lines:
                if l.mono_ratio < 0.5:
                    sizes[round(l.size, 1)] += l.nchars
        self.body_size = sizes.most_common(1)[0][0] if sizes else 10.0
        pitches = []
        for pd in self.pages:
            ls = [l for l in pd.lines if abs(l.size - self.body_size) < 0.5 and l.mono_ratio < 0.5]
            for i in range(len(ls) - 1):
                d = ls[i + 1].base - ls[i].base
                if 0.8 * self.body_size <= d <= 2.2 * self.body_size:
                    pitches.append(d)
        self.body_pitch = statistics.median(pitches) if pitches else self.body_size * 1.2

    def _remove_headers_footers(self):
        """Identify and remove running headers/footers and page numbers across pages."""
        top_c, bot_c = Counter(), Counter()
        for pd in self.pages:
            H = pd.height
            for l in pd.lines:
                txt = l.plain.strip()
                if not txt:
                    continue
                if l.y1 <= H * 0.12:
                    top_c[round(l.y0, 0)] += 1
                elif l.y0 >= H * 0.88:
                    bot_c[round(l.y0, 0)] += 1

        top_bands = [y for y, c in top_c.items() if c >= max(3, len(self.pages) * 0.20)]
        bot_bands = [y for y, c in bot_c.items() if c >= max(3, len(self.pages) * 0.20)]

        for pd in self.pages:
            H = pd.height
            kept = []
            for l in pd.lines:
                txt = l.plain.strip()
                if PAGE_NUM_RE.match(txt):
                    if l.y1 <= H * 0.12 or l.y0 >= H * 0.88:
                        continue
                if l.y1 <= H * 0.12 and any(abs(round(l.y0, 0) - ty) <= 3.0 for ty in top_bands):
                    if l.size <= self.body_size * 1.15:
                        continue
                if l.y0 >= H * 0.88 and any(abs(round(l.y0, 0) - by) <= 3.0 for by in bot_bands):
                    if l.size <= self.body_size * 1.15:
                        continue
                kept.append(l)
            pd.lines = kept

    def _order_page(self, pd: PageData):
        """Sort lines in reading order, handle RTL and 2-column spreads."""
        lines = pd.lines
        body = self.body_size
        dropcaps = [l for l in lines if len(l.plain) == 1 and l.plain.isalpha() and l.size >= body * 1.8]
        for d in dropcaps:
            d.dropcap = True
        lines = [l for l in lines if not l.dropcap]

        page_is_rtl = sum(count_rtl_chars(l.plain) for l in lines) > sum(len(l.plain) - count_rtl_chars(l.plain) for l in lines)
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
                    # In RTL two-column, right column is read first!
                    if page_is_rtl:
                        ordered += merge_baselines(band_r) + merge_baselines(band_l)
                    else:
                        ordered += merge_baselines(band_l) + merge_baselines(band_r)
                    if fi < len(full):
                        fl = full[fi]
                        fl.col = 0
                        ordered.append(fl)
                        fi += 1
                    prev_y = b
                for l in ordered:
                    l.col = 1 if l.x0 >= gx - 1 else 0

        if ordered is None:
            ordered = merge_baselines(lines)
            for l in ordered:
                l.col = 0

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

    def _classify(self, ln: Line):
        """Classify line as heading, code, footnote, list item, or text."""
        # Check for footnotes at bottom of page
        if ln.y0 >= 0.75 * self.body_size * 50 and ln.size <= self.body_size * 0.85:
            if re.match(r"^(\*|†|‡|\[\d{1,2}\]|\d{1,2}\b)", ln.plain):
                ln.kind = "footnote"
                return

        # Check for code (monospace or proportional code style)
        is_prop_code = round(ln.size, 1) in self.proportional_code_styles
        if ln.mono_ratio >= 0.5 or (is_prop_code and ln.size < self.body_size * 0.98):
            ln.kind = "code"
            ln.text = self._join_line_spans(ln, code=True)
            return

        # Check for headings
        txt = clean_ws(ln.plain)
        if (ln.size >= self.body_size * 1.25 or (ln.bold_ratio >= 0.8 and ln.size >= self.body_size * 1.08)) and len(txt) <= 120:
            if not TERMINAL_RE.search(txt) or CHAPTER_RE.match(txt):
                ln.kind = "heading"
                ln.text = self._join_line_spans(ln, code=False)
                return

        ln.text = self._join_line_spans(ln, code=False)

    def _join_line_spans(self, ln: Line, code: bool) -> str:
        out = []
        prev = None
        mark = self.opts.mark_inline_code and not code
        in_code = False
        is_rtl = ln.is_rtl

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
                    # Do NOT force space blindly on RTL; respect the physical gap!
                    elif gap > 0.12 * min(s.size, prev.size):
                        out.append(" ")
            if mark:
                if s.mono and not in_code:
                    out.append("`")
                    in_code = True
                elif not s.mono and in_code:
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
            return clean_text(text.rstrip())
        text = re.sub(r" {2,}", " ", text).strip()
        if LEADER_RE.search(text) and re.search(r"(?:\.\s*){4,}\s*\S{1,8}$", text):
            ln.tabular = True
            text = LEADER_RE.sub(" .... ", text)
        return clean_text(text.replace("\x00", "    "))

    def _insert_blocks(self, pd: PageData):
        if not pd.blocks:
            return
        pd.blocks.sort(key=lambda b: (b[0], b[1]))
        bi = 0
        ordered = []
        for l in pd.lines:
            while bi < len(pd.blocks) and pd.blocks[bi][0] <= l.y0:
                ordered.append(pd.blocks[bi])
                bi += 1
            ordered.append(l)
        while bi < len(pd.blocks):
            ordered.append(pd.blocks[bi])
            bi += 1
        pd.lines = ordered

    def _compute_layout_stats(self):
        for pd in self.pages:
            cols = defaultdict(list)
            for item in pd.lines:
                if isinstance(item, Line) and item.kind == "text":
                    cols[item.col].append(item)
            pd.colm = {}
            for c, ls in cols.items():
                if len(ls) >= 3:
                    left = percentile([l.x0 for l in ls], 0.15)
                    right = percentile([l.x1 for l in ls], 0.85)
                    pd.colm[c] = (left, right)
                elif ls:
                    pd.colm[c] = (min(l.x0 for l in ls), max(l.x1 for l in ls))
                else:
                    pd.colm[c] = (0.0, pd.width)

    def _build_vocab(self):
        vocab = set()
        for pd in self.pages:
            for item in pd.lines:
                if isinstance(item, Line) and item.kind == "text":
                    for w in WORD_RE.findall(item.plain):
                        vocab.add(w.lower())
        self.vocab = vocab

    def _heading_like(self, l: Line) -> bool:
        return l.kind == "heading" or l.bold_ratio >= 0.8 or l.size >= self.body_size * 1.08

    def _similarity(self, nw: str, nt: str, heading_like: bool) -> float:
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
            if not isinstance(lines[i], Line) or lines[i].kind in skip:
                continue
            texts, hl = [], True
            for k in range(3):
                j = i + k
                if j >= n or not isinstance(lines[j], Line) or lines[j].kind in skip:
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
            title = repair_metadata(clean_ws(str(title)))
            if title and 1 <= page <= self.doc.page_count:
                entries.append([lvl, title, page - 1])
        if len(entries) < 2:
            return
        top_page_count = Counter(e[2] for e in entries).most_common(1)[0][1]
        if len(entries) > 4 and top_page_count > 0.6 * len(entries):
            return
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
                pd.toc_unmatched.append((lvl, title))
                continue
            _score, i, j = m
            lines = pd.lines
            for t in range(i + 1, j + 1):
                if isinstance(lines[t], Line):
                    lines[t].kind = "absorbed"
            page_text = clean_ws(" ".join((lines[t].text or lines[t].plain) for t in range(i, j + 1) if isinstance(lines[t], Line)))
            if norm_key(title) in norm_key(page_text) and len(page_text) <= len(title) + 25:
                if re.match(r"^(?:\d{1,3}(?:\.\d{1,3})*|[A-Z]\.\d{1,3}|Chapter\s+\w+[:.]?)\s+", page_text, re.I):
                    title = page_text
            nt = norm_key(title)
            t, absorbed = i - 1, 0
            while t >= 0 and absorbed < 2:
                lt = lines[t]
                if isinstance(lt, Line):
                    lab = clean_ws(lt.plain)
                    if lt.kind == "heading" and len(lab) <= 25 and (LABEL_RE.match(lab) or norm_key(lab) in nt):
                        lt.kind = "absorbed"
                        if norm_key(lab) not in nt:
                            lab = lab.rstrip(".:")
                            if re.fullmatch(r"\d{1,3}|[IVXLCivxlc]{1,6}", lab):
                                title = f"{lab}. {title}"
                            else:
                                lab = lab.title() if lab.isupper() else lab
                                title = f"{lab}: {title}"
                            nt = norm_key(title)
                        t -= 1
                        absorbed += 1
                    else:
                        break
                else:
                    break
            lines[i].kind = "toc_heading"
            lines[i].level = lvl
            lines[i].text = title

    def _assign_font_heading_levels(self):
        sizes = set()
        for pd in self.pages:
            for item in pd.lines:
                if isinstance(item, Line) and item.kind == "heading" and not getattr(item, "level", 0):
                    sizes.add(round(item.size, 1))
        order = sorted(sizes, reverse=True)
        level_map = {sz: min(4, i + (self.toc_max + 1 if self.toc_used else 1)) for i, sz in enumerate(order)}
        for pd in self.pages:
            for item in pd.lines:
                if isinstance(item, Line) and item.kind == "heading" and not getattr(item, "level", 0):
                    item.level = level_map.get(round(item.size, 1), 3)

    def _split_list_marker(self, text: str) -> tuple[Optional[str], str]:
        m = STRONG_BULLET_RE.match(text) or WEAK_BULLET_RE.match(text) or NUM_LIST_RE.match(text)
        if m:
            mk = m.group(1)
            return ("•" if mk in "-–—*" else mk), text[m.end():]
        return None, text

    def _expected_pitch(self, a: Line, b: Line) -> float:
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
        ends = bool(TERMINAL_RE.search(a.text))

        if a.is_rtl:
            # For RTL lines, lines start at right and end at left!
            if a.x0 > left + 0.40 * width:
                return True
            if ends and a.x0 > left + 0.12 * width:
                return True
        else:
            if a.x1 < right - 0.40 * width:
                return True
            if ends and a.x1 < right - 0.12 * width:
                return True

        if b.x0 > a.x0 + 0.8 * em and a.x0 < left + 0.5 * em:
            return True
        if cur.rest_x0 is not None and b.x0 < cur.rest_x0 - 0.8 * em:
            return True
        return False

    def _build_page_elements(self, pd: PageData) -> list:
        els = []
        for lvl, title in (pd.toc_unmatched or []):
            els.append(Heading(lvl, title))
        cur: Union[Para, Code, Pre, None] = None
        prev: Optional[Line] = None

        for item in pd.lines:
            if isinstance(item, tuple):
                if cur is not None:
                    els.append(cur)
                    cur = None
                prev = None
                els.append(Pre(item[3].splitlines()))
                continue
            ln = item
            if ln.kind in ("absorbed", "dropcap"):
                if ln.kind == "dropcap" and isinstance(cur, Para):
                    cur.lines[0] = ln.plain + cur.lines[0]
                continue
            if ln.kind == "footnote":
                if cur is not None:
                    els.append(cur)
                    cur = None
                prev = None
                m = re.match(r"^(\*|†|‡|\[\d{1,2}\]|\d{1,2})\s*(.*)", ln.plain)
                if m:
                    self.footnotes_list.append(Footnote(m.group(1), m.group(2), pd.pno + 1))
                else:
                    self.footnotes_list.append(Footnote("*", ln.plain, pd.pno + 1))
                continue
            if ln.kind in ("toc_heading", "heading"):
                if cur is not None:
                    els.append(cur)
                    cur = None
                prev = None
                els.append(Heading(ln.level, ln.text, from_font=(ln.kind == "heading")))
                continue
            if ln.kind == "code":
                if not isinstance(cur, Code):
                    if cur is not None:
                        els.append(cur)
                    cur = Code()
                    prev = None
                bl = 0
                if prev is not None:
                    d = ln.base - prev.base
                    p = self._expected_pitch(prev, ln)
                    if d > p * 1.5:
                        bl = max(1, int(round((d - p) / p)))
                left = pd.colm.get(ln.col, (0.0, pd.width))[0]
                em = ln.cw or ln.size * 0.6
                ind = int(round((ln.x0 - left) / em)) if ln.x0 > left else 0
                cur.lines.append((max(0, ind), ln.text, bl))
                prev = ln
                continue

            # Body text
            marker, body = self._split_list_marker(ln.text)
            if marker:
                if cur is not None:
                    els.append(cur)
                left = pd.colm.get(ln.col, (ln.x0, ln.x1))[0]
                indent = int(round((ln.x0 - left) / (self.body_size * 2)))
                cur = Para(body, marker=marker, list_level=max(0, indent), x0=ln.x0)
                prev = ln
                continue

            if isinstance(cur, Para) and not self._is_break(prev, ln, cur, pd):
                cur.lines.append(ln.text)
                if cur.rest_x0 is None and len(cur.lines) == 2:
                    cur.rest_x0 = ln.x0
            else:
                if cur is not None:
                    els.append(cur)
                cur = Para(ln.text, x0=ln.x0)
            prev = ln

        if cur is not None:
            els.append(cur)
        return els

    def _assemble(self, page_elements: list[tuple[PageData, list]]) -> list:
        out = []
        for pd, els in page_elements:
            if self.opts.page_markers:
                out.append(PageMark(pd.label))
            for e in els:
                if isinstance(e, Para) and out and isinstance(out[-1], Para):
                    last = out[-1]
                    if not last.marker and not e.marker:
                        if not TERMINAL_RE.search(last.lines[-1]) and not e.lines[0].startswith(("•", "-", "1.")):
                            last.lines.extend(e.lines)
                            continue
                elif isinstance(e, Code) and out and isinstance(out[-1], Code):
                    out[-1].lines.extend(e.lines)
                    continue
                out.append(e)
        return out

    def _post_process(self, elements: list) -> list:
        out = []
        for e in elements:
            if isinstance(e, Para):
                raw = " ".join(e.lines).strip()
                if len(raw) <= 2 or (len(raw) <= 4 and re.fullmatch(r"[-–—*#•]+", raw)):
                    continue
            out.append(e)
        return out

    def _join_para(self, lines: list[str]) -> str:
        if not lines:
            return ""
        out = lines[0]
        for nxt in lines[1:]:
            if not nxt:
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
                    out = out + nxt
                else:
                    out = out[:-1] + nxt
            elif re.search(r"(?:https?://|www\.)\S*[/._-]$", out) or out.endswith(("\u2014", "/")):
                out = out + nxt
            else:
                out = out + " " + nxt
        return clean_text(out.replace("\u00ad", ""))

    def _find_cover_title(self) -> Optional[str]:
        candidates = []
        for pno in range(min(4, len(self.doc))):
            d = self.doc[pno].get_text("rawdict", flags=TEXT_FLAGS)
            for b in d.get("blocks", []):
                if b.get("type") != 0:
                    continue
                for l in b.get("lines", []):
                    txt = clean_ws(" ".join("".join(c["c"] for c in s.get("chars", [])) for s in l.get("spans", [])))
                    if txt and len(txt) <= 120 and not PAGE_NUM_RE.match(txt):
                        sz = max((s.get("size", 0) for s in l.get("spans", [])), default=0)
                        candidates.append((sz, txt))
        if candidates:
            candidates.sort(key=lambda c: c[0], reverse=True)
            return candidates[0][1]
        return None

    def _header_block(self, rule_len: int) -> list[str]:
        meta = self.metadata
        title = repair_metadata(meta.get("title") or "") or self.cover_title or self.path.stem
        author = repair_metadata(meta.get("author") or "")
        lines = ["=" * rule_len, f"  {title}"]
        if author:
            lines.append(f"  by {author}")
        lines.append("=" * rule_len)

        if self.opts.contents and self.toc_entries:
            lines.append("\nTABLE OF CONTENTS")
            lines.append("-" * min(rule_len, 40))
            for lvl, t, p in self.toc_entries:
                if lvl <= self.opts.contents_depth:
                    pad = "    " * (lvl - 1)
                    dots = " " + ". " * max(1, (rule_len - len(pad) - len(t) - 8) // 2) + " "
                    lines.append(f"{pad}{t}{dots}{p + 1}")
            lines.append("-" * min(rule_len, 40))
        return lines

    # ------------------------------------------------------------------ #
    #  Renderers (Text, Markdown, JSONL)
    # ------------------------------------------------------------------ #
    def render(self, elements: list, include_header: bool = True) -> str:
        fmt = self.opts.format.lower()
        if fmt == "md":
            return self.render_markdown(elements, include_header=include_header)
        elif fmt == "jsonl":
            return self.render_jsonl(elements)
        else:
            return self.render_text(elements, include_header=include_header)

    def render_text(self, elements: list, include_header: bool = True) -> str:
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
                if opts.strip_line_numbers:
                    body = strip_listing_numbers(body)
                blank(1)
                if opts.code_style == "fence":
                    lang = guess_code_language("\n".join(body)) if opts.detect_lang else ""
                    out += [f"```{lang}"] + body + ["```"]
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

        # Append Footnotes if configured
        if self.footnotes_list and opts.footnotes == "section":
            blank(2)
            out.append("=" * rule)
            out.append("FOOTNOTES")
            out.append("-" * min(rule, 40))
            for fn in self.footnotes_list:
                out.append(f"[{fn.marker}] (p. {fn.page}) {fn.text}")
            blank(1)

        text = "\n".join(x.rstrip() for x in out)
        text = re.sub(r"\n{5,}", "\n\n\n\n", text)
        return clean_text(text.strip("\n") + "\n")

    def render_markdown(self, elements: list, include_header: bool = True) -> str:
        out: list[str] = []
        meta = self.metadata
        title = repair_metadata(meta.get("title") or "") or self.cover_title or self.path.stem
        author = repair_metadata(meta.get("author") or "")

        if include_header:
            out.append(f"# {title}\n")
            if author:
                out.append(f"**Author:** {author}\n")
            if self.opts.contents and self.toc_entries:
                out.append("## Table of Contents\n")
                for lvl, t, p in self.toc_entries:
                    if lvl <= self.opts.contents_depth:
                        pad = "  " * (lvl - 1)
                        out.append(f"{pad}* {t} *(p. {p + 1})*")
                out.append("\n---\n")

        for e in elements:
            if isinstance(e, Heading):
                hashes = "#" * max(1, min(6, e.level + (0 if include_header else 0)))
                out.append(f"\n{hashes} {e.text}\n")
            elif isinstance(e, Para):
                text = self._join_para(e.lines)
                if not text:
                    continue
                if e.marker:
                    pad = "  " * e.list_level
                    mk = e.marker if e.marker.startswith(("1", "2", "3", "4", "5", "6", "7", "8", "9")) else "-"
                    out.append(f"{pad}{mk} {text}")
                else:
                    out.append(f"\n{text}\n")
            elif isinstance(e, Code):
                rows = [(i, t) for i, t, _b in e.lines]
                base = min((i for i, t in rows if t.strip()), default=0)
                body = []
                for i, t, b in e.lines:
                    body += [""] * b
                    if self.opts.fix_code_quotes:
                        t = t.translate(CODE_TRANS)
                    body.append((" " * (i - base) + t).rstrip() if t.strip() else "")
                while body and not body[-1]:
                    body.pop()
                if self.opts.strip_line_numbers:
                    body = strip_listing_numbers(body)
                code_text = "\n".join(body)
                lang = guess_code_language(code_text) if self.opts.detect_lang else ""
                out.append(f"\n```{lang}\n{code_text}\n```\n")
            elif isinstance(e, Pre):
                out.append("\n```\n" + "\n".join(x.rstrip() for x in e.lines) + "\n```\n")
            elif isinstance(e, PageMark):
                out.append(f"\n<!-- Page {e.label} -->\n")

        if self.footnotes_list and self.opts.footnotes != "drop":
            out.append("\n## Footnotes\n")
            for fn in self.footnotes_list:
                out.append(f"[^{fn.marker}]: (p. {fn.page}) {fn.text}")

        text = "\n".join(out)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return clean_text(text.strip("\n") + "\n")

    def render_jsonl(self, elements: list) -> str:
        records = []
        meta = self.metadata
        book_title = repair_metadata(meta.get("title") or "") or self.cover_title or self.path.stem
        curr_chapter = ""
        curr_section = ""
        item_id = 1

        for e in elements:
            if isinstance(e, Heading):
                if e.level == 1:
                    curr_chapter = e.text
                    curr_section = ""
                elif e.level == 2:
                    curr_section = e.text
                records.append({
                    "id": f"{self.path.stem}_{item_id:05d}",
                    "book": book_title,
                    "type": "heading",
                    "level": e.level,
                    "text": e.text,
                    "chapter": curr_chapter,
                    "section": curr_section,
                })
                item_id += 1
            elif isinstance(e, Para):
                text = self._join_para(e.lines)
                if not text:
                    continue
                records.append({
                    "id": f"{self.path.stem}_{item_id:05d}",
                    "book": book_title,
                    "type": "paragraph",
                    "text": text,
                    "chapter": curr_chapter,
                    "section": curr_section,
                    "list_marker": e.marker,
                    "char_count": len(text),
                    "token_estimate": max(1, len(text.split())),
                })
                item_id += 1
            elif isinstance(e, Code):
                rows = [(i, t) for i, t, _b in e.lines]
                base = min((i for i, t in rows if t.strip()), default=0)
                body = []
                for i, t, b in e.lines:
                    body += [""] * b
                    if self.opts.fix_code_quotes:
                        t = t.translate(CODE_TRANS)
                    body.append((" " * (i - base) + t).rstrip() if t.strip() else "")
                while body and not body[-1]:
                    body.pop()
                if self.opts.strip_line_numbers:
                    body = strip_listing_numbers(body)
                code_text = "\n".join(body)
                if not code_text.strip():
                    continue
                lang = guess_code_language(code_text) if self.opts.detect_lang else ""
                records.append({
                    "id": f"{self.path.stem}_{item_id:05d}",
                    "book": book_title,
                    "type": "code",
                    "language": lang,
                    "text": code_text,
                    "chapter": curr_chapter,
                    "section": curr_section,
                    "char_count": len(code_text),
                    "token_estimate": max(1, len(code_text.split())),
                })
                item_id += 1
            elif isinstance(e, Pre):
                pre_text = "\n".join(x.rstrip() for x in e.lines)
                if not pre_text.strip():
                    continue
                records.append({
                    "id": f"{self.path.stem}_{item_id:05d}",
                    "book": book_title,
                    "type": "pre",
                    "text": pre_text,
                    "chapter": curr_chapter,
                    "section": curr_section,
                })
                item_id += 1

        return "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n"

    def stats(self) -> Counter:
        c = Counter()
        for e in self.elements:
            c[type(e).__name__] += 1
        return c


# --------------------------------------------------------------------------- #
#  Chapter splitting
# --------------------------------------------------------------------------- #
def split_by_chapters(extractor: BookExtractor, elements: list) -> list[tuple[str, list]]:
    chapters = []
    curr_title = "Front Matter"
    curr_els = []
    idx = 0
    for e in elements:
        if isinstance(e, Heading) and e.level == 1:
            if curr_els:
                chapters.append((f"{idx:02d}_{slugify(curr_title)}", curr_els))
                idx += 1
            curr_title = e.text
            curr_els = [e]
        else:
            curr_els.append(e)
    if curr_els:
        chapters.append((f"{idx:02d}_{slugify(curr_title)}", curr_els))
    return chapters


def slugify(s: str) -> str:
    s = norm_key(s)
    return s[:40] if s else "chapter"


def parse_page_ranges(spec: str, total_pages: int) -> list[int]:
    pages = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            start = int(a) if a else 1
            end = int(b) if b else total_pages
            for p in range(max(1, start), min(total_pages, end) + 1):
                pages.add(p - 1)
        else:
            p = int(part)
            if 1 <= p <= total_pages:
                pages.add(p - 1)
    return sorted(pages)


# --------------------------------------------------------------------------- #
#  Execution, File Processing & CLI
# --------------------------------------------------------------------------- #
def process_file(pdf_path: Path, out_path: Optional[Path], opts: Options) -> bool:
    pdf_path = Path(pdf_path)
    if out_path is None and not opts.stdout:
        ext = ".md" if opts.format == "md" else (".jsonl" if opts.format == "jsonl" else ".txt")
        out_path = pdf_path.with_suffix(ext)

    if opts.skip_existing and out_path and out_path.exists() and out_path.stat().st_size > 0:
        log(f"  ⏭ Skipping {pdf_path.name} (output exists: {out_path.name})", opts)
        return True

    log(f"Processing: {pdf_path.name}", opts)
    ex = BookExtractor(pdf_path, opts)
    try:
        elements = ex.run()
    except Exception as exc:
        print(f"  ✗ Failed: {exc}", file=sys.stderr)
        return False

    rendered = ex.render(elements, include_header=True)

    if opts.stdout:
        sys.stdout.write(rendered)
        sys.stdout.flush()
        return True

    # Encoding: default write BOM unless --no-bom is passed
    out_path.parent.mkdir(parents=True, exist_ok=True)
    encoding = "utf-8" if opts.no_bom else "utf-8-sig"
    with open(out_path, "w", encoding=encoding, errors="replace") as f:
        f.write(rendered)

    if opts.split_chapters:
        folder = out_path.parent / (out_path.stem + "_chapters")
        folder.mkdir(parents=True, exist_ok=True)
        ext = ".md" if opts.format == "md" else (".jsonl" if opts.format == "jsonl" else ".txt")
        for slug, ch_elements in split_by_chapters(ex, elements):
            ch_text = ex.render(ch_elements, include_header=False)
            ch_file = folder / f"{slug}{ext}"
            with open(ch_file, "w", encoding=encoding, errors="replace") as f:
                f.write(ch_text)
        log(f"  ✓ Chapters written to: {folder}", opts)

    st = ex.stats()
    log(f"  ✓ Saved: {out_path}", opts)
    log(f"    {len(ex.pages)} pages | {st.get('Heading', 0)} headings | {st.get('Para', 0)} paragraphs | "
        f"{st.get('Code', 0)} code blocks | {len(rendered):,} chars | "
        f"headings from {'PDF bookmarks' if ex.toc_used else 'font analysis'} | {ex.elapsed:.1f}s", opts)
    for w in ex.warnings:
        log(f"  ⚠ {w}", opts)
    return True


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pdf-book-to-text",
        description="Extract clean, structured text, Markdown, and RAG chunks from PDF books.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  %(prog)s book.pdf\n"
               "  %(prog)s book.pdf --format md -o book.md\n"
               "  %(prog)s book.pdf --format jsonl -o book.jsonl\n"
               "  %(prog)s ~/Books/ -r -j 4 --skip-existing\n"
               "  %(prog)s scanned.pdf --ocr --ocr-lang eng\n",
    )
    p.add_argument("inputs", nargs="*", help="PDF file(s) or folder(s). If omitted you will be asked.")
    p.add_argument("-o", "--output", help="Output file (single input) or destination folder (batch).")
    p.add_argument("-f", "--format", choices=["txt", "md", "jsonl"], default="txt",
                   help="Output format: txt (plain text), md (Markdown with ```lang), jsonl (RAG chunks). Default: txt.")
    p.add_argument("-r", "--recursive", action="store_true", help="Search folders recursively for PDFs.")
    p.add_argument("-w", "--width", type=int, default=100,
                   help="Wrap paragraphs at N characters (0 = no wrap). Code is never wrapped. Default: 100.")
    p.add_argument("--code-style", choices=["fence", "indent", "plain"], default="fence",
                   help="Code listing style in plain text format: ``` fence (default), 4-space indent, or plain.")
    p.add_argument("--no-detect-lang", dest="detect_lang", action="store_false",
                   help="Disable automatic programming language detection for code fences.")
    p.add_argument("--no-strip-line-numbers", dest="strip_line_numbers", action="store_false",
                   help="Do not strip leading line numbers from code listings.")
    p.add_argument("--footnotes", choices=["section", "inline", "drop"], default="section",
                   help="Footnote handling: section (append at end), inline, or drop. Default: section.")
    p.add_argument("-j", "--jobs", type=int, default=1,
                   help="Number of parallel worker processes for batch extraction. Default: 1.")
    p.add_argument("--skip-existing", action="store_true", help="Skip files that already have target output.")
    p.add_argument("--no-bom", action="store_true", help="Write UTF-8 without BOM (recommended for Unix pipelines).")
    p.add_argument("--stdout", action="store_true", help="Output text to standard output instead of writing a file.")
    p.add_argument("--pages", help="Page range(s) to extract, e.g. '1-50' or '10-20,25,40-'.")
    p.add_argument("--page-markers", action="store_true", help="Insert [Page N] markers.")
    p.add_argument("--no-contents", dest="contents", action="store_false", help="Do not write table of contents at the top.")
    p.add_argument("--contents-depth", type=int, default=3, help="Depth of generated table of contents (default 3).")
    p.add_argument("--keep-headers", dest="strip_headers", action="store_false",
                   help="Keep running headers/footers and page numbers.")
    p.add_argument("--columns", choices=["auto", "1", "2"], default="auto", help="Column layout detection (default auto).")
    p.add_argument("--tables", action="store_true", help="Detect ruled tables and render them as ASCII/Markdown grids.")
    p.add_argument("--figures", action="store_true", help="Insert [Figure] placeholders where images are.")
    p.add_argument("--ocr", action="store_true", help="OCR scanned pages (requires Tesseract installed).")
    p.add_argument("--ocr-lang", default="eng", help="Tesseract language(s), e.g. 'eng' or 'ara'.")
    p.add_argument("--password", help="Password for encrypted PDFs.")
    p.add_argument("--split-chapters", action="store_true", help="Also write one file per chapter into <name>_chapters/.")
    p.add_argument("--ignore-bookmarks", dest="use_bookmarks", action="store_false",
                   help="Ignore PDF outline; detect headings from fonts only.")
    p.add_argument("--keep-code-quotes", dest="fix_code_quotes", action="store_false",
                   help="Do not convert curly quotes/dashes to ASCII inside code.")
    p.add_argument("--mark-inline-code", action="store_true", help="Wrap inline monospace words in `backticks`.")
    p.add_argument("-q", "--quiet", action="store_true", help="No progress output.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def collect_pdfs(inputs: list[str], recursive: bool) -> list[Path]:
    found = []
    for raw in inputs:
        s = raw.strip().strip("'\"")
        if s.startswith("file://"):
            s = s[7:]
        path = Path(s).expanduser()
        if not path.exists():
            parent = path.parent
            if parent.is_dir():
                clean_target = clean_text(unicodedata.normalize("NFC", path.name)).replace("\ufffd", "").strip()
                for cand in parent.glob("*.pdf"):
                    clean_cand = clean_text(unicodedata.normalize("NFC", cand.name)).replace("\ufffd", "").strip()
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


def _worker_task(args_tuple: tuple[Path, Optional[Path], Options]) -> bool:
    pdf, target, opts = args_tuple
    return process_file(pdf, target, opts)


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    inputs = args.inputs
    if not inputs:
        try:
            s = input("Enter path to a PDF book (or folder): ").strip()
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
        format=args.format, width=args.width, code_style=args.code_style, pages=args.pages,
        page_markers=args.page_markers, contents=args.contents, contents_depth=args.contents_depth,
        strip_headers=args.strip_headers, columns=args.columns, tables=args.tables, figures=args.figures,
        ocr=args.ocr, ocr_lang=args.ocr_lang, password=args.password, split_chapters=args.split_chapters,
        use_bookmarks=args.use_bookmarks, fix_code_quotes=args.fix_code_quotes,
        mark_inline_code=args.mark_inline_code, detect_lang=args.detect_lang,
        strip_line_numbers=args.strip_line_numbers, footnotes=args.footnotes,
        no_bom=args.no_bom, stdout=args.stdout, jobs=args.jobs,
        skip_existing=args.skip_existing, quiet=args.quiet,
    )

    out = Path(args.output).expanduser() if args.output else None
    ext = ".md" if opts.format == "md" else (".jsonl" if opts.format == "jsonl" else ".txt")

    # Common base directory calculation for subfolder preservation
    try:
        common_base = Path(os.path.commonpath([p.parent for p in pdfs]))
    except Exception:
        common_base = None

    tasks = []
    for pdf in pdfs:
        target = out
        if out is not None and (out.is_dir() or len(pdfs) > 1 or (not out.suffix and not out.exists())):
            if common_base and common_base != pdf.parent:
                try:
                    rel = pdf.relative_to(common_base)
                    target = out / rel.with_suffix(ext)
                except ValueError:
                    target = out / (pdf.stem + ext)
            else:
                target = out / (pdf.stem + ext)
        tasks.append((pdf, target, opts))

    ok = 0
    if opts.jobs > 1 and len(tasks) > 1 and not opts.stdout:
        log(f"Starting parallel extraction using {opts.jobs} workers...", opts)
        with concurrent.futures.ProcessPoolExecutor(max_workers=min(opts.jobs, len(tasks))) as pool:
            futures = {pool.submit(_worker_task, t): t[0] for t in tasks}
            for fut in concurrent.futures.as_completed(futures):
                p = futures[fut]
                try:
                    if fut.result():
                        ok += 1
                except Exception as exc:
                    print(f"  ✗ {p.name}: {exc}", file=sys.stderr)
    else:
        for t in tasks:
            if _worker_task(t):
                ok += 1

    if len(pdfs) > 1:
        log(f"\nDone: {ok}/{len(pdfs)} file(s) converted.", opts)
    return 0 if ok == len(pdfs) else 2


if __name__ == "__main__":
    sys.exit(main())
