"""Comprehensive v2.1.0 regression tests covering bug fixes, BiDi RTL, layout blocks, and RAG JSONL."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pymupdf
import pytest

from pdf_extractor import (
    BookExtractor,
    Code,
    Heading,
    Line,
    Note,
    Options,
    Para,
    Pre,
    Seg,
    guess_code_language,
    strip_listing_numbers,
    text_direction,
)
from pdf_extractor.cli import collect_pdfs, main as cli_main, plan_targets, write_output
from pdf_extractor.constants import CHAR_TRANS, TYPE3_FONT_RE


def _make_pdf(build_fn) -> Path:
    doc = pymupdf.open()
    build_fn(doc)
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp.close()
    doc.save(tmp.name)
    doc.close()
    return Path(tmp.name)


class TestDropcapAndFootnotes:
    def test_dropcap_prepends_to_following_paragraph(self):
        def build(doc):
            p = doc.new_page(width=595, height=842)
            p.insert_text((72, 118), "T", fontsize=34, fontname="helv")
            p.insert_text((100, 102), "he quick brown fox jumps", fontsize=11, fontname="helv")
            p.insert_text((100, 118), "over the lazy dog.", fontsize=11, fontname="helv")

        pdf = _make_pdf(build)
        try:
            with BookExtractor(pdf, Options(use_bookmarks=False, quiet=True)) as ex:
                els = ex.run()
            paras = [e for e in els if isinstance(e, Para)]
            heads = [e for e in els if isinstance(e, Heading) and e.text.strip() == "T"]
            assert not heads
            assert any("The quick brown fox jumps over the lazy dog." in ex._join_para(p.lines) for p in paras)
        finally:
            pdf.unlink(missing_ok=True)

    def test_multiline_footnote_inline_section_drop_and_chapter_scope(self):
        def build(doc):
            p1 = doc.new_page(width=595, height=842)
            p1.insert_text((72, 100), "Chapter 1: Intro", fontsize=20, fontname="helv")
            p1.insert_text((72, 140), "Security matters today1 in every system.", fontsize=11, fontname="helv")
            # Simulate superscript footnote marker & multi-line bottom footnote
            p1.insert_text((72, 760), "1 This is a multi-line footnote explaining", fontsize=8, fontname="helv")
            p1.insert_text((80, 772), "why key rotation is critical.", fontsize=8, fontname="helv")

            p2 = doc.new_page(width=595, height=842)
            p2.insert_text((72, 100), "Chapter 2: Next", fontsize=20, fontname="helv")
            p2.insert_text((72, 140), "Unrelated second chapter paragraph.", fontsize=11, fontname="helv")

        pdf = _make_pdf(build)
        try:
            # 1) Section scoping & chapter split isolation
            ex = BookExtractor(pdf, Options(footnotes="section", use_bookmarks=False, quiet=True))
            els = ex.run()
            chaps = ex.chapters()
            assert len(chaps) >= 2
            chap1_txt = ex.render_text(chaps[0][1], include_header=False)
            chap2_txt = ex.render_text(chaps[1][1], include_header=False)
            assert "why key rotation is critical." in chap1_txt
            assert "why key rotation is critical." not in chap2_txt
            assert any(isinstance(e, Note) and "why key rotation is critical." in e.text for e in els)

            # 2) Drop mode leaves zero Note objects
            ex_drop = BookExtractor(pdf, Options(footnotes="drop", use_bookmarks=False, quiet=True))
            els_drop = ex_drop.run()
            assert not any(isinstance(e, Note) for e in els_drop)
            assert "why key rotation is critical." not in ex_drop.render_text(els_drop)
        finally:
            pdf.unlink(missing_ok=True)

    def test_footnote_inline_mode_txt_and_md(self):
        ex = BookExtractor(Path("dummy.pdf"), Options(format="txt", footnotes="inline", quiet=True))
        n = Note(0, "1", "Extended footnote clarification.", page="1")
        p = Para(["Main claim\ue0000:1\ue001 stays here."])
        p.page, p.pno = "1", 0
        txt = ex.render_text([p, n], include_header=False)
        assert "[1: Extended footnote clarification.]" in txt
        md = BookExtractor(Path("dummy.pdf"), Options(format="md", footnotes="inline", quiet=True)).render_markdown(
            [p, n], include_header=False
        )
        assert "[^p1-1]: Extended footnote clarification." in md


class TestTablesCalloutsAndFigures:
    def test_ruled_table_preserved_and_boxed_code_not_stolen_and_callout_detected(self):
        def build(doc):
            p = doc.new_page(width=595, height=842)
            # 1) Ruled 2x2 table + caption above
            p.insert_text((72, 92), "Table 1: Service Ports", fontsize=10, fontname="helv")
            x0, x1, x2 = 72, 200, 340
            y0, y1, y2 = 100, 125, 150
            for y in (y0, y1, y2):
                p.draw_line((x0, y), (x2, y))
            for x in (x0, x1, x2):
                p.draw_line((x, y0), (x, y2))
            p.insert_text((80, 117), "Service", fontsize=10, fontname="helv")
            p.insert_text((210, 117), "Port", fontsize=10, fontname="helv")
            p.insert_text((80, 142), "HTTPS", fontsize=10, fontname="helv")
            p.insert_text((210, 142), "443", fontsize=10, fontname="helv")

            # 2) Boxed monospace code listing (drawn grid around code — must remain Code!)
            cx0, cx1 = 72, 420
            cy0, cy1, cy2 = 200, 225, 250
            for y in (cy0, cy1, cy2):
                p.draw_line((cx0, y), (cx1, y))
            for x in (cx0, cx1):
                p.draw_line((x, cy0), (x, cy2))
            p.insert_text((82, 217), "import json", fontsize=10, fontname="cour")
            p.insert_text((82, 242), "print(json.dumps({'ok': True}))", fontsize=10, fontname="cour")

            # 3) Shaded callout box starting with NOTE:
            box = pymupdf.Rect(72, 300, 480, 355)
            p.draw_rect(box, color=(0.2, 0.2, 0.2), fill=(0.92, 0.94, 0.98), width=1.0)
            p.insert_text((85, 322), "NOTE: Rotate credentials after every deployment.", fontsize=10, fontname="helv")

        pdf = _make_pdf(build)
        try:
            with BookExtractor(pdf, Options(format="md", tables=True, callouts=True, use_bookmarks=False, quiet=True)) as ex:
                els = ex.run()
            tables = [e for e in els if isinstance(e, Pre) and e.kind == "table"]
            codes = [e for e in els if isinstance(e, Code)]
            callouts = [e for e in els if isinstance(e, Pre) and e.kind == "callout"]
            assert len(tables) == 1
            assert tables[0].caption == "Table 1: Service Ports"
            assert len(codes) >= 1
            assert "import json" in "\n".join(codes[0].rendered_lines or [])
            assert len(callouts) == 1
            assert callouts[0].callout_kind == "NOTE"

            md = ex.render_markdown(els)
            assert "| Service | Port |" in md
            assert "*Table 1: Service Ports*" in md
            assert "> [!NOTE]" in md
        finally:
            pdf.unlink(missing_ok=True)


class TestCrossPageAndHeadings:
    def test_cross_page_stitching_works_with_page_markers_enabled(self):
        def build(doc):
            p1 = doc.new_page(width=595, height=842)
            p1.insert_text((72, 100), "Chapter One", fontsize=18, fontname="helv")
            p1.insert_text((72, 740), "This unfinished paragraph keeps flowing across", fontsize=11, fontname="helv")
            p2 = doc.new_page(width=595, height=842)
            p2.insert_text((72, 100), "the page boundary without splitting prematurely.", fontsize=11, fontname="helv")

        pdf = _make_pdf(build)
        try:
            with BookExtractor(pdf, Options(page_markers=True, use_bookmarks=False, quiet=True)) as ex:
                els = ex.run()
            paras = [e for e in els if isinstance(e, Para)]
            joined = [ex._join_para(p.lines) for p in paras]
            assert any("keeps flowing across the page boundary without splitting prematurely." in t for t in joined)
        finally:
            pdf.unlink(missing_ok=True)

    def test_cover_h1_promotion_avoids_orphan_cover_only_h1(self):
        def build(doc):
            cover = doc.new_page(width=595, height=842)
            cover.insert_text((72, 200), "BIG COVER TITLE", fontsize=34, fontname="helv")
            c1 = doc.new_page(width=595, height=842)
            c1.insert_text((72, 100), "Chapter 1: Foundations", fontsize=18, fontname="helv")
            c1.insert_text((72, 140), "Body prose here for chapter one.", fontsize=11, fontname="helv")
            c2 = doc.new_page(width=595, height=842)
            c2.insert_text((72, 100), "Chapter 2: Internals", fontsize=18, fontname="helv")
            c2.insert_text((72, 140), "Body prose here for chapter two.", fontsize=11, fontname="helv")

        pdf = _make_pdf(build)
        try:
            with BookExtractor(pdf, Options(use_bookmarks=False, quiet=True)) as ex:
                els = ex.run()
            h1s = [e.text for e in els if isinstance(e, Heading) and e.level == 1]
            assert len(h1s) >= 2
            assert any("Foundations" in h for h in h1s)
            assert any("Internals" in h for h in h1s)
        finally:
            pdf.unlink(missing_ok=True)


class TestBiDiAndRTLRegressions:
    def test_rtl_text_direction_ignores_digits(self):
        assert text_direction("الفصل 12: المقدمة 2026") == "rtl"
        assert text_direction("123456") == "ltr"
        assert text_direction("Chapter 12 Arabic كلمة") == "ltr"

    def test_bidi_mixed_rtl_with_ltr_segment_order(self):
        s_ar_start = Seg(350.0, 420.0, "تثبيت", 12.0, False, False, 6.0, 100.0)
        s_en = Seg(220.0, 320.0, "Python 3.14", 12.0, False, False, 6.0, 100.0)
        s_ar_end = Seg(120.0, 200.0, "بنجاح.", 12.0, False, False, 6.0, 100.0)
        ln = Line([s_ar_end, s_en, s_ar_start], 90.0, 110.0)
        assert ln.rtl is True
        ex = BookExtractor(Path("dummy.pdf"), Options())
        assert ex._join_line_spans(ln, code=False) == "تثبيت Python 3.14 بنجاح."


class TestCodeDetectionAndCleanups:
    def test_yaml_and_python_repl_disambiguation(self):
        assert guess_code_language(["host: localhost", "port: 8080", "enabled: true"]) == "yaml"
        assert guess_code_language([">>> x = 1", ">>> print(x)"]) == "python"

    def test_strip_listing_numbers_strict_sequential_only(self):
        assert strip_listing_numbers(["1 x = 1", "10 y = 2"]) == ["1 x = 1", "10 y = 2"]
        stripped = strip_listing_numbers([" 9   if x:", "10       return x"])
        assert stripped == ["if x:", "    return x"]

    def test_ligatures_and_type3_and_mid_sentence_number_guard(self):
        assert "\ufb01le \ufb02ow".translate(CHAR_TRANS) == "file flow"
        assert TYPE3_FONT_RE.search("Type3")
        ex = BookExtractor(Path("dummy.pdf"), Options())
        prev = Line([Seg(72, 300, "The buffer size equals", 11, False, False, 5, 100)], 90, 105)
        mk, _ = ex._list_marker("- 1024 bytes.", prev, prev_ends_par=False)
        assert mk is None

    def test_monospace_kerned_url_no_stray_space(self):
        # Simulate tiny advance gap 1.5pt where cw=6.0 (gap = 0.25*cw < 0.35*cw threshold -> no extra space)
        s1 = Seg(72.0, 144.0, "www.packtpub", 10.0, True, False, 6.0, 100.0, font="Courier")
        s2 = Seg(145.5, 169.5, ".com", 10.0, True, False, 6.0, 100.0, font="Courier")
        ln = Line([s1, s2], 90.0, 102.0)
        ex = BookExtractor(Path("dummy.pdf"), Options())
        assert ex._join_line_spans(ln, code=True) == "www.packtpub.com"


class TestRAGChunkingCLIAndPackaging:
    def test_rag_chunking_section_boundaries_and_overlap(self):
        def build(doc):
            p = doc.new_page(width=595, height=842)
            p.insert_text((72, 90), "Section One", fontsize=18, fontname="helv")
            words = "alpha beta gamma delta epsilon zeta eta theta iota kappa".split()
            for i in range(6):
                y = 130 + i * 16
                p.insert_text((72, y), f"Paragraph {i+1}: " + " ".join(words) + ".", fontsize=11, fontname="helv")
            p.insert_text((72, 260), "Section Two", fontsize=18, fontname="helv")
            p.insert_text((72, 290), "Final distinct section paragraph here.", fontsize=11, fontname="helv")

        pdf = _make_pdf(build)
        try:
            with BookExtractor(
                pdf, Options(format="jsonl", chunk_tokens=30, chunk_overlap=4, use_bookmarks=False, quiet=True)
            ) as ex:
                els = ex.run()
                jsonl = ex.render_jsonl(els)
            rows = [json.loads(line) for line in jsonl.strip().splitlines()]
            assert rows[0]["type"] == "document"
            chunks = [r for r in rows if r["type"] == "chunk"]
            assert len(chunks) >= 2
            # Section Two must start cleanly in its own chunk rather than bleeding into Section One
            last_chunk = chunks[-1]
            assert "Section Two" in " ".join(last_chunk["section_path"])
            assert "Final distinct section" in last_chunk["text"]
            assert "Paragraph 1" not in last_chunk["text"]
        finally:
            pdf.unlink(missing_ok=True)

    def test_md_wrap_width_and_bom_policy(self, tmp_path):
        ex = BookExtractor(Path("dummy.pdf"), Options(format="md", width=40, quiet=True))
        long_p = Para(["This sentence has plenty of words so that Markdown wrapping at width forty wraps cleanly."])
        md = ex.render_markdown([long_p], include_header=False)
        assert all(len(line) <= 40 for line in md.strip().splitlines())

        txt_file = tmp_path / "out.txt"
        md_file = tmp_path / "out.md"
        jsonl_file = tmp_path / "out.jsonl"
        write_output(txt_file, "hello", Options(format="txt", no_bom=False))
        write_output(md_file, "# title", Options(format="md", no_bom=False))
        write_output(jsonl_file, '{"a":1}\n', Options(format="jsonl", no_bom=False))
        assert txt_file.read_bytes().startswith(b"\xef\xbb\xbf")
        assert not md_file.read_bytes().startswith(b"\xef\xbb\xbf")
        assert not jsonl_file.read_bytes().startswith(b"\xef\xbb\xbf")

    def test_plan_targets_no_ext_file_and_collect_pdfs_dedup(self, tmp_path):
        pdf = tmp_path / "book.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        items = collect_pdfs([str(pdf), str(pdf)], recursive=False)
        assert len(items) == 1
        file_targets = plan_targets(items, Path("my_output"), ".txt", raw_output="my_output")
        assert file_targets == [Path("my_output")]
        dir_targets = plan_targets(items, Path("out_dir"), ".txt", raw_output="out_dir/")
        assert dir_targets == [Path("out_dir") / "book.txt"]

    def test_scanned_pdf_warning_and_context_manager_closes_doc(self, tmp_path):
        # Image-only page (no text layer)
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100), False)
        pix.clear_with(200)
        doc = pymupdf.open()
        page = doc.new_page(width=200, height=200)
        page.insert_image(pymupdf.Rect(10, 10, 190, 190), pixmap=pix)
        pdf = tmp_path / "scanned.pdf"
        doc.save(pdf)
        doc.close()

        with BookExtractor(pdf, Options(quiet=True)) as ex:
            ex.run()
            assert any("ocr" in w.lower() for w in ex.warnings)
        assert ex.doc is None

    def test_cli_end_to_end_chunked_jsonl(self, tmp_path):
        pdf = tmp_path / "sample.pdf"
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 100), "Chapter 1: Hello", fontsize=18, fontname="helv")
        page.insert_text((72, 140), "x = 42", fontsize=10, fontname="cour")
        doc.save(pdf)
        doc.close()

        out_file = tmp_path / "result_no_ext"
        rc = cli_main([str(pdf), "-f", "jsonl", "--chunk-tokens", "200", "-o", str(out_file), "-q"])
        assert rc == 0
        assert out_file.is_file()
        assert not out_file.read_bytes().startswith(b"\xef\xbb\xbf")
        rows = [json.loads(line) for line in out_file.read_text(encoding="utf-8").splitlines()]
        assert rows[0]["type"] == "document"
        assert rows[1]["type"] == "chunk"
