#!/usr/bin/env python3
"""pdf_book_to_text — Backwards-compatible CLI & library façade for pdf_extractor.

Copyright (c) 2026 AZZANI (https://github.com/7AZZANI).
Licensed under PolyForm Noncommercial 1.0.0 & Commercial Dual License (see LICENSE).
"""
from __future__ import annotations

import sys

from pdf_extractor import (
    CHAR_MAP,
    CHAR_TRANS,
    CODE_TRANS,
    TEXT_FLAGS,
    BookExtractor,
    Code,
    Footnote,
    Heading,
    Line,
    Note,
    Options,
    PageData,
    PageMark,
    Para,
    Pre,
    Seg,
    __author__,
    __version__,
    clean_text,
    clean_ws,
    code_score,
    count_rtl_chars,
    gh_slug,
    guess_code_language,
    is_predominantly_rtl,
    is_rtl_text,
    norm_key,
    render_md_table,
    render_table,
    render_table_md,
    repair_metadata,
    safe_filename,
    sanitize_text,
    slugify,
    split_by_chapters,
    strip_code_listing_numbers,
    strip_listing_numbers,
    text_direction,
)
from pdf_extractor.cli import (
    build_arg_parser,
    collect_pdfs,
    main,
    parse_page_ranges,
    plan_targets,
    process_file,
    write_output,
)
from pdf_extractor.code_detect import guess_language
from pdf_extractor.constants import FORMAT_EXT, LANG_RULES
from pdf_extractor.models import order_segments
from pdf_extractor.raw import (
    extract_raw_lines,
    extract_raw_lines_from_dict,
    make_seg,
    merge_baselines,
    merge_line_group,
)
from pdf_extractor.renderers import render_jsonl, render_md, render_txt

__all__ = [
    "BookExtractor",
    "CHAR_MAP",
    "CHAR_TRANS",
    "CODE_TRANS",
    "Code",
    "FORMAT_EXT",
    "Footnote",
    "Heading",
    "LANG_RULES",
    "Line",
    "Note",
    "Options",
    "PageData",
    "PageMark",
    "Para",
    "Pre",
    "Seg",
    "TEXT_FLAGS",
    "__author__",
    "__version__",
    "build_arg_parser",
    "clean_text",
    "clean_ws",
    "code_score",
    "collect_pdfs",
    "count_rtl_chars",
    "extract_raw_lines",
    "extract_raw_lines_from_dict",
    "gh_slug",
    "guess_code_language",
    "guess_language",
    "is_predominantly_rtl",
    "is_rtl_text",
    "main",
    "make_seg",
    "merge_baselines",
    "merge_line_group",
    "norm_key",
    "order_segments",
    "parse_page_ranges",
    "plan_targets",
    "process_file",
    "render_jsonl",
    "render_md",
    "render_md_table",
    "render_table",
    "render_table_md",
    "render_txt",
    "repair_metadata",
    "safe_filename",
    "sanitize_text",
    "slugify",
    "split_by_chapters",
    "strip_code_listing_numbers",
    "strip_listing_numbers",
    "text_direction",
    "write_output",
]

if __name__ == "__main__":
    sys.exit(main())
