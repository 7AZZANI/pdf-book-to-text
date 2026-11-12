"""Core AST elements, line/segment BiDi geometry models, and extraction options."""
from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass
from typing import Any, Optional, Union

from pdf_extractor.constants import RTL_RE
from pdf_extractor.sanitize import text_direction


class Seg:
    """A run of text rendered in one font/style along a baseline."""

    __slots__ = ("x0", "x1", "text", "size", "mono", "bold", "cw", "base", "nchars", "font", "sup", "prop", "math")

    def __init__(
        self,
        x0: float,
        x1: float,
        text: str,
        size: float,
        mono: bool,
        bold: bool,
        cw: float,
        base: float,
        font: str = "",
        sup: bool = False,
        math: bool = False,
    ):
        self.x0, self.x1, self.text, self.size = x0, x1, text, size
        self.mono, self.bold, self.cw, self.base = mono, bold, cw, base
        self.font, self.sup, self.prop, self.math = font, sup, False, math
        self.nchars = len(text.replace(" ", ""))


def _seg_kind(s: Seg) -> str:
    if RTL_RE.search(s.text):
        return "R"
    if re.search(r"[^\W_]", s.text):
        return "L"
    return "N"


def order_segments(segs: list[Seg], rtl: bool) -> list[Seg]:
    """Order line segments in reading order: LTR or RTL with embedded LTR runs preserved."""
    if not rtl:
        return sorted(segs, key=lambda s: s.x0)

    ordered = sorted(segs, key=lambda s: -s.x1)
    kinds = [_seg_kind(s) for s in ordered]
    out: list[Seg] = []
    i = 0
    n = len(ordered)
    while i < n:
        if kinds[i] == "L":
            j = last_strong = i
            while j + 1 < n and kinds[j + 1] in ("L", "N"):
                j += 1
                if kinds[j] == "L":
                    last_strong = j
            out.extend(reversed(ordered[i : last_strong + 1]))
            i = last_strong + 1
        else:
            out.append(ordered[i])
            i += 1
    return out


class Line:
    """One visual horizontal line merged from one or more Seg runs."""

    __slots__ = (
        "x0",
        "y0",
        "x1",
        "y1",
        "segs",
        "base",
        "size",
        "mono_ratio",
        "bold_ratio",
        "cw",
        "kind",
        "level",
        "text",
        "col",
        "nchars",
        "tabular",
        "block",
        "dropcap",
        "rtl",
        "prop",
    )

    def __init__(self, segs: list[Seg], y0: float, y1: float):
        joined_visual = " ".join(s.text for s in segs)
        self.rtl = text_direction(joined_visual) == "rtl"
        self.segs = order_segments(segs, self.rtl)
        self.y0, self.y1 = y0, y1
        self.kind = "text"
        self.level = 0
        self.text = ""
        self.col = 0
        self.tabular = False
        self.block: Any = None
        self.dropcap = False
        self.prop = False
        self.refresh()

    @property
    def is_rtl(self) -> bool:
        """Backwards-compatible alias for rtl attribute."""
        return self.rtl

    @staticmethod
    def _bidi_sort(segs: list[Seg]) -> list[Seg]:
        """Backwards-compatible BiDi ordering helper."""
        return order_segments(segs, rtl=True)

    def refresh(self) -> None:
        segs = self.segs
        self.x0 = min(s.x0 for s in segs)
        self.x1 = max(s.x1 for s in segs)
        n = sum(s.nchars for s in segs) or 1
        self.nchars = n
        self.mono_ratio = sum(s.nchars for s in segs if s.mono) / n
        self.bold_ratio = sum(s.nchars for s in segs if s.bold) / n
        dom = max(segs, key=lambda s: s.nchars)
        sizes: Counter[float] = Counter()
        for s in segs:
            sizes[round(s.size, 1)] += s.nchars
        self.size = sizes.most_common(1)[0][0]
        self.base = dom.base
        cws = [s.cw for s in segs if s.mono and s.cw > 0]
        self.cw = statistics.median(cws) if cws else 0.0
        self.prop = any(s.prop for s in segs)

    @property
    def plain(self) -> str:
        return " ".join(s.text for s in self.segs)

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2.0

    @property
    def h(self) -> float:
        return self.y1 - self.y0


class PageData:
    """Extracted page metadata, ordered lines, tables/figures/callouts, and column bounds."""

    def __init__(
        self,
        pno: int = 0,
        label: str = "1",
        width: float = 0.0,
        height: float = 0.0,
        lines: Optional[list[Line]] = None,
        blocks: Optional[list[Any]] = None,
        scanned: bool = False,
        garbled: bool = False,
    ):
        self.pno = pno
        self.label = label
        self.width = width
        self.height = height
        self.lines: list[Line] = lines if lines is not None else []
        self.blocks: list[Any] = blocks if blocks is not None else []
        self.scanned = scanned
        self.garbled = garbled
        self.ncols = 1
        self.gutter = 0.0
        self.rtl = False
        self.colm: dict[int, tuple[float, float]] = {}
        self.toc_unmatched: list[tuple[int, str]] = []


class Heading:
    __slots__ = ("level", "text", "from_font", "page", "pno")

    def __init__(self, level: int, text: str, from_font: bool = False):
        self.level = level
        self.text = text
        self.from_font = from_font
        self.page = ""
        self.pno = 0


class Para:
    __slots__ = ("lines", "marker", "list_level", "marker_x0", "first_x0", "rest_x0", "rtl", "page", "pno")

    def __init__(
        self,
        text: Union[str, list[str], tuple[str, ...]],
        marker: Optional[str] = None,
        list_level: int = 0,
        x0: float = 0.0,
    ):
        if isinstance(text, (list, tuple)):
            self.lines: list[str] = [str(t) for t in text if str(t) != ""]
        else:
            self.lines = [text] if text != "" else []
        self.marker = marker
        self.list_level = list_level
        self.marker_x0 = self.first_x0 = x0
        self.rest_x0: Optional[float] = None
        self.rtl = False
        self.page = ""
        self.pno = 0


class Code:
    # rows in lines: [indent_cols, text, blank_lines_before, rel_x0]
    __slots__ = ("lines", "lang", "prop", "rendered_lines", "page", "pno")

    def __init__(self, lang: str = ""):
        self.lines: list[list[Any]] = []
        self.lang = lang
        self.prop = False
        self.rendered_lines: Optional[list[str]] = None
        self.page = ""
        self.pno = 0


class Pre:
    """Structured preformatted element: ruled table, figure, callout, or aligned text."""

    __slots__ = ("lines", "kind", "rows", "caption", "callout_kind", "page", "pno")

    def __init__(
        self,
        lines: list[str],
        kind: str = "pre",
        rows: Optional[list[list[Any]]] = None,
        caption: str = "",
        callout_kind: str = "",
    ):
        self.lines = list(lines)
        self.kind = kind
        self.rows = rows
        self.caption = caption
        self.callout_kind = callout_kind
        self.page = ""
        self.pno = 0


class Note:
    """Extracted page footnote anchored cleanly to a page/section."""

    __slots__ = ("pno", "page", "mark", "text")

    def __init__(self, pno: int, mark: str, text: str, page: str = ""):
        self.pno = pno
        self.page = page or str(pno + 1)
        self.mark = mark
        self.text = text


# Backwards-compatible alias for callers checking Footnote
Footnote = Note


class PageMark:
    __slots__ = ("label", "pno")

    def __init__(self, label: str, pno: int = 0):
        self.label = label
        self.pno = pno


@dataclass
class Options:
    format: str = "txt"                   # txt | md | jsonl
    width: Optional[int] = None           # None -> 100 for txt, 0 (unwrapped) for md/jsonl
    code_style: str = "fence"             # fence | indent | plain
    pages: Optional[str] = None
    page_markers: bool = False
    contents: bool = True
    contents_depth: int = 3
    strip_headers: bool = True
    columns: str = "auto"                 # auto | 1 | 2
    tables: bool = False
    figures: bool = False
    callouts: bool = True                 # Detect boxed callout notes via PDF vector drawings
    ocr: bool = False
    ocr_lang: str = "eng"
    password: Optional[str] = None
    split_chapters: bool = False
    use_bookmarks: bool = True
    fix_code_quotes: bool = True
    mark_inline_code: bool = False
    detect_lang: bool = True
    code_lang: str = "auto"               # auto | none | <explicit language>
    detect_code: str = "auto"             # auto | font
    strip_line_numbers: bool = True
    footnotes: str = "section"            # section | inline | drop
    no_bom: bool = False
    stdout: bool = False
    jobs: int = 1
    skip_existing: bool = False
    chunk_tokens: int = 0                 # JSONL semantic RAG chunk target (0 = per-element)
    chunk_overlap: int = 0                # Word/token overlap for RAG chunking
    quiet: bool = False

    def __post_init__(self) -> None:
        self.format = (self.format or "txt").lower()
        if not self.detect_lang:
            self.code_lang = "none"

    @property
    def wrap_width(self) -> int:
        if self.width is None:
            return 100 if self.format == "txt" else 0
        return max(0, int(self.width))
