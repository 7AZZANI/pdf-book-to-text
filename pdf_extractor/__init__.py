"""pdf_extractor - Structured extraction engine for technical and multilingual PDF books."""
from __future__ import annotations

from pdf_extractor.constants import CHAR_MAP, CHAR_TRANS, CODE_TRANS, TEXT_FLAGS, __author__, __version__
from pdf_extractor.code_detect import code_score, guess_code_language, strip_code_listing_numbers, strip_listing_numbers
from pdf_extractor.extractor import BookExtractor, split_by_chapters
from pdf_extractor.models import Code, Footnote, Heading, Line, Note, Options, PageData, PageMark, Para, Pre, Seg
from pdf_extractor.sanitize import (
    clean_text,
    clean_ws,
    count_rtl_chars,
    gh_slug,
    is_predominantly_rtl,
    is_rtl_text,
    norm_key,
    repair_metadata,
    safe_filename,
    sanitize_text,
    slugify,
    text_direction,
)
from pdf_extractor.tables import render_md_table, render_table, render_table_md

__all__ = [
    "BookExtractor",
    "CHAR_MAP",
    "CHAR_TRANS",
    "CODE_TRANS",
    "Code",
    "Footnote",
    "Heading",
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
    "clean_text",
    "clean_ws",
    "code_score",
    "count_rtl_chars",
    "gh_slug",
    "guess_code_language",
    "is_predominantly_rtl",
    "is_rtl_text",
    "norm_key",
    "render_md_table",
    "render_table",
    "render_table_md",
    "repair_metadata",
    "safe_filename",
    "sanitize_text",
    "slugify",
    "split_by_chapters",
    "strip_code_listing_numbers",
    "strip_listing_numbers",
    "text_direction",
]
