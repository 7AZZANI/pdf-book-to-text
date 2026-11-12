"""Table formatting, figure clustering, and PDF callout-box detection."""
from __future__ import annotations

import textwrap
from typing import Any, Optional

try:
    import pymupdf
except ImportError:  # pragma: no cover
    import fitz as pymupdf

from pdf_extractor.constants import CALLOUT_KIND_RE, CAPTION_RE
from pdf_extractor.models import Line, Options
from pdf_extractor.sanitize import clean_ws


def render_table(rows: list[list[Any]], maxw: int = 40) -> list[str]:
    """Render structured table rows as an ASCII grid."""
    if not rows:
        return []
    norm_rows = [[clean_ws(str(c)) if c is not None else "" for c in r] for r in rows]
    ncols = max((len(r) for r in norm_rows), default=0)
    if ncols == 0:
        return []
    norm_rows = [r + [""] * (ncols - len(r)) for r in norm_rows]
    keep = [i for i in range(ncols) if any(r[i] for r in norm_rows)]
    norm_rows = [[r[i] for i in keep] for r in norm_rows]
    if not norm_rows or not norm_rows[0]:
        return []

    widths = [max(3, min(maxw, max(len(r[i]) for r in norm_rows))) for i in range(len(norm_rows[0]))]
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    hsep = "+" + "+".join("=" * (w + 2) for w in widths) + "+"
    out = [sep]
    for ri, r in enumerate(norm_rows):
        wrapped_cells = [textwrap.wrap(c, w, break_long_words=True) or [""] for c, w in zip(r, widths)]
        row_height = max(len(c) for c in wrapped_cells)
        for k in range(row_height):
            line = "| " + " | ".join((c[k] if k < len(c) else "").ljust(w) for c, w in zip(wrapped_cells, widths)) + " |"
            out.append(line)
        out.append(hsep if ri == 0 and len(norm_rows) > 1 else sep)
    return out


def render_md_table(rows: list[list[Any]]) -> list[str]:
    """Render structured table rows as a GitHub-Flavored Markdown table."""
    if not rows:
        return []
    norm_rows = [[clean_ws(str(c)).replace("|", "\\|") if c is not None else "" for c in r] for r in rows]
    ncols = max((len(r) for r in norm_rows), default=0)
    if ncols == 0:
        return []
    norm_rows = [r + [""] * (ncols - len(r)) for r in norm_rows]
    keep = [i for i in range(ncols) if any(r[i] for r in norm_rows)]
    norm_rows = [[r[i] for i in keep] for r in norm_rows]
    if not norm_rows or not norm_rows[0]:
        return []
    ncols = len(norm_rows[0])
    out = [
        "| " + " | ".join(norm_rows[0]) + " |",
        "| " + " | ".join("---" for _ in range(ncols)) + " |",
    ]
    for r in norm_rows[1:]:
        out.append("| " + " | ".join(r) + " |")
    return out


# Backwards-compatible alias tested by tests/test_extractor.py
render_table_md = render_md_table


def _cluster_rects(rects: list[pymupdf.Rect], pad: float = 12.0) -> list[pymupdf.Rect]:
    """Merge overlapping or adjacent raster tiles belonging to the same composite figure."""
    clusters: list[pymupdf.Rect] = []
    for r in rects:
        expanded = pymupdf.Rect(r.x0 - pad, r.y0 - pad, r.x1 + pad, r.y1 + pad)
        merged_idx: Optional[int] = None
        for idx, c in enumerate(clusters):
            if expanded.intersects(c):
                clusters[idx] = c | r
                merged_idx = idx
                break
        if merged_idx is None:
            clusters.append(pymupdf.Rect(r))
    return clusters


def _pop_caption(lines: list[Line], rect: pymupdf.Rect, prefix_filter: str) -> tuple[list[Line], str]:
    """Check lines tightly above/below rect for a Figure/Table caption and attach it."""
    best_idx: Optional[int] = None
    best_dist = 1e9
    for idx, ln in enumerate(lines):
        txt = clean_ws(ln.plain)
        if not CAPTION_RE.match(txt):
            continue
        if prefix_filter == "table" and not txt.lower().startswith("table"):
            continue
        if prefix_filter == "figure" and not txt.lower().startswith("fig"):
            continue
        # Horizontal overlap with figure/table bounds
        if ln.x1 < rect.x0 - 30 or ln.x0 > rect.x1 + 30:
            continue
        dist_below = ln.y0 - rect.y1
        dist_above = rect.y0 - ln.y1
        if 0 <= dist_below <= 32 and dist_below < best_dist:
            best_dist, best_idx = dist_below, idx
        elif 0 <= dist_above <= 26 and dist_above < best_dist:
            best_dist, best_idx = dist_above, idx
    if best_idx is None:
        return lines, ""
    cap = clean_ws(lines[best_idx].plain)
    kept = [ln for i, ln in enumerate(lines) if i != best_idx]
    return kept, cap


def extract_page_blocks(
    page: Any,
    lines: list[Line],
    width: float,
    height: float,
    body_size: float,
    opts: Options,
    scanned: bool,
) -> tuple[list[Line], list[tuple[Any, ...]]]:
    """Detect tables, figures, and boxed callouts on one page without stealing code/prose."""
    blocks: list[tuple[Any, ...]] = []
    claimed_rects: list[pymupdf.Rect] = []
    kept = list(lines)

    # 1. Ruled tables (--tables)
    if opts.tables and kept and not scanned:
        try:
            for t in page.find_tables().tables:
                data = t.extract()
                if len(data) < 2 or max((len(r) for r in data), default=0) < 2:
                    continue
                non_empty_rows = sum(1 for r in data if any(c and str(c).strip() for c in r))
                non_empty_cols = sum(
                    1
                    for ci in range(max(len(r) for r in data))
                    if any(ci < len(r) and r[ci] and str(r[ci]).strip() for r in data)
                )
                if non_empty_rows < 2 or non_empty_cols < 2:
                    continue
                rect = pymupdf.Rect(t.bbox)
                inside = [l for l in kept if rect.contains(pymupdf.Point((l.x0 + l.x1) / 2, l.yc))]
                # Guard: do not turn a boxed monospace code listing into a table
                if inside and sum(1 for l in inside if l.mono_ratio >= 0.8) / len(inside) > 0.65:
                    continue
                rendered = render_table(data)
                if not rendered:
                    continue
                kept = [l for l in kept if not rect.contains(pymupdf.Point((l.x0 + l.x1) / 2, l.yc))]
                kept, caption = _pop_caption(kept, rect, "table")
                claimed_rects.append(rect)
                blocks.append((rect.y0, rect.x0, "table", rendered, data, caption))
        except Exception:
            pass

    # 2. Figures (--figures)
    if opts.figures and not scanned:
        try:
            raw_rects = []
            for info in page.get_image_info():
                r = pymupdf.Rect(info["bbox"])
                if r.width > 0.18 * width and r.height > 0.07 * height:
                    # Skip full-page decorative/watermark background sheets on text pages
                    if r.width > 0.92 * width and r.height > 0.92 * height and len(kept) >= 4:
                        continue
                    raw_rects.append(r)
            for rect in _cluster_rects(raw_rects):
                kept, caption = _pop_caption(kept, rect, "figure")
                claimed_rects.append(rect)
                blocks.append((rect.y0, rect.x0, "figure", ["[Figure]"], None, caption))
        except Exception:
            pass

    # 3. Callout boxes via vector drawings (NOTE / WARNING / TIP / shaded callout cards)
    if opts.callouts and kept and not scanned:
        try:
            drawings = page.get_drawings()
            candidates: list[pymupdf.Rect] = []
            for drw in drawings:
                r = pymupdf.Rect(drw["rect"])
                if not (0.30 * width <= r.width <= 0.94 * width and 2.0 * body_size <= r.height <= 0.60 * height):
                    continue
                fill = drw.get("fill")
                color = drw.get("color")
                # Must have explicit background fill (non-pure-white) or visible stroke border
                has_fill = bool(fill) and any(v < 0.98 for v in fill[:3])
                has_stroke = bool(color) and float(drw.get("width") or 0) > 0.2
                if not (has_fill or has_stroke):
                    continue
                if any(r.intersects(cr) for cr in claimed_rects):
                    continue
                candidates.append(r)

            for rect in sorted(candidates, key=lambda r: (r.y0, r.x0)):
                inside = [
                    l
                    for l in kept
                    if l.x0 >= rect.x0 - 3 and l.x1 <= rect.x1 + 3 and l.yc >= rect.y0 and l.yc <= rect.y1
                ]
                if len(inside) < 1 or len(inside) > 25:
                    continue
                # Skip boxes containing predominantly monospace code listings
                if sum(1 for l in inside if l.mono_ratio >= 0.6) / len(inside) > 0.45:
                    continue
                inside.sort(key=lambda l: (l.y0, l.x0))
                first_txt = clean_ws(inside[0].plain)
                m = CALLOUT_KIND_RE.match(first_txt)
                if not m:
                    continue
                callout_kind = m.group(0).strip(" :").upper()
                box_lines = [clean_ws(l.plain) for l in inside if clean_ws(l.plain)]
                if not box_lines:
                    continue
                inside_ids = {id(l) for l in inside}
                kept = [l for l in kept if id(l) not in inside_ids]
                claimed_rects.append(rect)
                blocks.append((rect.y0, rect.x0, "callout", box_lines, None, callout_kind))
        except Exception:
            pass

    return kept, blocks
