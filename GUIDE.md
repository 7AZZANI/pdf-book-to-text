# Complete User & Developer Guide

This guide provides in-depth instructions, advanced recipes, programmatic Python integration, and technical explanations for [`pdf_book_to_text.py`](pdf_book_to_text.py).

---

## Table of Contents

1. [Installation & Setup](#1-installation--setup)
2. [Visual Screenshots & CLI Walkthrough](#2-visual-screenshots--cli-walkthrough)
3. [CLI Workflows & Advanced Examples](#3-cli-workflows--advanced-examples)
   * [Standard Extraction](#standard-extraction)
   * [Chapter Splitting](#chapter-splitting)
   * [LLM / RAG Corpus Ingestion](#llm--rag-corpus-ingestion)
   * [Multi-Column Layouts](#multi-column-layouts)
   * [Tables & Figures](#tables--figures)
   * [Scanned Books & OCR](#scanned-books--ocr)
4. [Python API (Programmatic Usage)](#4-python-api-programmatic-usage)
5. [Integration Guides](#5-integration-guides)
   * [Integrating with LangChain / LlamaIndex](#integrating-with-langchain--llamaindex)
   * [Importing into Obsidian / Notion](#importing-into-obsidian--notion)
6. [Under the Hood: Technical Architecture](#6-under-the-hood-technical-architecture)
   * [Mathematical Monospace Detection](#mathematical-monospace-detection)
   * [Margin-Band Header Filtering](#margin-band-header-filtering)
   * [Dynamic Vocabulary De-Hyphenation](#dynamic-vocabulary-de-hyphenation)
7. [Troubleshooting & Edge Cases](#7-troubleshooting--edge-cases)

---

## 1. Installation & Setup

### Requirements
* **Python**: 3.10 or later
* **PyMuPDF**: `>= 1.24.0`
* **Tesseract OCR** *(Optional, only needed if using `--ocr` on image-only scanned books)*:
  * Ubuntu/Debian: `sudo apt install tesseract-ocr`
  * macOS: `brew install tesseract`
  * Windows: [UB-Mannheim Tesseract installer](https://github.com/UB-Mannheim/tesseract/wiki)

### Recommended Environment Setup

```bash
# 1. Clone repository
git clone https://github.com/your-username/pdf-book-to-text.git
cd pdf-book-to-text

# 2. Create isolated virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt
```

---

## 2. Visual Screenshots & CLI Walkthrough

### 🖥️ Screenshot 1: Standard Extraction & Live Progress
When you run the tool on any PDF book, it automatically inspects character metrics, identifies code listings, filters margin headers, and extracts the full content with live progress:

<p align="center">
  <img src="docs/images/cli_demo.svg" alt="Terminal Run Walkthrough" width="100%" />
</p>

### 📁 Screenshot 2: Chapter Splitting (`--split-chapters`)
When `--split-chapters` is specified, the tool splits the book at level-1 headings, generating neatly numbered files ideal for Obsidian, Notion, Kindle, or LLM chunking:

<p align="center">
  <img src="docs/images/chapter_splitting_demo.svg" alt="Chapter Splitting Demo" width="100%" />
</p>

### ⚖️ Screenshot 3: Output Quality Comparison
Standard extractors like `pdftotext` scramble code indentation, inject headers into functions, mangle quotes, and invert Arabic words. `pdf_book_to_text.py` repairs all of these issues:

<p align="center">
  <img src="docs/images/before_after_comparison.svg" alt="Quality Comparison" width="100%" />
</p>

---

## 3. CLI Workflows & Advanced Examples

### Standard Extraction
```bash
python3 pdf_book_to_text.py book.pdf
```
* Generates `book.txt` next to `book.pdf`.
* Wraps lines at 100 characters for terminal and text-editor readability.
* Code blocks are wrapped with ```` ``` ```` fences and preserved with exact indentation.

---

### Chapter Splitting
```bash
python3 pdf_book_to_text.py book.pdf --split-chapters
```
* Generates `book.txt` (the entire book).
* Generates a directory named `book_chapters/` containing:
  ```text
  00 - Front Matter.txt
  01 - Preface.txt
  02 - Chapter 1: The way of the program.txt
  03 - Chapter 2: Variables, expressions and statements.txt
  04 - Chapter 3: Functions.txt
  ...
  ```
* Useful for reading one chapter at a time, importing into markdown note-taking apps, or chunking by chapter.

---

### LLM / RAG Corpus Ingestion
When ingesting documents into vector databases (Chroma, Pinecone, Weaviate) or embedding pipelines:
```bash
python3 pdf_book_to_text.py book.pdf -w 0 --page-markers -o rag_corpus.txt
```
* `-w 0`: Turns off paragraph wrapping so every paragraph is a single unbroken line. Embedding models perform substantially better on un-wrapped text.
* `--page-markers`: Injects `[Page 45]` markers throughout the text, allowing your RAG system to cite the exact physical page number in the original book.
* Code blocks remain naturally multi-line with exact indentation preserved.

---

### Multi-Column Layouts
Most technical books use a single-column layout, but some academic texts, papers, and older manuals use two columns:
* `--columns auto` *(Default)*: Automatically scans each page for a vertical gutter and determines reading order.
* `--columns 1`: Forces single-column mode.
* `--columns 2`: Forces two-column left-to-right reading order:
  ```bash
  python3 pdf_book_to_text.py two_column_manual.pdf --columns 2
  ```

---

### Tables & Figures
Technical books frequently contain summary tables and architecture diagrams:
```bash
python3 pdf_book_to_text.py book.pdf --tables --figures
```
* `--tables`: Uses PyMuPDF's table extraction engine to detect ruled tables and format them as bordered ASCII text grids:
  ```text
  +---------+-------------------+------------+
  | Command | Description       | Scope      |
  +=========+===================+============+
  | init    | Create repository | Local      |
  +---------+-------------------+------------+
  | clone   | Copy repository   | Remote     |
  +---------+-------------------+------------+
  ```
* `--figures`: Detects significant images and inserts a `[Figure]` placeholder to mark where graphics were positioned in the original text.

---

### Scanned Books & OCR
If a PDF was generated by scanning paper without running OCR (or with corrupted OCR layers):
```bash
python3 pdf_book_to_text.py scanned_book.pdf --ocr --ocr-lang eng+fra
```
* Automatically invokes Tesseract OCR on any page that contains only raster images.

---

## 4. Python API (Programmatic Usage)

You can import and use `BookExtractor` directly inside your Python applications:

```python
from pathlib import Path
from pdf_book_to_text import BookExtractor, Options

# Configure extraction settings
opts = Options(
    width=100,              # Wrap width (0 for no wrapping)
    code_style="fence",     # "fence", "indent", or "plain"
    split_chapters=True,    # Generate chapter files
    page_markers=False,     # Insert [Page N] tags
    strip_headers=True,     # Strip repeating headers & footers
    tables=False,           # ASCII table rendering
    quiet=False             # Show progress
)

# Initialize extractor
extractor = BookExtractor(Path("my_book.pdf"), opts)

# Execute extraction pipeline
elements = extractor.run()

# Render complete text
full_text = extractor.render(elements)
print(f"Extracted {len(full_text):,} characters from {extractor.book_title()}")

# Inspect chapters programmatically
for chapter_title, chapter_elements in extractor.chapters():
    chapter_text = extractor.render(chapter_elements, include_header=False)
    print(f"Chapter: {chapter_title} ({len(chapter_text):,} chars)")
```

---

## 5. Integration Guides

### Integrating with LangChain / LlamaIndex

For optimal Retrieval-Augmented Generation (RAG):

```python
from pathlib import Path
from pdf_book_to_text import BookExtractor, Options
from langchain_core.documents import Document

def load_pdf_book(pdf_path: str) -> list[Document]:
    opts = Options(width=0, page_markers=True, quiet=True)
    extractor = BookExtractor(Path(pdf_path), opts)
    extractor.run()
    
    docs = []
    book_title = extractor.book_title()
    
    for chapter_num, (title, elements) in enumerate(extractor.chapters()):
        content = extractor.render(elements, include_header=False)
        docs.append(Document(
            page_content=content,
            metadata={
                "source": pdf_path,
                "book_title": book_title,
                "chapter_index": chapter_num,
                "chapter_title": title,
            }
        ))
    return docs
```

---

### Importing into Obsidian / Notion

1. Run the chapter splitter:
   ```bash
   python3 pdf_book_to_text.py book.pdf --split-chapters
   ```
2. Rename the generated `.txt` files to `.md`:
   ```bash
   cd book_chapters
   for f in *.txt; do mv "$f" "${f%.txt}.md"; done
   ```
3. Move the `book_chapters` folder directly into your Obsidian vault or drag the files into Notion. Code blocks are already enclosed in ```` ``` ```` Markdown fences.

---

## 6. Under the Hood: Technical Architecture

### Mathematical Monospace Detection
Standard tools rely on font names containing substrings like `"mono"` or `"courier"`. In practice, technical books use dozens of custom or localized monospace fonts (e.g. `mplus1mn-regular`, `SFTT1000`, `tx-tt`, `SourceCodePro`).

`pdf_book_to_text.py` uses a **two-tier detection model**:
1. **Name Pattern Matching**: Fast pre-filtering via regular expressions matching standard font families.
2. **Character Metric Analysis**: On the first 25 pages, the tool measures the advance width of Latin characters normalized by font size:
   $$\Delta w = |w(\text{'i'}) - w(\text{'m'})|$$
   In proportional fonts, $\Delta w > 0.25$. In monospaced fonts, $\Delta w = 0.00$. If glyph widths are uniform across distinct characters, the font is mathematically classified as code.

---

### Margin-Band Header Filtering
Standard tools either leave running headers in place or use naive repetition counters. However, in books where the running header alternates between the chapter title on even pages and the section title on odd pages, section titles only appear on 1 or 2 pages, bypassing repetition filters.

`pdf_book_to_text.py` solves this with **geometric margin clustering**:
1. It records all lines within the top 12% and bottom 12% of page height across the book.
2. It clusters the exact vertical positions ($y$-coordinates) into dominant margin bands.
3. Any text sitting inside a dominant margin band with a body font size or smaller is safely stripped, regardless of what text it contains.

---

### Dynamic Vocabulary De-Hyphenation
Line-breaking in printed books splits words across line boundaries with hyphens (e.g., `al-` on line 1, `gorithm` on line 2). Simply removing all hyphens breaks true compound words (`state-of-the-art`, `read-only`, `client-side`).

`pdf_book_to_text.py` builds an internal vocabulary set of all whole words appearing throughout the book. When encountering a hyphenated line break:
* If `f"{part_a}-{part_b}"` exists in the book's vocabulary as a recognized compound word, the hyphen is retained.
* Otherwise, the soft hyphen is removed and the word is seamlessly re-joined (`algorithm`).

---

## 7. Troubleshooting & Edge Cases

| Issue | Cause | Solution |
| :--- | :--- | :--- |
| **English text displays as Chinese characters** | Corrupt PDF bookmarks contained null bytes (`\x00`), causing Windows Notepad to misguess UTF-16LE | Fixed automatically by `pdf_book_to_text.py` via bookmark sanitization and saving with UTF-8 BOM (`utf-8-sig`). |
| **Arabic text words reversed or separated** | Naive extractors sort RTL text spans from left to right | Handled automatically: span sequences are preserved in natural RTL reading order without word inversion. |
| **Garbled characters or question marks** | PDF uses fonts with missing or non-standard Unicode ToUnicode mappings | Run with `--ocr` to re-extract the text using visual character recognition. |
| **Password protected PDF** | Document has encryption | Pass `--password "your_password"`. |
| **Inline code is not in backticks** | Default behavior treats inline code as normal text flow | Pass `--mark-inline-code` to wrap inline monospace terms in `` `backticks` ``. |
| **Too many blank lines between sections** | Book has large decorative vertical whitespace | Handled automatically; output normalizes consecutive empty lines to a maximum of 3. |

---

## License & Commercial Inquiries

Copyright (c) 2026 **Majed Ibrahim Alotaibi (7AZZANI)**.
* Free for personal, academic, and non-commercial open-source use.
* For commercial licensing or enterprise use, contact `majed.alotaibi1011@gmail.com` / `agz97agz97@gmail.com`.
* See the full [`LICENSE`](LICENSE) for complete terms.
