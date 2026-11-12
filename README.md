<div align="center">

# 📖 PDF Book to Text Extractor v2.1

**Extract clean, publication-grade Plain Text, GitHub Markdown, and hierarchy-aware RAG JSONL chunks from PDF books — engineered for programming, technical, and multilingual RTL literature.**

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg?style=flat-square)](https://www.python.org/downloads/)
[![License: Non-Commercial / Dual](https://img.shields.io/badge/License-Non--Commercial%20%2F%20Dual-orange.svg?style=flat-square)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey.svg?style=flat-square)](#installation)
[![Tests](https://img.shields.io/badge/tests-passing-brightgreen.svg?style=flat-square)](tests/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat-square)](#contributing)

[Quick Start](#-quick-start) • [The Solution](#-the-problem-vs-the-solution) • [CLI Reference](#-cli-options-reference) • [Recipes](#-common-recipes) • [Architecture](#-architecture) • [Full Guide](GUIDE.md)

</div>

---

## ⚡ The Problem vs. The Solution

Standard PDF extraction tools (`pdftotext`, `pypdf`, `pdfplumber`) treat technical books like simple plain text documents. This breaks code listings, loses indentation, injects running headers into the middle of functions, corrupts directory tree characters, and destroys chapter hierarchies.

<p align="center">
  <img src="docs/images/before_after_comparison.svg" alt="Standard Extractors vs pdf-book-to-text" width="100%" />
</p>

### ❌ Standard Tool Output (`pdftotext` / naive extractors):
```python
# - Indentation is lost or scrambled
# - Running header injected right into the function!
# - Curly quotes break Python syntax
# - Directory tree lines corrupt into '%'

def calculate_wholesale(copies):
 wholesale_price = 24.95 * 0.60
Chapter 3. Functions 19
 shipping = 3.0 + 0.75 * (copies - 1)
 return wholesale_price * copies + shipping

print(‘Total cost:’, calculate_wholesale(60))
```

### ✅ `pdf-book-to-text` Output (v2.1):
```python
# - Automatic language-tagged code block (```python)
# - Exact 4-space indentation and alignment preserved
# - Running headers & footers automatically stripped
# - Curly quotes normalized to valid ASCII syntax
# - Box-drawing characters (├──, └──) kept 100% intact

def calculate_wholesale(copies):
    wholesale_price = 24.95 * 0.60
    shipping = 3.0 + 0.75 * (copies - 1)
    return wholesale_price * copies + shipping

print('Total cost:', calculate_wholesale(60))
```

---

## ✨ Features

* **Multi-Format Export**:
  * **Plain Text (`.txt`)**: Underlined chapter headings (`====`, `----`, `~~~~`), preserved indentation, callout tags (`[NOTE]`), and bordered ASCII tables.
  * **GitHub Markdown (`.md`)**: Proper `#` heading hierarchy, fenced code blocks with detected language tags (```` ```python ````, ```` ```bash ````, ```` ```sql ````, ```` ```yaml ````), GitHub `[!NOTE]`/`[!WARNING]` alerts for callout boxes, and GFM tables with captions.
  * **RAG JSONL (`.jsonl`)**: Structured records AND optional semantic `--chunk-tokens` / `--chunk-overlap` bundles with book metadata, `section_path` breadcrumbs, printed `page`, `page_index`, structured table `rows`, captions, callouts, and footnotes ready for vector databases (Chroma, Qdrant, Milvus, LangChain, LlamaIndex).
* **Authoritative Chapter Hierarchy**: Uses PDF bookmarks (Table of Contents) with adaptive H1 promotion when cover pages monopolize large display fonts, while retaining printed section numbering (`3.1 Function calls`, `3.2 Math functions`).
* **Three-Tier Code & Language Detection**:
  * Regex matching for popular monospace families (`Courier`, `Consolas`, `JetBrains Mono`, `Fira Code`, `M+`, `DejaVu`, etc.) excluding symbol/Type3 fonts.
  * **Step-by-step monospace advance reconstruction**: Measures real glyph advances to prevent kerning-split spaces inside URLs (`www.packtpub.com`) and code.
  * **Proportional Font & Syntax Code Detection**: Detects code snippets even in proportional body fonts (keyed by `(font, size)` style tuples and syntax rules including Python REPL `>>>` and YAML vs Python disambiguation).
* **Code Listing Line Number Stripping & Dropcaps**: Strictly strips consecutive printed listing line numbers (`9  if x:`) without touching spaced constants, and seamlessly rejoins illuminated dropcap letters (`"T"` + `"he"` -> `"The"`).
* **Vector Callouts, Ruled Tables & Figure Clustering**: Extracts `NOTE`/`WARNING`/`TIP` callout boxes from PDF vector drawings, extracts GFM/ASCII tables while protecting boxed code listings from false table capture, and merges multi-tile raster figures with nearby captions.
* **Clean Text & Box-Drawing Integrity**: Recovers standard ligatures (`fi`, `fl`), keeps box-drawing tree characters (`├──`, `└──`, `│`, `─` U+2500) and HTML entities (`&lt;div&gt;`) intact.
* **Full Multilingual & Arabic (RTL) Support**: Native Right-to-Left BiDi ordering (excluding digits from strong LTR runs), RTL right-margin paragraph break detection, connected Arabic letter preservation, and Unicode NFKC outline matching.
* **Margin Band Header/Footer Removal & Scoped Footnotes**: Removes recurring page headers/footers across pages, isolates multi-line footnotes cleanly to section/chapter boundaries (preserving cross-page stitching even with `--page-markers`), and warns explicitly on scanned or PUA-garbled pages.
* **Modular Architecture & High-Speed Parallel Batch**: Clean [`pdf_extractor/`](pdf_extractor/) package + backwards-compatible [`pdf_book_to_text.py`](pdf_book_to_text.py) CLI/library façade, with multi-core batch conversion (`-j N`) and `--skip-existing` caching.

---

## 🚀 Quick Start

### 1. Installation

```bash
# Clone the repository
git clone https://github.com/7AZZANI/pdf-book-to-text.git
cd pdf-book-to-text

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install the package and dependencies
pip install -e .
```

### 2. Basic Usage

Extract any PDF book into a `.txt` file:
```bash
pdf-book-to-text book.pdf
# Output: book.txt next to the PDF
```

Extract to **GitHub-Flavored Markdown** with tagged code blocks:
```bash
pdf-book-to-text book.pdf --format md
# Output: book.md
```

Extract to **RAG JSONL Chunks** for vector search and LLMs:
```bash
pdf-book-to-text book.pdf --format jsonl
# Output: book.jsonl
```

Or run interactively (prompts for file or folder):
```bash
pdf-book-to-text
```

---

## 🛠️ Common Recipes

### 📚 Recipe 1: Split into Chapter Files (for Obsidian, Notion, or e-Readers)
Creates the main file plus a `<book>_chapters/` folder with individual chapter files:
```bash
pdf-book-to-text book.pdf --format md --split-chapters
```
```text
book_chapters/
  ├── 00_front_matter.md
  ├── 01_preface.md
  ├── 02_chapter_1_network_reconnaissance.md
  ├── 03_chapter_2_socket_programming.md
  └── ...
```

### 🤖 Recipe 2: Vector Search & RAG Embeddings (JSONL)
Extracts structured records OR hierarchy-aware token-budget chunks (`--chunk-tokens`) with chapter breadcrumbs and page metadata:
```bash
# Per-element structured records
pdf-book-to-text book.pdf --format jsonl -o knowledge_base.jsonl

# Or bundle elements into ~500-token chunks with 40-word overlap
pdf-book-to-text book.pdf --format jsonl --chunk-tokens 500 --chunk-overlap 40 -o chunks.jsonl
```

Example JSONL record:
```json
{
  "type": "code",
  "language": "python",
  "text": "import socket\ns = socket.socket(socket.AF_INET, socket.SOCK_STREAM)",
  "id": 42,
  "page": "19",
  "page_index": 29,
  "chapter": "Warming up - Your First Shell",
  "section_path": ["Warming up - Your First Shell", "TCP reverse shell"],
  "book": "Python for Offensive PenTest",
  "source": "book.pdf"
}
```

### 📁 Recipe 3: High-Speed Parallel Batch Conversion
Recursively process an entire library across 4 CPU cores, skipping previously converted books:
```bash
pdf-book-to-text ~/Books/ -r -j 4 --format md -o ~/MarkdownBooks/ --skip-existing
```

### 📑 Recipe 4: Extract Specific Page Ranges
Test a sample range or extract specific chapters:
```bash
pdf-book-to-text book.pdf --pages 10-50,85,120-140 --format md
```

### 🖼️ Recipe 5: Scanned PDFs (OCR Fallback)
Extract text from scanned pages using Tesseract:
```bash
pdf-book-to-text scanned_book.pdf --ocr --ocr-lang eng
```

---

## 📖 CLI Options Reference

| Flag | Default | Description |
| :--- | :---: | :--- |
| `inputs` | *Interactive* | PDF file(s) or directory paths to process. |
| `-o`, `--output` | `<name>.<ext>` | Destination output file or folder. Subdirectories are preserved in batch mode. |
| `-f`, `--format` | `txt` | Output format: `txt` (plain text), `md` (Markdown with ```` ```lang ````), `jsonl` (RAG chunks). |
| `-j`, `--jobs` | `1` | Number of parallel worker processes for batch extraction. |
| `--skip-existing` | `False` | Skip files that already have target output. |
| `-r`, `--recursive` | `False` | Recursively scan folders for `.pdf` files. |
| `-w`, `--width` | `100` / `0` | Paragraph wrap width (`100` default for `.txt`, `0` unwrapped for `.md`/`.jsonl`; code is never wrapped). |
| `--code-style` | `fence` | Code block format in plain text: `fence` (```` ``` ````), `indent` (4 spaces), or `plain`. |
| `--code-lang` | `auto` | Language tag for code fences: `auto` (detect), `none`, or an explicit name like `python`. |
| `--detect-code` | `auto` | Code detection mode: `auto` (monospace + style + syntax) or `font` (monospace only). |
| `--no-detect-lang` | `False` | Disable automatic programming language detection for code fences. |
| `--keep-line-numbers` | `False` | Keep leading printed listing line numbers in code blocks instead of stripping them. |
| `--footnotes` | `section` | Footnote handling: `section` (end of chapter/section), `inline` (expanded in place), or `drop`. |
| `--no-callouts` | `False` | Disable automatic detection of boxed callout notes (`NOTE`, `WARNING`, `TIP`). |
| `--chunk-tokens` | `0` | In `--format jsonl`, pack elements into section-scoped RAG chunks targeting N tokens. |
| `--chunk-overlap` | `0` | Trailing overlap words carried across consecutive `--chunk-tokens` chunks in the same section. |
| `--no-bom` | `False` | Write `.txt` as plain UTF-8 without BOM (`.md` and `.jsonl` never write a BOM). |
| `--stdout` | `False` | Output text directly to standard output. |
| `--split-chapters` | `False` | Also outputs individual files per chapter into `<name>_chapters/`. |
| `--pages` | *All* | Page range filter (e.g. `1-20`, `35-50,75`). |
| `--page-markers` | `False` | Inserts `[Page N]` indicators to locate source pages. |
| `--no-contents` | `False` | Omits the Table of Contents block from the document header. |
| `--contents-depth` | `3` | Maximum outline depth for the generated Table of Contents header. |
| `--keep-headers` | `False` | Disables header, footer, and page number removal. |
| `--columns` | `auto` | Column layout: `auto`, `1` (single column), or `2` (two column). |
| `--tables` | `False` | Detects grid tables and formats them as ASCII or Markdown tables. |
| `--figures` | `False` | Inserts `[Figure]` placeholders where images appear. |
| `--ocr` | `False` | OCRs image-only pages (requires Tesseract). |
| `--ocr-lang` | `eng` | Tesseract language code (e.g., `eng`, `ara`, `eng+deu`). |
| `--password` | `None` | Password for encrypted PDF files. |
| `--mark-inline-code` | `False` | Wraps inline monospace code snippets in `` `backticks` ``. |
| `-q`, `--quiet` | `False` | Suppresses progress output. |

---

## 🏗️ Architecture

```mermaid
flowchart TD
    A["Input PDF File"] --> B["1. Document Profiler\n• Font metrics (excl. Type3/symbol)\n• (font, size) proportional code profiling\n• Repeating margin-band coordinates"]
    B --> C["2. Raw Line & Block Extractor\n• Stepwise monospace advance recovery\n• Vector callouts, ruled tables, figure clusters\n• BiDi RTL segment ordering & ligature recovery"]
    C --> D["3. Running Header/Footer & Footnotes\n• Margin-band header suppression\n• Multi-line footnote isolation & section scoping"]
    D --> E["4. Chapter & Outline Engine\n• Unicode NFKC bookmark alignment\n• Adaptive H1 promotion past cover displays"]
    E --> F["5. Code Reconstruction Engine\n• Indent + strict listing number stripping\n• Language detection (Python REPL, YAML, Bash, SQL...)\n• Cross-page Para/Code stitching"]
    F --> G["6. Paragraph Reflow\n• Vocabulary compound-hyphen resolution\n• Dropcap prepending & RTL margin breaks"]
    G --> H["7. Multi-Format & RAG Output\n• Plain Text (.txt)\n• GitHub Markdown (.md w/ [!NOTE] alerts)\n• Per-element or Chunked JSONL (.jsonl)"]
```

---

## 🧪 Testing

The test suite runs automatically with `pytest`:

```bash
pytest -v
```

Tests cover (`tests/test_extractor.py` & `tests/test_regressions_v21.py`):
* Box-drawing character preservation (`├──`, `└──`, `│`, `─` ord 0x2500) and ligature recovery (`fi`, `fl`).
* HTML entity preservation in code listings (`&lt;div&gt;`) and monospace advance kerning (`www.packtpub.com`).
* Dropcaps, multi-line footnotes (`section`, `inline`, `drop`, chapter isolation), and cross-page stitching with `--page-markers`.
* Vector callout boxes (`> [!NOTE]`), ruled tables (`--tables`) without stealing boxed code, and raster figure clustering (`--figures`).
* Multilingual Unicode NFKC bookmarks, Arabic RTL BiDi ordering (with Latin digits), and RTL right-margin breaks.
* Programming language disambiguation (Python vs YAML vs Bash/SQL/JSON, `>>>` REPL), strict sequential listing number stripping, and proportional/syntax fallback.
* Hierarchy-aware RAG JSONL chunking (`--chunk-tokens` / `--chunk-overlap`), UTF-8 BOM guarantees, CLI `-o` file/dir planning, and context-manager cleanup.

---

## 🤝 Contributing

Contributions, bug reports, and suggestions are welcome!
1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## 📄 License & Commercial Inquiries

Copyright (c) 2026 **AZZANI**. All rights reserved.

* **Free Non-Commercial Use**: This software is free for personal use, education, research, and non-commercial open-source projects under the [AZZANI Non-Commercial Dual License](LICENSE).
* **Commercial Use**: Any use in commercial products, proprietary software, SaaS platforms, closed-source systems, or for-profit workflows requires a separate commercial license agreement.
* **Commercial Licensing Inquiries**:
  * 📧 Email: [info@7azzani.com](mailto:info@7azzani.com) / [agz97agz97@gmail.com](mailto:agz97agz97@gmail.com)
  * 🐙 GitHub: [@7AZZANI](https://github.com/7AZZANI)

See the full [`LICENSE`](LICENSE) for complete legal terms.
