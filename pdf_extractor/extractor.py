"""Core BookExtractor pipeline: font profiling, layout recovery, classification, and assembly."""
from __future__ import annotations

import difflib
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional, Set, Tuple, Union

try:
    import pymupdf
except ImportError:  # pragma: no cover
    import fitz as pymupdf

from pdf_extractor.code_detect import code_score, guess_language, strip_code_listing_numbers
from pdf_extractor.constants import (
    CHAPTER_RE,
    CODE_TRANS,
    FN_MARK_RE,
    FN_TOKEN_RE,
    LABEL_RE,
    LEADER_RE,
    LETTERS2_RE,
    MATH_FONT_RE,
    MONO_FONT_RE,
    NUM_LIST_RE,
    PAGE_NUM_RE,
    STRONG_BULLET_RE,
    SYMBOL_FONT_RE,
    TERMINAL_RE,
    TEXT_FLAGS,
    TYPE3_FONT_RE,
    WEAK_BULLET_RE,
    WORD_BULLETS,
    WORD_RE,
)
from pdf_extractor.models import Code, Heading, Line, Note, Options, PageData, PageMark, Para, Pre, Seg
from pdf_extractor.raw import extract_raw_lines, merge_baselines
from pdf_extractor.sanitize import clean_text, clean_ws, is_rtl_text, norm_key, repair_metadata
from pdf_extractor.tables import extract_page_blocks


def log(msg: str, opts: Options, end: str = "\n") -> None:
    """Print progress updates cleanly unless quiet mode is active."""
    if not opts.quiet:
        print(msg, end=end, flush=True, file=sys.stderr if opts.stdout else sys.stdout)


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


def _mode_of(values: list[float], ndigits: int = 1, default: float = 0.0) -> float:
    if not values:
        return default
    return Counter(round(v, ndigits) for v in values).most_common(1)[0][0]


class BookExtractor:
    """Structure-aware PDF book extraction engine."""

    def __init__(self, path: Union[Path, str], opts: Optional[Options] = None):
        self.path = Path(path)
        self.opts = opts or Options()
        self.doc: Optional[pymupdf.Document] = None
        self.metadata: dict[str, Any] = {}
        self.cover_title: Optional[str] = None
        self.pages: list[PageData] = []
        self.warnings: list[str] = []
        self.toc_entries: list[list[Any]] = []
        self.toc_used: bool = False
        self.toc_max: int = 0
        self.vocab: Set[str] = set()
        self.mono_fonts: Set[str] = set()
        self.proportional_code_styles: Set[Tuple[str, float]] = set()
        self.code_source: str = "none"  # font | style | syntax | none
        self.body_size: float = 10.0
        self.mono_size: float = 10.0
        self.has_mono: bool = False
        self.mono_share: float = 0.0
        self.body_pitch: float = 12.0
        self.code_pitch: float = 12.0
        self.notes: dict[int, list[tuple[str, str]]] = {}
        self.footnotes_list: list[Note] = []
        self.elements: list[Any] = []
        self.elapsed: float = 0.0
        self._mark_pno: int = 0
        self._marks: Set[str] = set()
        self._raw_cache: dict[int, dict[str, Any]] = {}

    def __enter__(self) -> BookExtractor:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Close underlying PyMuPDF document handle safely and clear caches."""
        self._raw_cache.clear()
        if self.doc is not None:
            try:
                if not self.doc.is_closed:
                    self.doc.close()
            except Exception:
                pass
            self.doc = None

    def _get_rawdict(self, pno: int, cache: bool = True) -> dict[str, Any]:
        """Fetch rawdict for a page, caching early/sample reads to avoid duplicate PyMuPDF parses."""
        if pno in self._raw_cache:
            return self._raw_cache[pno]
        assert self.doc is not None
        d = self.doc[pno].get_text("rawdict", flags=TEXT_FLAGS)
        if cache:
            self._raw_cache[pno] = d
        return d

    def run(self) -> list[Any]:
        """Run the complete extraction pipeline and return normalized AST elements."""
        from pdf_extractor.cli import parse_page_ranges

        opts = self.opts
        t0 = time.time()
        self.pages = []
        self.warnings = []
        self.toc_entries = []
        self.toc_used = False
        self.toc_max = 0
        self.notes = {}
        self.footnotes_list = []
        self.proportional_code_styles = set()
        self._raw_cache.clear()

        doc = pymupdf.open(self.path)
        self.doc = doc
        try:
            if doc.needs_pass and not doc.authenticate(opts.password or ""):
                raise RuntimeError("PDF is password protected - use --password")

            n = doc.page_count
            indices = parse_page_ranges(opts.pages, n) if opts.pages else list(range(n))
            if not indices:
                raise RuntimeError("No pages selected")

            self.metadata = dict(doc.metadata or {})
            try:
                raw_toc = doc.get_toc(simple=True)
            except Exception:
                raw_toc = []

            self.cover_title = self._find_cover_title()
            self._profile_document_fonts()
            self._read_pages(indices)
            self._raw_cache.clear()

            self._compute_font_stats()
            self._detect_code_styles()
            if opts.strip_headers:
                self._remove_headers_footers()

            for pd in self.pages:
                self._extract_footnotes(pd)
                self._order_page(pd)
                self._mark_pno = pd.pno
                self._marks = {mk for mk, _t in self.notes.get(pd.pno, [])}
                for ln in pd.lines:
                    self._classify(ln)
                if self.code_source == "none" and opts.detect_code == "auto" and not pd.scanned:
                    self._detect_syntax_code(pd)
                self._insert_blocks(pd)

            if self.code_source == "none" and any(
                isinstance(it, Line) and it.kind == "code" for pd in self.pages for it in pd.lines
            ):
                self.code_source = "syntax"

            self._compute_layout_stats()
            self._build_vocab()
            if opts.use_bookmarks and raw_toc:
                self._apply_bookmarks(raw_toc, n)
            self._assign_font_heading_levels()
            self._promote_cover_h1_if_needed()

            page_elements = [(pd, self._build_page_elements(pd)) for pd in self.pages]
            elements = self._assemble(page_elements)
            elements = self._post_process(elements)
            self.elements = elements
            self.elapsed = time.time() - t0
            return elements
        finally:
            self.close()

    # ------------------------------------------------------------------ #
    #  Single-pass Cover Title & Font Profiling
    # ------------------------------------------------------------------ #
    def _find_cover_title(self) -> Optional[str]:
        assert self.doc is not None
        candidates: list[tuple[float, int, float, str]] = []
        for pno in range(min(4, self.doc.page_count)):
            d = self._get_rawdict(pno, cache=True)
            for b in d.get("blocks", []):
                if b.get("type") != 0:
                    continue
                for ln in b.get("lines", []):
                    dx, dy = ln.get("dir", (1, 0))
                    if abs(dy) > 0.10 or dx < 0:
                        continue
                    spans = ln.get("spans", [])
                    txt = clean_ws(
                        clean_text(
                            " ".join("".join(c["c"] for c in sp.get("chars", [])) for sp in spans)
                        )
                    )
                    if not txt or len(txt) > 120 or PAGE_NUM_RE.match(txt) or len(txt) <= 1:
                        continue
                    if re.search(r"copyright|isbn|all rights|http|\.com|edition|license", txt, re.I):
                        continue
                    sz = max((float(sp.get("size", 0)) for sp in spans), default=0.0)
                    y0 = float(ln.get("bbox", (0.0, 0.0, 0.0, 0.0))[1])
                    candidates.append((sz, pno, y0, txt))
        if not candidates:
            return None
        top_sz, top_pno, top_y, _top_txt = max(candidates, key=lambda c: (c[0], -c[1], -c[2]))
        same_block = sorted(
            [
                (y0, txt)
                for sz, pno, y0, txt in candidates
                if pno == top_pno and abs(sz - top_sz) <= max(1.5, 0.15 * top_sz) and top_y - 5 <= y0 <= top_y + top_sz * 3.8
            ],
            key=lambda item: item[0],
        )
        title = clean_ws(" ".join(txt for _y, txt in same_block))
        return title[:140] if title else None

    def _profile_document_fonts(self) -> None:
        assert self.doc is not None
        n = self.doc.page_count
        sample = sorted(
            set(list(range(min(6, n))) + [int(i * (n - 1) / 40) for i in range(41)])
        ) if n else []

        mono_fonts: Set[str] = set()
        prop_fonts: Set[str] = set()
        font_advances: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

        for pno in sample:
            if pno >= n:
                continue
            d = self._get_rawdict(pno, cache=True)
            for b in d.get("blocks", []):
                if b.get("type") != 0:
                    continue
                for ln in b.get("lines", []):
                    for sp in ln.get("spans", []):
                        fn = (sp.get("font") or "").split("+")[-1]
                        if not fn or TYPE3_FONT_RE.search(fn) or SYMBOL_FONT_RE.search(fn) or MATH_FONT_RE.search(fn):
                            prop_fonts.add(fn)
                            continue
                        flags = int(sp.get("flags", 0))
                        if (flags & 8) or MONO_FONT_RE.search(fn):
                            mono_fonts.add(fn)
                            continue
                        if fn in mono_fonts or fn in prop_fonts:
                            continue
                        sz = float(sp.get("size", 1.0)) or 1.0
                        chars = sp.get("chars") or []
                        for idx, ch in enumerate(chars[:-1]):
                            c = ch["c"]
                            if not (c.isascii() and c.isalpha()):
                                continue
                            nxt = chars[idx + 1]
                            adv = (float(nxt["origin"][0]) - float(ch["origin"][0])) / sz
                            if 0.25 <= adv <= 1.10 and len(font_advances[fn][c]) < 12:
                                font_advances[fn][c].append(adv)

        for fn, adv_map in font_advances.items():
            if fn in mono_fonts or fn in prop_fonts:
                continue
            med = {ch: statistics.median(vals) for ch, vals in adv_map.items() if vals}
            if "i" in med and "m" in med and abs(med["i"] - med["m"]) < 0.05:
                mono_fonts.add(fn)
            elif "l" in med and "w" in med and abs(med["l"] - med["w"]) < 0.05:
                mono_fonts.add(fn)
            elif "I" in med and "W" in med and len(med) >= 10 and max(med.values()) - min(med.values()) < 0.05:
                mono_fonts.add(fn)
            else:
                prop_fonts.add(fn)

        self.mono_fonts = mono_fonts

    # ------------------------------------------------------------------ #
    #  Page Ingestion (cached rawdict + OCR + Tables/Figures/Callouts)
    # ------------------------------------------------------------------ #
    def _read_pages(self, indices: list[int]) -> None:
        assert self.doc is not None
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

            width, height = float(page.rect.width), float(page.rect.height)
            cached = self._raw_cache.pop(pno, None)
            lines = extract_raw_lines(page, mono_fonts=self.mono_fonts, rawdict=cached)

            scanned = False
            if not lines or sum(l.nchars for l in lines) < 15:
                if page.get_images(full=False):
                    scanned = True
                    if opts.ocr and not ocr_failed:
                        try:
                            tp = page.get_textpage_ocr(
                                flags=TEXT_FLAGS, language=opts.ocr_lang, dpi=300, full=True
                            )
                            lines = extract_raw_lines(page, textpage=tp, mono_fonts=self.mono_fonts)
                            scanned = False
                        except Exception as exc:
                            ocr_failed = True
                            self.warnings.append(f"OCR unavailable ({exc}); scanned pages skipped.")

            all_chars = "".join(s.text for l in lines for s in l.segs)
            bad = sum(1 for c in all_chars if c == "\ufffd" or "\ue000" <= c <= "\uf8ff")
            garbled = len(all_chars) > 20 and (bad / len(all_chars)) > 0.25

            lines, blocks = extract_page_blocks(
                page=page,
                lines=lines,
                width=width,
                height=height,
                body_size=10.0,
                opts=opts,
                scanned=scanned,
            )

            self.pages.append(PageData(pno, label, width, height, lines, blocks, scanned, garbled))

        log("", opts)
        sc = sum(1 for pd in self.pages if pd.scanned)
        gb = sum(1 for pd in self.pages if pd.garbled)
        if sc:
            msg = f"{sc} page(s) look scanned (image only, no text layer)."
            if not opts.ocr:
                msg += " Use --ocr to OCR them (needs Tesseract)."
            self.warnings.append(msg)
        if gb:
            self.warnings.append(
                f"{gb} page(s) contain garbled text (fonts without Unicode mapping); --ocr may give better results."
            )

    def _compute_font_stats(self) -> None:
        body: Counter[float] = Counter()
        mono: Counter[float] = Counter()
        for pd in self.pages:
            for l in pd.lines:
                for s in l.segs:
                    (mono if s.mono else body)[round(s.size * 2) / 2] += s.nchars

        self.body_size = body.most_common(1)[0][0] if body else (mono.most_common(1)[0][0] if mono else 10.0)
        self.mono_size = mono.most_common(1)[0][0] if mono else self.body_size
        self.has_mono = sum(mono.values()) > 0
        total = sum(body.values()) + sum(mono.values())
        self.mono_share = sum(mono.values()) / total if total else 0.0

    def _detect_code_styles(self) -> None:
        """Identify (font_name, size) proportional fonts used for code listings."""
        if self.mono_share > 0.02:
            self.code_source = "font"
            return
        self.code_source = "none"
        if self.opts.detect_code != "auto":
            return

        style_chars: Counter[Tuple[str, float]] = Counter()
        for pd in self.pages:
            for l in pd.lines:
                for s in l.segs:
                    if s.font:
                        style_chars[(s.font, round(s.size, 1))] += s.nchars
        if not style_chars:
            return
        body_style = style_chars.most_common(1)[0][0]

        stats: dict[Tuple[str, float], list[int]] = defaultdict(lambda: [0, 0])
        for pd in self.pages:
            for l in pd.lines:
                if l.rtl:
                    continue
                # Skip tabular multi-column text lines before profiling
                ordered_segs = sorted(l.segs, key=lambda s: s.x0)
                if any((b.x0 - a.x1) > 2.5 * max(a.size, b.size) for a, b in zip(ordered_segs, ordered_segs[1:])):
                    continue
                dom = max(l.segs, key=lambda s: s.nchars)
                st = (dom.font, round(dom.size, 1))
                if not dom.font or st == body_style or dom.size > self.body_size * 1.05 or dom.bold:
                    continue
                stats[st][0] += 1
                stats[st][1] += 1 if code_score(l.plain) >= 2 else 0

        code_styles = {st for st, (n_lines, hits) in stats.items() if n_lines >= 6 and (hits / n_lines) >= 0.40}
        if not code_styles:
            return

        self.proportional_code_styles = code_styles
        for pd in self.pages:
            for l in pd.lines:
                hit = False
                for s in l.segs:
                    if (s.font, round(s.size, 1)) in code_styles:
                        s.mono = s.prop = hit = True
                if hit:
                    l.refresh()
        self.code_source = "style"
        self._compute_font_stats()

    # ------------------------------------------------------------------ #
    #  Headers/Footers & Footnotes
    # ------------------------------------------------------------------ #
    def _remove_headers_footers(self) -> None:
        zone = 0.12
        seen: dict[str, set[int]] = defaultdict(set)
        top_y_counts: Counter[float] = Counter()
        bot_y_counts: Counter[float] = Counter()

        def norm_hdr(t: str) -> str:
            return re.sub(r"\s+", " ", re.sub(r"\d+", "#", t.lower())).strip()

        def in_zone(l: Line, pd: PageData) -> bool:
            return l.y1 < pd.height * zone or l.y0 > pd.height * (1.0 - zone)

        for i, pd in enumerate(self.pages):
            for l in pd.lines:
                if in_zone(l, pd) and l.mono_ratio < 0.8:
                    seen[norm_hdr(l.plain)].add(i)
                    if l.y1 < pd.height * zone:
                        top_y_counts[round(l.y0, 0)] += 1
                    elif l.y0 > pd.height * (1.0 - zone):
                        bot_y_counts[round(l.y0, 0)] += 1

        n_pages = len(self.pages)
        min_rep = 3 if n_pages >= 6 else 10**9
        repeating = {k for k, v in seen.items() if len(v) >= min_rep and re.search(r"[^\W\d_]", k)}

        min_band = max(4, int(n_pages * 0.08)) if n_pages >= 10 else 10**9
        top_bands = {y for y, c in top_y_counts.items() if c >= min_band}
        bot_bands = {y for y, c in bot_y_counts.items() if c >= min_band}

        for pd in self.pages:
            kept: list[Line] = []
            for l in pd.lines:
                if in_zone(l, pd) and l.mono_ratio < 0.8:
                    t = l.plain.strip()
                    if PAGE_NUM_RE.match(t) and l.size <= self.body_size * 1.25:
                        continue
                    if norm_hdr(t) in repeating:
                        continue
                    if l.size <= self.body_size * 1.15 and len(t) <= 85 and l.bold_ratio < 0.9:
                        if any(abs(round(l.y0, 0) - ty) <= 3.0 for ty in top_bands):
                            continue
                        if any(abs(round(l.y0, 0) - by) <= 3.0 for by in bot_bands):
                            continue
                kept.append(l)
            pd.lines = kept

    def _extract_footnotes(self, pd: PageData) -> None:
        """Isolate contiguous small-print bottom footnotes (including continuation lines)."""
        body = self.body_size
        lines = sorted(pd.lines, key=lambda l: (l.y0, l.x0))
        if len(lines) < 4:
            return

        def small(l: Line) -> bool:
            return body * 0.50 <= l.size <= body * 0.88 and l.mono_ratio < 0.5

        k = len(lines)
        while k > 0 and small(lines[k - 1]) and lines[k - 1].y0 > pd.height * 0.50:
            k -= 1
        cand = lines[k:]
        if not cand or k == 0 or len(cand) > 0.5 * len(lines):
            return
        if not any(abs(l.size - body) < 0.6 and l.mono_ratio < 0.5 for l in lines[:k]):
            return
        if cand[0].y0 < lines[k - 1].y1 - 1.0:
            return

        notes: list[list[Any]] = []
        for ml in merge_baselines(cand):
            t = clean_ws(self._join_line_spans(ml, code=False))
            m = FN_MARK_RE.match(t)
            if m:
                notes.append([m.group(1), [t[m.end() :].strip()]])
            elif notes:
                notes[-1][1].append(t)
            else:
                return

        if not notes:
            return

        cand_ids = {id(l) for l in cand}
        pd.lines = [l for l in pd.lines if id(l) not in cand_ids]
        self.notes[pd.pno] = [(mk, self._join_para(parts)) for mk, parts in notes]

    # ------------------------------------------------------------------ #
    #  Column & Reading Order, Classification, Syntax Code
    # ------------------------------------------------------------------ #
    def _order_page(self, pd: PageData) -> None:
        lines = pd.lines
        body = self.body_size
        rtl_chars = sum(l.nchars for l in lines if l.rtl)
        pd.rtl = rtl_chars > sum(l.nchars for l in lines) - rtl_chars

        dropcaps = [l for l in lines if len(l.plain) == 1 and l.plain.isalpha() and l.size >= body * 1.8 and not l.rtl]
        for d in dropcaps:
            d.dropcap = True
        lines = [l for l in lines if not l.dropcap]

        mode = self.opts.columns
        ordered: Optional[list[Line]] = None
        if mode != "1" and len(lines) >= 8:
            W = pd.width
            best: Optional[tuple[tuple[int, float], int]] = None
            for x in range(int(W * 0.30), int(W * 0.70), 2):
                crossing = sum(1 for l in lines if l.x0 < x - 1 and l.x1 > x + 1)
                score = (crossing, abs(x - W / 2))
                if best is None or score < best[0]:
                    best = (score, x)
            assert best is not None
            gx = best[1]
            left = [l for l in lines if l.x1 <= gx + 1]
            right = [l for l in lines if l.x0 >= gx - 1]
            full = [l for l in lines if l not in left and l not in right]
            ok = False
            if len(left) >= 4 and len(right) >= 4 and len(full) <= 0.25 * len(lines):
                mono_right = sum(1 for l in right if l.mono_ratio >= 0.8) / len(right)
                overlap = sum(1 for r in right if any(a.y0 < r.y1 and r.y0 < a.y1 for a in left)) / len(right)
                ok = mode == "2" or (
                    mono_right < 0.4
                    and overlap >= 0.5
                    and statistics.median(l.x1 - l.x0 for l in left) > W * 0.2
                    and statistics.median(l.x1 - l.x0 for l in right) > W * 0.2
                )
            if ok:
                pd.ncols, pd.gutter = 2, float(gx)
                full.sort(key=lambda l: l.y0)
                ordered, prev_y = [], -1e9
                bounds = [l.y0 for l in full] + [1e9]
                fi = 0
                for b in bounds:
                    band_l = [l for l in left if prev_y <= l.yc < b]
                    band_r = [l for l in right if prev_y <= l.yc < b]
                    if pd.rtl:
                        ordered += merge_baselines(band_r) + merge_baselines(band_l)
                    else:
                        ordered += merge_baselines(band_l) + merge_baselines(band_r)
                    if fi < len(full):
                        ordered.append(full[fi])
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
            d.text = d.plain
            idx = len(ordered)
            for i, l in enumerate(ordered):
                if l.y1 > d.y0 and l.x0 > d.x0:
                    idx = i
                    break
            d.col = ordered[idx].col if idx < len(ordered) else 0
            ordered.insert(idx, d)

        pd.lines = ordered

    @staticmethod
    def _is_note_ref(s: Seg, ln: Line) -> bool:
        t = s.text.strip()
        if not re.fullmatch(r"\d{1,3}|[*†‡§¶]{1,3}", t):
            return False
        return s.sup or (s.size <= ln.size * 0.80 and s.base < ln.base - 0.12 * ln.size)

    def _join_line_spans(self, ln: Line, code: bool) -> str:
        """Assemble segments of one visual line, honoring physical spacing and optional tokens."""
        out: list[str] = []
        prev: Optional[Seg] = None
        mark = self.opts.mark_inline_code and not code
        marks = self._marks if (not code and self.opts.footnotes != "drop") else set()
        in_code = False

        for s in ln.segs:
            if marks and prev is not None and s.text.strip() in marks and self._is_note_ref(s, ln):
                out.append(f"\ue000{self._mark_pno}:{s.text.strip()}\ue001")
                prev = s
                continue

            if prev is not None:
                gap = max(s.x0 - prev.x1, prev.x0 - s.x1)
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

    def _fix_word_bullets(self, ln: Line) -> None:
        segs = ln.segs
        if len(segs) >= 2 and segs[0].text in WORD_BULLETS and segs[0].font != segs[1].font:
            gap = max(segs[1].x0 - segs[0].x1, segs[0].x0 - segs[1].x1)
            if gap >= 0.3 * segs[0].size:
                segs[0].text = "•"
                segs[0].mono = False
                ln.refresh()

    def _classify(self, ln: Line) -> None:
        if ln.kind == "dropcap" or ln.dropcap:
            ln.kind = "dropcap"
            ln.text = ln.plain
            return

        self._fix_word_bullets(ln)
        body = self.body_size
        plain = clean_ws(ln.plain)
        is_mono = ln.mono_ratio >= 0.80
        head_ref = max(body, self.mono_size) if is_mono else body

        if ln.size >= head_ref * 1.15 and len(plain) <= 200 and re.search(r"\w", plain) and not LEADER_RE.search(plain):
            ln.kind = "heading"
            ln.text = plain
        elif is_mono:
            ln.kind = "code"
            ln.text = self._join_line_spans(ln, code=True)
        else:
            ln.kind = "text"
            ln.text = self._join_line_spans(ln, code=False)

    def _detect_syntax_code(self, pd: PageData) -> int:
        lines = pd.lines
        n = len(lines)
        scores = [
            code_score(l.plain) if l.kind == "text" and not l.rtl and not l.tabular else 0
            for l in lines
        ]
        hits = 0
        i = 0
        while i < n:
            if scores[i] >= 2:
                j = i
                while j + 1 < n and scores[j + 1] >= 2 and lines[j + 1].col == lines[i].col:
                    j += 1
                if j > i or scores[i] >= 3:
                    for t in range(i, j + 1):
                        ln = lines[t]
                        ln.kind = "code"
                        ln.prop = True
                        if not ln.cw:
                            ln.cw = ln.size * 0.5
                        ln.text = self._join_line_spans(ln, code=True)
                        hits += 1
                i = j + 1
            else:
                i += 1
        return hits

    def _insert_blocks(self, pd: PageData) -> None:
        for blk in sorted(pd.blocks, key=lambda b: (b[0], b[1])):
            y0, x0, kind, text_lines, rows = blk[:5]
            extra = blk[5] if len(blk) > 5 else ""
            fake = Line([Seg(x0, x0 + 1, "x", self.body_size, False, False, 1.0, y0)], y0, y0 + 1)
            fake.kind = kind
            fake.block = (text_lines, rows, extra)
            col = 1 if pd.ncols == 2 and x0 >= pd.gutter - 1 else 0
            fake.col = col
            idx = len(pd.lines)
            for i, ln in enumerate(pd.lines):
                if ln.col == col and ln.y0 >= y0:
                    idx = i
                    break
            pd.lines.insert(idx, fake)

    # ------------------------------------------------------------------ #
    #  Layout Stats, Vocabulary, Bookmarks & Headings
    # ------------------------------------------------------------------ #
    def _compute_layout_stats(self) -> None:
        body = self.body_size
        bp: list[float] = []
        cp: list[float] = []
        acc: dict[tuple[int, int, int], tuple[list[float], list[float]]] = defaultdict(lambda: ([], []))

        for pd in self.pages:
            ls = pd.lines
            for a, b in zip(ls, ls[1:]):
                if a.col != b.col:
                    continue
                d = b.base - a.base
                if (
                    a.kind == b.kind == "text"
                    and abs(a.size - body) < 0.6
                    and abs(b.size - body) < 0.6
                    and 0.85 * body <= d <= 1.8 * body
                ):
                    bp.append(d)
                elif a.kind == b.kind == "code" and 0.8 * a.size <= d <= 1.9 * a.size:
                    cp.append(d)

            by_col: dict[int, list[Line]] = defaultdict(list)
            for l in ls:
                if l.kind in ("text", "code"):
                    by_col[l.col].append(l)

            for col, lst in by_col.items():
                xs0 = sorted(l.x0 for l in lst)
                xs1 = sorted(l.x1 for l in lst)
                k = (pd.pno % 2, col, pd.ncols)
                acc[k][0].extend(xs0)
                acc[k][1].extend(xs1)
                if len(lst) >= 8:
                    pd.colm[col] = (_percentile(xs0, 0.15), _percentile(xs1, 0.85))

        self.body_pitch = _mode_of(bp, 1, body * 1.2)
        self.code_pitch = _mode_of(cp, 1, self.mono_size * 1.2)
        docm = {k: (_percentile(sorted(a), 0.15), _percentile(sorted(b), 0.85)) for k, (a, b) in acc.items() if a}
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

    def _build_vocab(self) -> None:
        vocab: Set[str] = set()
        for pd in self.pages:
            for l in pd.lines:
                if l.kind != "text":
                    continue
                for w in WORD_RE.findall(l.text)[:-1]:
                    vocab.add(w.lower())
        self.vocab = vocab

    def _heading_like(self, l: Line) -> bool:
        return l.kind == "heading" or l.bold_ratio >= 0.8 or l.size >= self.body_size * 1.08

    @staticmethod
    def _similarity(nw: str, nt: str, heading_like: bool) -> float:
        if nw == nt:
            return 1.0
        sm = difflib.SequenceMatcher(None, nw, nt, autojunk=False)
        r = sm.ratio() if sm.real_quick_ratio() >= 0.6 and sm.quick_ratio() >= 0.6 else 0.0
        if heading_like and len(nw) >= 3:
            lo, hi = sorted((len(nw), len(nt)))
            if hi > 0 and lo / hi >= 0.35 and (nt.endswith(nw) or nw.endswith(nt) or nt.startswith(nw)):
                r = max(r, 0.86)
        return r

    def _match_title(self, pd: PageData, title: str) -> Optional[tuple[float, int, int]]:
        nt = norm_key(title)
        if not nt:
            return None
        lines = pd.lines
        n = len(lines)
        skip = ("absorbed", "toc_heading", "table", "figure", "callout", "code", "dropcap")
        best: Optional[tuple[float, int, int]] = None
        for i in range(n):
            if lines[i].kind in skip:
                continue
            texts: list[str] = []
            hl = True
            for k in range(3):
                j = i + k
                if j >= n or lines[j].kind in skip:
                    break
                texts.append(lines[j].text or lines[j].plain)
                hl = hl and self._heading_like(lines[j])
                if k > 0 and not hl:
                    break
                nw = norm_key(FN_TOKEN_RE.sub("", " ".join(texts)))
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

    def _apply_bookmarks(self, toc: list[Any], page_count: int) -> None:
        page_map = {pd.pno: pd for pd in self.pages}
        entries: list[list[Any]] = []
        for lvl, title, page in toc:
            repaired = repair_metadata(title)
            if repaired and 1 <= page <= page_count:
                entries.append([lvl, repaired, page - 1])
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
                lines[t].kind = "absorbed"
            page_text = clean_ws(" ".join((lines[t].text or lines[t].plain) for t in range(i, j + 1)))
            if norm_key(title) in norm_key(page_text) and len(page_text) <= len(title) + 25:
                if re.match(r"^(?:\d{1,3}(?:\.\d{1,3})*|[A-Z]\.\d{1,3}|Chapter\s+\w+[:.]?)\s+", page_text, re.I):
                    title = page_text
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
                            title = f"{lab}. {title}"
                        else:
                            lab = lab.title() if lab.isupper() else lab
                            title = f"{lab}: {title}"
                        nt = norm_key(title)
                    t -= 1
                    absorbed += 1
                else:
                    break
            lines[i].kind = "toc_heading"
            lines[i].level = lvl
            lines[i].text = title

    def _assign_font_heading_levels(self) -> None:
        sizes: set[float] = set()
        for pd in self.pages:
            for l in pd.lines:
                if l.kind == "heading":
                    sizes.add(round(l.size, 1))
        if not sizes:
            return
        clusters: list[float] = []
        for sz in sorted(sizes, reverse=True):
            if not clusters or clusters[-1] - sz >= 0.75:
                clusters.append(sz)
        base_lvl = min(4, self.toc_max + 1) if self.toc_used else 1
        for pd in self.pages:
            for l in pd.lines:
                if l.kind == "heading":
                    idx = min(range(len(clusters)), key=lambda k: abs(clusters[k] - l.size))
                    l.level = min(4, base_lvl + idx)

    def _promote_cover_h1_if_needed(self) -> None:
        """Promote main body chapters to H1 when giant cover titles monopolize level 1."""
        if self.toc_used:
            return
        h1_pages = [pd.pno for pd in self.pages for l in pd.lines if l.kind == "heading" and l.level == 1]
        if not h1_pages or len(h1_pages) > 2:
            return
        max_h1_pno = max(h1_pages)
        if max_h1_pno > 2:
            return
        body_headings = [
            l for pd in self.pages for l in pd.lines if l.kind == "heading" and pd.pno > max_h1_pno
        ]
        if not body_headings:
            return
        chap_levels = [l.level for l in body_headings if CHAPTER_RE.match(l.text or l.plain)]
        target_lvl = min(chap_levels) if chap_levels else min(l.level for l in body_headings)
        if target_lvl <= 1:
            return
        shift = target_lvl - 1
        for pd in self.pages:
            if pd.pno <= max_h1_pno:
                continue
            for l in pd.lines:
                if l.kind == "heading" and l.level >= target_lvl:
                    l.level = max(1, l.level - shift)

    # ------------------------------------------------------------------ #
    #  Page -> Elements & Cross-Page Assembly
    # ------------------------------------------------------------------ #
    @staticmethod
    def _sx(l: Line) -> float:
        return -l.x1 if l.rtl else l.x0

    @staticmethod
    def _ex(l: Line) -> float:
        return -l.x0 if l.rtl else l.x1

    @staticmethod
    def _bounds(pd: PageData, col: int, l: Line) -> tuple[float, float]:
        left, right = pd.colm.get(col, (l.x0, l.x1))
        return (-right, -left) if l.rtl else (left, right)

    @staticmethod
    def _list_marker(text: str, prev_line: Optional[Line], prev_ends_par: bool) -> tuple[Optional[str], str]:
        m = STRONG_BULLET_RE.match(text)
        if m:
            return "•", text[m.end() :]
        m = WEAK_BULLET_RE.match(text) or NUM_LIST_RE.match(text)
        if m and (prev_line is None or prev_ends_par):
            mk = m.group(1)
            return ("•" if mk in "-–—*" else mk), text[m.end() :]
        return None, text

    def _expected_pitch(self, a: Line, b: Line) -> float:
        return self.body_pitch * (max(a.size, b.size) / self.body_size)

    def _is_break(self, a: Optional[Line], b: Line, cur: Para, pd: PageData) -> bool:
        if a is None or a.col != b.col or a.kind != "text":
            return True
        if a.tabular or b.tabular or a.rtl != b.rtl:
            return True
        d = b.base - a.base
        if d <= 0 or d > self._expected_pitch(a, b) * 1.15 + 0.3:
            return True
        if abs(a.size - b.size) > 1.2:
            return True
        em = self.body_size
        left, right = self._bounds(pd, b.col, a)
        width = max(right - left, 1.0)
        if cur.marker:
            if self._sx(b) > cur.marker_x0 + 0.3 * em:
                return False
            return bool(TERMINAL_RE.search(a.text))
        ends = bool(TERMINAL_RE.search(a.text))
        if self._ex(a) < right - 0.40 * width:
            return True
        if ends and self._ex(a) < right - 0.12 * width:
            return True
        if self._sx(b) > self._sx(a) + 0.8 * em and self._sx(a) < left + 0.5 * em:
            return True
        if cur.rest_x0 is not None and self._sx(b) < cur.rest_x0 - 0.8 * em:
            return True
        return False

    def _build_page_elements(self, pd: PageData) -> list[Any]:
        els: list[Any] = []

        def tag(el: Any) -> Any:
            el.page, el.pno = pd.label, pd.pno
            return el

        for lvl, title in pd.toc_unmatched:
            els.append(tag(Heading(lvl, title)))

        lines = pd.lines
        cur: Union[Para, Code, Pre, None] = None
        prev: Optional[Line] = None
        last_heading_line: Optional[Line] = None
        pending_drop = ""
        em = self.body_size

        def flush() -> None:
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
            if k in ("table", "figure", "callout"):
                flush()
                text_lines, rows, extra = ln.block
                if k == "callout":
                    els.append(tag(Pre(text_lines, "callout", None, callout_kind=extra)))
                else:
                    els.append(tag(Pre(text_lines, k, rows, caption=extra)))
                prev = None
                continue
            if k == "toc_heading":
                flush()
                els.append(tag(Heading(ln.level, ln.text)))
                prev, last_heading_line = ln, None
                continue
            if k == "heading":
                flush()
                h = els[-1] if els else None
                if (
                    isinstance(h, Heading)
                    and h.from_font
                    and last_heading_line is not None
                    and prev is last_heading_line
                    and ln.col == prev.col
                    and 0 < ln.y0 - prev.y1 < 1.5 * max(ln.size, prev.size)
                ):
                    if h.level == ln.level:
                        h.text = f"{h.text} {ln.text}"
                    elif len(h.text) <= 25 or LABEL_RE.match(h.text):
                        lab = h.text.title() if h.text.isupper() and CHAPTER_RE.match(h.text) else h.text
                        h.text = f"{lab.rstrip('.:')}: {ln.text}"
                        h.level = min(h.level, ln.level)
                    else:
                        els.append(tag(Heading(ln.level, ln.text, True)))
                else:
                    els.append(tag(Heading(ln.level, ln.text, True)))
                prev = last_heading_line = ln
                continue
            if k == "code":
                left = pd.colm.get(ln.col, (ln.x0, ln.x1))[0]
                cw = ln.cw or self.mono_size * 0.6
                indent = max(0, int(round((ln.x0 - left) / cw)))
                row: list[Any] = [indent, ln.text, 0, ln.x0 - left]
                if isinstance(cur, Code) and prev is not None and prev.kind == "code" and prev.col == ln.col:
                    d = ln.base - prev.base
                    ratio = d / self.code_pitch if self.code_pitch else 1.0
                    if 0 < ratio <= 3.6:
                        row[2] = max(0, min(2, int(round(ratio)) - 1))
                        cur.lines.append(row)
                        cur.prop = cur.prop or ln.prop
                        prev = ln
                        continue
                flush()
                cur = tag(Code())
                cur.prop = ln.prop
                cur.lines.append(row)
                prev = ln
                continue

            # ---- Ordinary prose text line ----
            text = ln.text
            if pending_drop:
                text = pending_drop + text
                ln.text = text
                pending_drop = ""
            if not text:
                continue

            nxt = lines[idx + 1] if idx + 1 < len(lines) else None
            if ln.tabular:
                if isinstance(cur, Pre) and prev is not None and prev.tabular and cur.kind == "pre":
                    cur.lines.append(text)
                else:
                    flush()
                    cur = tag(Pre([text]))
                prev = ln
                continue

            # Stand-alone bold line -> minor H4 subheading
            gap_above = (
                prev is None
                or prev.kind != "text"
                or prev.col != ln.col
                or (ln.base - prev.base) > self._expected_pitch(prev, ln) * 1.15 + 0.3
            )
            if (
                ln.bold_ratio >= 0.95
                and len(text) <= 90
                and len(text.split()) <= 14
                and not re.search(r"[.,;،]$", text)
                and not text[0].islower()
                and LETTERS2_RE.search(text)
                and gap_above
                and not STRONG_BULLET_RE.match(text)
                and not (
                    nxt is not None
                    and nxt.kind == "text"
                    and nxt.bold_ratio >= 0.95
                    and nxt.col == ln.col
                    and 0 < nxt.base - ln.base <= self._expected_pitch(ln, nxt) * 1.15 + 0.3
                )
            ):
                flush()
                els.append(tag(Heading(4, FN_TOKEN_RE.sub("", text), True)))
                prev, last_heading_line = ln, None
                continue

            prev_ends = (
                cur is None
                or not isinstance(cur, Para)
                or prev is None
                or prev.kind != "text"
                or bool(TERMINAL_RE.search(prev.text))
                or cur.marker is not None
                or self._is_break(prev, ln, Para(""), pd)
            )
            marker, rest = self._list_marker(text, prev if isinstance(cur, Para) else None, prev_ends)
            if marker is not None:
                flush()
                left = self._bounds(pd, ln.col, ln)[0]
                lvl = max(0, min(3, int(round((self._sx(ln) - left) / (2 * em)))))
                cur = tag(Para(rest, marker, lvl, self._sx(ln)))
                cur.rtl = ln.rtl
            elif isinstance(cur, Para) and not self._is_break(prev, ln, cur, pd):
                cur.lines.append(text)
                if cur.rest_x0 is None:
                    cur.rest_x0 = self._sx(ln)
            else:
                flush()
                cur = tag(Para(text, None, 0, self._sx(ln)))
                cur.rtl = ln.rtl
            prev, last_heading_line = ln, None

        flush()
        if self.opts.footnotes != "drop":
            for mk, ftext in self.notes.get(pd.pno, []):
                n = Note(pd.pno, mk, ftext, page=pd.label)
                els.append(n)
                self.footnotes_list.append(n)
        return els

    def _assemble(self, page_elements: list[tuple[PageData, list[Any]]]) -> list[Any]:
        """Join elements across page boundaries cleanly, preserving PageMark and Note scopes."""
        out: list[Any] = []
        for pd, els in page_elements:
            pending_pm = PageMark(pd.label, pd.pno) if self.opts.page_markers else None
            trailing: list[Any] = []
            # Peel back trailing PageMarks and bottom-of-page Notes to test cross-page continuation
            while out and isinstance(out[-1], (PageMark, Note)):
                trailing.insert(0, out.pop())

            if els and out:
                first = els[0]
                last = out[-1]
                if (
                    isinstance(last, Para)
                    and isinstance(first, Para)
                    and not last.marker
                    and not first.marker
                    and last.rtl == first.rtl
                    and not TERMINAL_RE.search(last.lines[-1])
                ):
                    last.lines.extend(first.lines)
                    els = els[1:]
                elif isinstance(last, Code) and isinstance(first, Code) and len(els) > 1:
                    last.lines.extend(first.lines)
                    last.prop = last.prop or first.prop
                    els = els[1:]
                elif (
                    isinstance(last, Code)
                    and isinstance(first, Code)
                    and len(els) == 1
                    and len(first.lines) >= 1
                    and not isinstance(els[-1], Note)
                ):
                    last.lines.extend(first.lines)
                    last.prop = last.prop or first.prop
                    els = []

            out.extend(trailing)
            if pending_pm is not None:
                out.append(pending_pm)
            out.extend(els)
        return out

    @staticmethod
    def _normalise_prop_indent(code: Code) -> None:
        xs = sorted({round(row[3], 1) for row in code.lines if row[1].strip()})
        centers: list[float] = []
        for x in xs:
            if not centers or x - centers[-1] > 2.5:
                centers.append(x)
        if not centers:
            return
        for row in code.lines:
            if not row[1].strip():
                continue
            lvl = min(range(len(centers)), key=lambda idx: abs(centers[idx] - row[3]))
            row[0] = 4 * lvl

    def _compute_code_body(self, e: Code) -> list[str]:
        base = min((row[0] for row in e.lines if row[1].strip()), default=0)
        body: list[str] = []
        for indent, txt, blank_before, _rx in e.lines:
            body.extend([""] * blank_before)
            if self.opts.fix_code_quotes:
                txt = txt.translate(CODE_TRANS)
            body.append((" " * max(0, indent - base) + txt).rstrip() if txt.strip() else "")
        while body and not body[-1]:
            body.pop()
        if self.opts.strip_line_numbers:
            from pdf_extractor.code_detect import strip_listing_numbers

            body = strip_listing_numbers(body)
        return body

    def _post_process(self, elements: list[Any]) -> list[Any]:
        opts = self.opts
        result: list[Any] = []
        for e in elements:
            if isinstance(e, Para):
                raw = " ".join(e.lines).strip()
                if len(raw) <= 2 or (len(raw) <= 4 and re.fullmatch(r"[-–—*#•]+", raw)):
                    continue
                result.append(e)
                continue
            if isinstance(e, Heading):
                e.text = clean_ws(FN_TOKEN_RE.sub("", e.text))
                if not e.text:
                    continue
                if e.from_font and len(e.text) > 250:
                    p = Para(e.text)
                    p.page, p.pno = e.page, e.pno
                    result.append(p)
                    continue
                if e.from_font and re.fullmatch(r"[\divxlcIVXLC.:\-–— ]{1,5}", e.text):
                    continue
                result.append(e)
                continue
            if isinstance(e, Code):
                if not any(r[1].strip() for r in e.lines):
                    continue
                if opts.strip_line_numbers:
                    strip_code_listing_numbers(e)
                if e.prop:
                    self._normalise_prop_indent(e)
                e.rendered_lines = self._compute_code_body(e)
                if not any(ln.strip() for ln in e.rendered_lines):
                    continue
                if opts.detect_lang and opts.code_lang == "auto":
                    e.lang = guess_language(e.rendered_lines)
                elif opts.detect_lang and opts.code_lang != "none":
                    e.lang = opts.code_lang
                else:
                    e.lang = ""
                result.append(e)
                continue
            result.append(e)
        return result

    # ------------------------------------------------------------------ #
    #  Paragraph Joining & Document Metadata Helpers
    # ------------------------------------------------------------------ #
    def _join_para(self, lines: list[str]) -> str:
        out = ""
        for nxt in lines:
            nxt = nxt.strip()
            if not nxt:
                continue
            if not out:
                out = nxt
                continue
            if is_rtl_text(out[-12:]) or is_rtl_text(nxt[:12]):
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

    def book_title(self) -> str:
        meta_title = repair_metadata(self.metadata.get("title") or "")
        garbage_meta = bool(
            re.search(r"(?:microsoft\s+word|powerpoint|\.docx?|\.pdf|\.dvi|^untitled$|^presentation$)", meta_title, re.I)
        )
        if meta_title and not garbage_meta:
            return meta_title
        return self.cover_title or self.path.stem

    def stats(self) -> Counter[str]:
        return Counter(type(e).__name__ for e in self.elements)

    # ------------------------------------------------------------------ #
    #  Render Dispatch & Backwards-Compatible Methods
    # ------------------------------------------------------------------ #
    def render(self, elements: list[Any], include_header: bool = True) -> str:
        from pdf_extractor.renderers import render_jsonl, render_md, render_txt

        fmt = self.opts.format.lower()
        if fmt == "md":
            return render_md(self, elements, include_header=include_header)
        if fmt == "jsonl":
            return render_jsonl(self, elements, include_header=include_header)
        return render_txt(self, elements, include_header=include_header)

    def render_text(self, elements: list[Any], include_header: bool = True) -> str:
        from pdf_extractor.renderers import render_txt

        return render_txt(self, elements, include_header=include_header)

    def render_markdown(self, elements: list[Any], include_header: bool = True) -> str:
        from pdf_extractor.renderers import render_md

        return render_md(self, elements, include_header=include_header)

    def render_jsonl(self, elements: list[Any], include_header: bool = True) -> str:
        from pdf_extractor.renderers import render_jsonl

        return render_jsonl(self, elements, include_header=include_header)

    def chapters(self) -> list[tuple[str, list[Any]]]:
        """Split elements at level-1 headings while skipping empty PageMark-only front matter."""
        return split_by_chapters(self, self.elements)


def split_by_chapters(extractor: BookExtractor, elements: list[Any]) -> list[tuple[str, list[Any]]]:
    """Split elements into (slug_name, chapter_elements) tuples without empty PageMark stubs."""
    from pdf_extractor.sanitize import slugify

    chapters: list[tuple[str, list[Any]]] = []
    curr_title = "Front Matter"
    curr_els: list[Any] = []
    idx = 0

    for e in elements:
        if isinstance(e, Heading) and e.level == 1:
            if any(not isinstance(x, PageMark) for x in curr_els):
                chapters.append((f"{idx:02d}_{slugify(curr_title)}", curr_els))
                idx += 1
            curr_title = e.text
            curr_els = [e]
        else:
            curr_els.append(e)

    if any(not isinstance(x, PageMark) for x in curr_els):
        chapters.append((f"{idx:02d}_{slugify(curr_title)}", curr_els))
    return chapters
