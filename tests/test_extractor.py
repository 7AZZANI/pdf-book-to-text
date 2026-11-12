"""
tests/test_extractor.py - Automated test suite for pdf-book-to-text.

Tests critical correctness, UTF-8 integrity, language detection,
Arabic BiDi handling, and Markdown / JSONL rendering using in-memory synthetic PDFs.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import pymupdf

import pdf_book_to_text as extractor
from pdf_book_to_text import (
    BookExtractor,
    Options,
    clean_text,
    guess_code_language,
    norm_key,
    repair_metadata,
    strip_listing_numbers,
)


class TestTextSanitization(unittest.TestCase):
    def test_clean_text_preserves_box_drawing(self):
        """CRITICAL: Ensure box-drawing characters (U+2500, etc.) are NOT corrupted into '%'."""
        tree = "├── src/\n│   ├── main.py\n└── tests/\n"
        cleaned = clean_text(tree)
        self.assertEqual(cleaned, tree)
        self.assertNotIn("%", cleaned)
        # Verify ord(0x2500) specifically
        horizontal = "─" * 10
        self.assertEqual(clean_text(horizontal), horizontal)

    def test_clean_text_preserves_html_entities(self):
        """CRITICAL: Ensure HTML code examples are NOT decoded in body text."""
        code_html = '<div class="alert">&lt;script&gt;alert(1)&lt;/script&gt;&amp;copy;</div>'
        cleaned = clean_text(code_html)
        self.assertEqual(cleaned, code_html)
        self.assertIn("&lt;script&gt;", cleaned)

    def test_clean_text_strips_nul_and_control_chars(self):
        raw = "Hello\x00World\x01\x02\x08Test\tKeep\nNewline\r"
        expected = "HelloWorldTest\tKeep\nNewline\r"
        self.assertEqual(clean_text(raw), expected)

    def test_repair_metadata_repairs_byteswapped_utf16(self):
        """Bookmarks with broken UTF-16 byte-swap and HTML entities must be repaired."""
        meta_html = "Chapter 1: Security &#x2013; Overview &amp; Setup"
        self.assertEqual(repair_metadata(meta_html), "Chapter 1: Security – Overview & Setup")

        # Byte swapped ASCII: 0x5000 -> 'P', 0x6F00 -> 'o', 0x4300 -> 'C'
        swapped = chr(0x2800) + chr(0x5000) + chr(0x6F00) + chr(0x4300) + chr(0x2900)
        self.assertEqual(repair_metadata(swapped), "(PoC)")


class TestMultilingualNormKey(unittest.TestCase):
    def test_norm_key_english(self):
        self.assertEqual(norm_key("Chapter 1: Getting Started!"), "chapter1gettingstarted")

    def test_norm_key_arabic(self):
        """Arabic bookmarks must NOT collapse to empty strings."""
        result = norm_key("الفصل الأول: مدخل إلى البرمجة")
        self.assertTrue(len(result) > 5)
        self.assertEqual(result, norm_key("الفصل الأول - مدخل إلى البرمجة."))

    def test_norm_key_russian(self):
        result = norm_key("Глава 1: Введение")
        self.assertEqual(result, "глава1введение")


class TestCodeLanguageDetection(unittest.TestCase):
    def test_python_detection(self):
        py_code = (
            "import os\n"
            "from pathlib import Path\n\n"
            "def calculate_hash(data: bytes) -> str:\n"
            "    if not data:\n"
            "        return ''\n"
            "    return hashlib.sha256(data).hexdigest()\n"
        )
        self.assertEqual(guess_code_language(py_code), "python")

    def test_bash_detection(self):
        sh_code = (
            "#!/bin/bash\n"
            "set -euo pipefail\n"
            "sudo apt-get update && sudo apt-get install -y curl\n"
            "curl -sSL https://example.com | grep ok\n"
        )
        self.assertEqual(guess_code_language(sh_code), "bash")

    def test_sql_detection(self):
        sql_code = (
            "SELECT user_id, email, created_at\n"
            "FROM enterprise_users\n"
            "WHERE is_active = 1\n"
            "GROUP BY user_id\n"
            "ORDER BY created_at DESC;\n"
        )
        self.assertEqual(guess_code_language(sql_code), "sql")

    def test_json_detection(self):
        json_code = '{\n  "status": "success",\n  "count": 42\n}'
        self.assertEqual(guess_code_language(json_code), "json")


class TestStripListingNumbers(unittest.TestCase):
    def test_strip_sequential_line_numbers(self):
        raw_listing = [
            "1  import sys",
            "2  import os",
            "3  ",
            "4  def run():",
            "5      print('ok')",
        ]
        cleaned = strip_listing_numbers(raw_listing)
        self.assertEqual(cleaned[0], "import sys")
        self.assertEqual(cleaned[1], "import os")
        self.assertEqual(cleaned[3], "def run():")
        self.assertEqual(cleaned[4], "    print('ok')")

    def test_do_not_strip_ordinary_code(self):
        code = [
            "x = 10",
            "y = 20",
            "total = x + y",
        ]
        self.assertEqual(strip_listing_numbers(code), code)


class TestSyntheticPDFExtraction(unittest.TestCase):
    def setUp(self):
        # Create a synthetic PDF in memory with PyMuPDF
        self.doc = pymupdf.open()

        # Page 0: Cover / Title
        page0 = self.doc.new_page(width=595, height=842)
        page0.insert_text((72, 150), "Advanced Python Security", fontsize=24, fontname="helv")
        page0.insert_text((72, 190), "Author: AZZANI", fontsize=14, fontname="helv")

        # Page 1: Chapter 1 + Code
        page1 = self.doc.new_page(width=595, height=842)
        page1.insert_text((72, 80), "Chapter 1: Network Reconnaissance", fontsize=18, fontname="helv")
        page1.insert_text((72, 120), "This chapter covers automated network reconnaissance using sockets.", fontsize=11, fontname="tiro")
        page1.insert_text((72, 140), "Directory layout is structured as follows:", fontsize=11, fontname="tiro")
        # Insert Courier code block
        code_text = (
            "import socket\n"
            "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
            "s.connect(('127.0.0.1', 8080))\n"
            "s.send(b'PING')\n"
            "s.close()\n"
        )
        page1.insert_text((72, 180), code_text, fontsize=9.5, fontname="cour")

        # Add outline / TOC
        self.doc.set_toc([
            [1, "Title Page", 1],
            [1, "Chapter 1: Network Reconnaissance", 2],
        ])

        # Save to temp file
        self.temp_pdf = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        self.temp_pdf.close()
        self.doc.save(self.temp_pdf.name)
        self.doc.close()

    def tearDown(self):
        Path(self.temp_pdf.name).unlink(missing_ok=True)

    def test_extract_txt_format(self):
        opts = Options(format="txt", use_bookmarks=True)
        ex = BookExtractor(Path(self.temp_pdf.name), opts)
        elements = ex.run()
        rendered = ex.render(elements)

        self.assertIn("Network Reconnaissance", rendered)
        self.assertIn("import socket", rendered)
        self.assertIn("s.connect", rendered)

    def test_extract_markdown_format(self):
        opts = Options(format="md", use_bookmarks=True, detect_lang=True)
        ex = BookExtractor(Path(self.temp_pdf.name), opts)
        elements = ex.run()
        rendered = ex.render_markdown(elements)

        self.assertIn("# ", rendered)
        self.assertIn("```python", rendered)
        self.assertIn("import socket", rendered)

    def test_extract_jsonl_format(self):
        opts = Options(format="jsonl", use_bookmarks=True)
        ex = BookExtractor(Path(self.temp_pdf.name), opts)
        elements = ex.run()
        rendered = ex.render_jsonl(elements)

        lines = [json.loads(line) for line in rendered.strip().splitlines() if line.strip()]
        self.assertTrue(len(lines) >= 2)
        # Verify schema
        code_records = [r for r in lines if r.get("type") == "code"]
        self.assertTrue(len(code_records) >= 1)
        self.assertEqual(code_records[0]["language"], "python")
        self.assertIn("import socket", code_records[0]["text"])


class TestArabicAndBiDi(unittest.TestCase):
    def test_arabic_bidi_line_ordering(self):
        """Ensure RTL lines order segments right-to-left, placing periods correctly."""
        # Create Line with segments simulating right-to-left layout:
        # Arabic word at x=300..350, period at x=280..285
        from pdf_book_to_text import Seg, Line
        seg_word = Seg(300.0, 350.0, "الكتاب", 12.0, False, False, 6.0, 100.0)
        seg_dot = Seg(280.0, 285.0, ".", 12.0, False, False, 3.0, 100.0)
        line = Line([seg_dot, seg_word], 90.0, 110.0)

        # In visual reading order, 'الكتاب' should precede '.'
        self.assertTrue(line.is_rtl)
        self.assertEqual(line.segs[0].text, "الكتاب")
        self.assertEqual(line.segs[1].text, ".")

    def test_arabic_letter_no_split(self):
        """Ensure connected Arabic letters across adjacent spans do NOT get forced spaces."""
        from pdf_book_to_text import Seg, Line, BookExtractor, Options
        ex = BookExtractor(Path("dummy.pdf"), Options())
        # Two connected segments of 'جيد' and 'اً' with zero or negligible gap
        seg1 = Seg(310.0, 330.0, "جيد", 12.0, False, False, 6.0, 100.0)
        seg2 = Seg(305.0, 310.0, "اً", 12.0, False, False, 6.0, 100.0)
        ln = Line([seg2, seg1], 90.0, 110.0)
        joined = ex._join_line_spans(ln, code=False)
        self.assertNotIn("جيد ا", joined)


class TestTablesAndFootnotes(unittest.TestCase):
    def test_table_ascii_and_markdown(self):
        from pdf_book_to_text import render_table, render_table_md
        rows = [
            ["Option", "Description", "Default"],
            ["--format", "Output format (txt, md, jsonl)", "txt"],
            ["-j", "Parallel worker jobs", "1"],
        ]
        ascii_grid = render_table(rows)
        self.assertTrue(any("+---" in line for line in ascii_grid))
        self.assertTrue(any("Output format" in line for line in ascii_grid))

        md_grid = render_table_md(rows)
        self.assertEqual(md_grid[0], "| Option | Description | Default |")
        self.assertEqual(md_grid[1], "| --- | --- | --- |")
        self.assertIn("--format", md_grid[2])


if __name__ == "__main__":
    unittest.main()
