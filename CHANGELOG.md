# Changelog

All notable changes to **pdf-book-to-text** are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.1.0] - 2026-11-13

### Added
- **Modular Architecture (`pdf_extractor/`)**: Split the codebase into focused modules (`constants.py`, `sanitize.py`, `models.py`, `code_detect.py`, `raw.py`, `tables.py`, `extractor.py`, `renderers.py`, `cli.py`) while keeping `pdf_book_to_text.py` as a backwards-compatible CLI and library façade.
- **Vector Callout Box Detection**: Automatically detects bordered/shaded `NOTE`, `WARNING`, `TIP`, `IMPORTANT`, and `CAUTION` vector boxes via PyMuPDF drawings and renders them as GitHub `[!NOTE]` Alerts in Markdown (`--no-callouts` to disable).
- **Hierarchy-Aware RAG Chunking (`--chunk-tokens`, `--chunk-overlap`)**: Optional section-scoped JSONL bundling with configurable overlap words, breadcrumb `section_path`, `page`, `page_index`, `rows`, `caption`, `type="callout"`, and `type="footnote"`.
- **Adaptive H1 Promotion**: Promotes main chapter headings to Level 1 when cover pages monopolize giant display fonts (`--use-bookmarks` disabled or PDFs lacking bookmarks).
- **Multi-Line Cover Title Joining**: Joins multi-line cover titles of matching display font size (e.g., *Hacking Secret Ciphers with Python*).
- **Community & Governance**: Added `CODE_OF_CONDUCT.md` and expanded CI verification in `.github/workflows/ci.yml`.
- **Regression Suite**: Added `tests/test_regressions_v21.py` bringing total test coverage to 36 unit and synthetic PDF tests.

### Fixed
- **Dropcaps**: Prepend large decorative initial letters directly to the following paragraph instead of emitting orphaned single-letter headings.
- **Footnotes**: Extract and strip bottom-of-page multi-line footnotes even under `--footnotes drop`, support true `--footnotes inline` expansion in TXT/MD, and scope section notes to chapter boundaries under `--split-chapters`.
- **Cross-Page Stitching with `--page-markers`**: Peel trailing `PageMark`/`Note` nodes before testing paragraph/code continuation across page breaks.
- **Monospace Kerning & Ligatures**: Stepwise glyph-advance reconstruction eliminates spurious kerning spaces (`www.packtpub.com`), excludes `Type3`/symbol/math fonts, and recovers `fi`/`fl`/`ffi`/`ffl` ligatures via `CHAR_MAP`.
- **Code & Language Detection**: Improved Python REPL (`>>>`), byte/f-string prefix rules, YAML vs Python disambiguation, strict consecutive-step `strip_listing_numbers`, `prev_ends_par` guard on hyphenated numeric text, and TOC leader-line exclusion from headings.
- **RTL BiDi & Margin Symmetry**: Exclude numeric digits from strong directional counts in `text_direction`, sort mixed RTL/LTR runs with neutral spans intact, and mirror RTL right-margin paragraph break geometry.
- **CLI & Encoding**: Restrict UTF-8 BOM strictly to `.txt` (`utf-8` always for `.md` and `.jsonl`), handle single-file `-o` without extension as a file path, deduplicate `collect_pdfs`, warn on scanned/PUA-garbled pages, and provide context-manager lifecycle cleanup (`with BookExtractor(...) as ex:`).

## [2.0.0] - 2026-11-12

### Added
- Structured Markdown (`.md`) and RAG JSON Lines (`.jsonl`) export formats.
- PDF outline bookmark alignment with printed section numbers and Unicode NFKC matching.
- Dual-tier monospace character-metric analysis and proportional code-style profiling.
- Arabic BiDi line/span ordering and dynamic vocabulary de-hyphenation.
- Parallel batch execution (`-j`), `--skip-existing`, `--tables`, `--figures`, and `--ocr` support.
