"""Low-level PyMuPDF rawdict extraction, glyph spacing reconstruction, and baseline merging."""
from __future__ import annotations

import re
import statistics
from typing import Any, Optional, Set

from pdf_extractor.constants import (
    BOLD_FONT_RE,
    CHAR_TRANS,
    MATH_FONT_RE,
    MONO_FONT_RE,
    SYMBOL_FONT_RE,
    TEXT_FLAGS,
)
from pdf_extractor.models import Line, Seg
from pdf_extractor.sanitize import clean_text


def _estimate_mono_advance(items: list[tuple[float, float, str]], size: float) -> float:
    """Compute true monospace advance width from consecutive origin steps (fallback to ink width)."""
    steps: list[float] = []
    for (x0_a, _x1_a, ch_a), (x0_b, _x1_b, ch_b) in zip(items, items[1:]):
        if ch_a.isspace() or ch_b.isspace():
            continue
        dx = x0_b - x0_a
        if 0.35 * size <= dx <= 0.95 * size:
            steps.append(dx)
    if steps:
        return statistics.median(steps)

    widths = [x1 - x0 for x0, x1, c in items if not c.isspace() and len(c) == 1 and x1 > x0]
    cw = statistics.median(widths) if widths else size * 0.6
    if cw <= 0.05 * size:
        cw = size * 0.6
    return cw


def make_seg(span: dict[str, Any], mono_fonts: Optional[Set[str]] = None) -> Optional[Seg]:
    """Convert one PyMuPDF rawdict span dict into a clean geometric Seg."""
    chars = span.get("chars") or []
    if not chars:
        return None

    font = (span.get("font") or "").split("+")[-1]
    flags = int(span.get("flags", 0))
    mono = bool(flags & 8) or bool(MONO_FONT_RE.search(font)) or (mono_fonts is not None and font in mono_fonts)
    bold = bool(flags & 16) or bool(BOLD_FONT_RE.search(font))
    math = bool(MATH_FONT_RE.search(font))
    size = float(span.get("size", 0)) or 1.0

    items: list[tuple[float, float, str]] = []
    for ch in chars:
        c = clean_text(ch["c"].translate(CHAR_TRANS))
        if not c:
            continue
        ox = float(ch["origin"][0])
        bx1 = float(ch["bbox"][2])
        items.append((ox, max(bx1, ox), c))

    while items and items[0][2].isspace():
        items.pop(0)
    while items and items[-1][2].isspace():
        items.pop()
    if not items:
        return None

    cw = _estimate_mono_advance(items, size)

    if SYMBOL_FONT_RE.search(font) and len("".join(c for *_x, c in items).strip()) <= 2:
        text, mono = "•", False
    elif mono:
        # Step-by-step monospace reconstruction avoids cumulative drift ('www.packtpub .com')
        ordered_items = sorted(items, key=lambda it: it[0])
        out: list[str] = []
        prev_ox: Optional[float] = None
        pending_space = False
        for ox, _bx1, c in ordered_items:
            if c.isspace():
                pending_space = True
                continue
            if prev_ox is not None:
                step = ox - prev_ox
                # Emit computed spaces for visual gaps (>= 1.45*cw) or explicit single space glyph
                if step >= 1.45 * cw:
                    n_spaces = max(1, int(round(step / cw)) - 1)
                    out.append(" " * n_spaces)
                elif pending_space and step >= 0.85 * cw:
                    out.append(" ")
            out.append(c)
            prev_ox = ox
            pending_space = False
        text = clean_text("".join(out))
    else:
        text = clean_text(re.sub(r" {2,}", " ", "".join(c for _a, _b, c in items)))

    if not text.strip():
        return None

    min_x = min(min(ox, bx1) for ox, bx1, _c in items)
    max_x = max(max(ox, bx1) for ox, bx1, _c in items)
    return Seg(min_x, max_x, text, size, mono, bold, cw, float(span["origin"][1]), font, bool(flags & 1), math)


def extract_raw_lines_from_dict(d: dict[str, Any], mono_fonts: Optional[Set[str]] = None) -> list[Line]:
    """Extract horizontal lines from an already-parsed PyMuPDF rawdict payload."""
    out: list[Line] = []
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            dx, dy = ln.get("dir", (1, 0))
            if abs(dy) > 0.10 or dx < 0:
                continue
            segs = [s for s in (make_seg(sp, mono_fonts) for sp in ln.get("spans", [])) if s]
            if not segs:
                continue
            bb = ln["bbox"]
            out.append(Line(segs, float(bb[1]), float(bb[3])))
    return out


def extract_raw_lines(
    page: Any,
    textpage: Any = None,
    mono_fonts: Optional[Set[str]] = None,
    rawdict: Optional[dict[str, Any]] = None,
) -> list[Line]:
    """Extract horizontal visual lines from a PyMuPDF page or cached rawdict."""
    d = rawdict if rawdict is not None else page.get_text("rawdict", flags=TEXT_FLAGS, textpage=textpage)
    return extract_raw_lines_from_dict(d, mono_fonts=mono_fonts)


def merge_line_group(group: list[Line]) -> Line:
    """Merge non-overlapping visual fragments sitting on the same baseline."""
    segs = sorted((s for ln in group for s in ln.segs), key=lambda s: s.x0)
    dedup: list[Seg] = []
    for s in segs:
        if dedup and s.text == dedup[-1].text and abs(s.x0 - dedup[-1].x0) < 1.5:
            continue
        dedup.append(s)
    return Line(dedup, min(l.y0 for l in group), max(l.y1 for l in group))


def merge_baselines(lines: list[Line]) -> list[Line]:
    """Combine fragments sitting on the same baseline and sort top-to-bottom."""
    if not lines:
        return []
    ordered = sorted(lines, key=lambda l: (l.yc, l.x0))
    groups: list[list[Line]] = []
    for ln in ordered:
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
