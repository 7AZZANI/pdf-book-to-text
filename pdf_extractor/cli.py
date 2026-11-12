"""Command-line interface, batch target planner, and parallel worker runner."""
from __future__ import annotations

import argparse
import os
import re
import sys
import unicodedata
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from typing import Optional

from pdf_extractor.constants import CHAR_TRANS, FORMAT_EXT, __version__
from pdf_extractor.extractor import BookExtractor
from pdf_extractor.models import Code, Options
from pdf_extractor.sanitize import clean_text

_log_print = print


def _log(msg: str, opts: Options) -> None:
    if not opts.quiet:
        _log_print(msg, file=sys.stderr if opts.stdout else sys.stdout)


def parse_page_ranges(spec: str, total_pages: int) -> list[int]:
    """Parse 1-based page range specs (e.g. '1-5,10,20-') into sorted 0-based indices."""
    pages: set[int] = set()
    for raw_part in spec.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if "-" in part:
            lo_s, hi_s = part.split("-", 1)
            try:
                lo = int(lo_s.strip()) if lo_s.strip() else 1
                hi = int(hi_s.strip()) if hi_s.strip() else total_pages
            except ValueError as exc:
                raise ValueError(f"Invalid page range: {part!r}") from exc
        else:
            try:
                lo = hi = int(part)
            except ValueError as exc:
                raise ValueError(f"Invalid page number: {part!r}") from exc
        if lo > hi:
            lo, hi = hi, lo
        pages.update(range(max(1, lo) - 1, min(total_pages, hi)))
    return sorted(pages)


def write_output(path: Path, text: str, opts: Options) -> None:
    """Write output text, using UTF-8 BOM only for .txt unless --no-bom is set."""
    enc = "utf-8-sig" if (not opts.no_bom and opts.format == "txt") else "utf-8"
    path.write_text(text, encoding=enc, newline="\n")


def collect_pdfs(inputs: list[str], recursive: bool = False) -> list[tuple[Path, Optional[Path]]]:
    """Resolve input files/folders into deduplicated (pdf_path, root_dir_or_None) tuples."""
    found: list[tuple[Path, Optional[Path]]] = []
    seen: set[Path] = set()

    for raw in inputs:
        cleaned = raw.strip().strip("'\"")
        if cleaned.startswith("file://"):
            cleaned = cleaned[7:]
        path = Path(cleaned).expanduser()
        if not path.exists() and path.parent.is_dir():
            want = clean_text(unicodedata.normalize("NFC", path.name)).translate(CHAR_TRANS).replace("\ufffd", "").strip()
            for cand in path.parent.glob("*.pdf"):
                have = clean_text(unicodedata.normalize("NFC", cand.name)).translate(CHAR_TRANS).replace("\ufffd", "").strip()
                if want and have == want:
                    path = cand
                    break

        if path.is_dir():
            iterator = path.rglob("*") if recursive else path.glob("*")
            items = [(p, path) for p in sorted(iterator) if p.is_file() and p.suffix.lower() == ".pdf"]
        elif path.is_file():
            items = [(path, None)]
        else:
            print(f"Warning: path not found: {path}", file=sys.stderr)
            items = []

        for pdf_path, root in items:
            key = pdf_path.resolve()
            if key not in seen:
                seen.add(key)
                found.append((pdf_path, root))
    return found


def plan_targets(
    items: list[tuple[Path, Optional[Path]]],
    out: Optional[Path],
    ext: str,
    raw_output: Optional[str] = None,
) -> list[Path]:
    """Determine collision-free output path per input PDF.

    Single-file '-o my_out_file' without trailing slash targets that exact file path;
    directory targets mirror folder hierarchies.
    """
    targets: list[Path] = []
    used: set[Path] = set()
    single = len(items) == 1
    raw_str = raw_output if raw_output is not None else (str(out) if out is not None else "")
    explicit_dir = raw_str.endswith(("/", os.sep))

    for pdf, root in items:
        if out is None:
            target = pdf.with_suffix(ext)
        elif single and not out.is_dir() and not explicit_dir:
            target = out
        else:
            rel = pdf.relative_to(root).with_suffix(ext) if root is not None else Path(pdf.stem + ext)
            target = out / rel

        base = target
        idx = 2
        while target.resolve() in used:
            stem = base.stem
            suffix = base.suffix
            target = base.with_name(f"{stem} ({idx}){suffix}")
            idx += 1
        used.add(target.resolve())
        targets.append(target)
    return targets


def process_file(
    pdf: Path,
    out_path: Optional[Path],
    opts: Options,
    to_stdout: bool = False,
) -> tuple[bool, str, Optional[Path]]:
    """Extract and write a single PDF. Returns (ok, summary, resolved_output_path)."""
    _log(f"\n[PDF] {pdf}", opts)
    ext = FORMAT_EXT[opts.format]
    ex = BookExtractor(pdf, opts)
    try:
        ex.run()
        text = ex.render(ex.elements)
        if to_stdout:
            sys.stdout.write(text)
            sys.stdout.flush()
            out_path = Path("-")
        else:
            if out_path is None:
                out_path = pdf.with_suffix(ext)
            elif out_path.is_dir():
                out_path = out_path / (pdf.stem + ext)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            write_output(out_path, text, opts)
            if opts.split_chapters:
                folder = out_path.with_name(out_path.stem + "_chapters")
                folder.mkdir(parents=True, exist_ok=True)
                for chap_slug, els in ex.chapters():
                    write_output(folder / f"{chap_slug}{ext}", ex.render(els, include_header=False), opts)
                _log(f"  Chapters written to: {folder}", opts)

        st = ex.stats()
        langs = Counter(e.lang for e in ex.elements if isinstance(e, Code) and e.lang)
        lang_txt = f" ({', '.join(f'{k} {v}' for k, v in langs.most_common(3))})" if langs else ""
        src_note = {
            "font": "",
            "style": " [code font style detected]",
            "syntax": " [code detected by syntax]",
            "none": "",
        }.get(ex.code_source, "")
        heading_src = "PDF bookmarks" if ex.toc_used else "font analysis"
        summary = (
            f"{len(ex.pages)} pages | {st.get('Heading', 0)} headings | {st.get('Para', 0)} paragraphs | "
            f"{st.get('Code', 0)} code blocks{lang_txt}{src_note} | {st.get('Note', 0)} footnotes | "
            f"{len(text):,} chars | headings from {heading_src} | {ex.elapsed:.1f}s"
        )
        if ex.warnings:
            summary += "".join(f"\n  WARNING: {w}" for w in ex.warnings)
        _log(f"  Saved: {out_path}\n    {summary}", opts)
        return True, summary, out_path
    except Exception as exc:
        _log(f"  Failed: {exc}", opts)
        return False, f"Failed: {exc}", out_path
    finally:
        ex.close()


def _worker(job: tuple[Path, Path, Options]) -> tuple[Path, bool, str, Optional[Path]]:
    pdf, target, opts = job
    ok, summary, outp = process_file(pdf, target, replace(opts, quiet=True))
    return pdf, ok, summary, outp


def build_arg_parser() -> argparse.ArgumentParser:
    """Construct the CLI ArgumentParser."""
    p = argparse.ArgumentParser(
        prog="pdf-book-to-text",
        description="Extract clean, structured text (chapters, paragraphs, code listings, tables, RAG JSONL) from PDF books.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s book.pdf\n"
            "  %(prog)s book.pdf --format md\n"
            "  %(prog)s book.pdf --format jsonl --chunk-tokens 500 -o chunks.jsonl\n"
            "  %(prog)s ~/Books -r -o ~/Text -j 4 --skip-existing\n"
            "  %(prog)s scanned.pdf --ocr --ocr-lang eng+ara\n"
        ),
    )
    p.add_argument("inputs", nargs="*", help="PDF file(s) or folder(s). Prompts if omitted interactively.")
    p.add_argument("-o", "--output", help="Output file (single input) or output directory.")
    p.add_argument(
        "-f",
        "--format",
        choices=["txt", "md", "jsonl"],
        default="txt",
        help="Output format: txt (default), md (GitHub-Flavored Markdown), or jsonl (structured/RAG).",
    )
    p.add_argument("-r", "--recursive", action="store_true", help="Search folders recursively for PDFs.")
    p.add_argument("-j", "--jobs", type=int, default=1, help="Convert N books in parallel (0 = all CPU cores).")
    p.add_argument("--skip-existing", action="store_true", help="Skip books whose output is newer than the PDF.")
    p.add_argument("--stdout", action="store_true", help="Write result to standard output (single input only).")
    p.add_argument(
        "-w",
        "--width",
        type=int,
        default=None,
        help="Wrap paragraphs at N columns (0 = unwrapped). Default: 100 for txt, 0 for md/jsonl.",
    )
    p.add_argument(
        "--code-style",
        choices=["fence", "indent", "plain"],
        default="fence",
        help="How code blocks are marked in .txt: fence (default), indent, or plain.",
    )
    p.add_argument(
        "--code-lang",
        default="auto",
        help="Language tag for fenced code blocks: auto (default), none, or an explicit name.",
    )
    p.add_argument(
        "--no-detect-lang",
        dest="detect_lang",
        action="store_false",
        help="Disable automatic programming language detection inside code blocks.",
    )
    p.add_argument(
        "--keep-line-numbers",
        dest="strip_line_numbers",
        action="store_false",
        help="Keep printed line numbers at the start of code listing lines.",
    )
    p.add_argument(
        "--detect-code",
        choices=["auto", "font"],
        default="auto",
        help="Code detection strategy: auto (monospace + style + syntax, default) or font (monospace only).",
    )
    p.add_argument(
        "--footnotes",
        choices=["section", "drop", "inline"],
        default="section",
        help="Footnote handling: section (end of chapter/section, default), inline (in place), or drop.",
    )
    p.add_argument("--pages", help="1-based page range(s) to extract, e.g. '1-50' or '10-20,25,40-'.")
    p.add_argument("--page-markers", action="store_true", help="Insert [Page N] / <!-- Page N --> markers.")
    p.add_argument("--no-contents", dest="contents", action="store_false", help="Omit generated Table of Contents.")
    p.add_argument("--contents-depth", type=int, default=3, help="Depth of generated Table of Contents (default 3).")
    p.add_argument(
        "--keep-headers",
        dest="strip_headers",
        action="store_false",
        help="Keep running page headers, footers, and page numbers.",
    )
    p.add_argument("--columns", choices=["auto", "1", "2"], default="auto", help="Column layout detection (default auto).")
    p.add_argument("--tables", action="store_true", help="Extract ruled tables (ASCII grids in txt, GFM tables in md).")
    p.add_argument("--figures", action="store_true", help="Insert [Figure] placeholders with nearby captions.")
    p.add_argument(
        "--no-callouts",
        dest="callouts",
        action="store_false",
        help="Disable PDF vector callout-box detection (NOTE / WARNING / TIP boxes).",
    )
    p.add_argument("--ocr", action="store_true", help="Run Tesseract OCR on image-only/scanned pages.")
    p.add_argument("--ocr-lang", default="eng", help="Tesseract OCR language(s), e.g. 'eng', 'ara', or 'eng+ara'.")
    p.add_argument("--password", help="Password for encrypted PDFs.")
    p.add_argument("--split-chapters", action="store_true", help="Write one file per chapter into <name>_chapters/.")
    p.add_argument(
        "--ignore-bookmarks",
        dest="use_bookmarks",
        action="store_false",
        help="Ignore PDF outline bookmarks; derive headings from fonts only.",
    )
    p.add_argument(
        "--keep-code-quotes",
        dest="fix_code_quotes",
        action="store_false",
        help="Do not normalize smart quotes/dashes to ASCII inside code blocks.",
    )
    p.add_argument("--mark-inline-code", action="store_true", help="Wrap inline monospace tokens inside prose in `backticks`.")
    p.add_argument(
        "--chunk-tokens",
        type=int,
        default=0,
        help="For JSONL output, bundle elements into hierarchy-aware chunks of ~N tokens (default 0 = per-element).",
    )
    p.add_argument(
        "--chunk-overlap",
        type=int,
        default=0,
        help="For --chunk-tokens, carry N trailing overlap words inside consecutive section chunks (default 0).",
    )
    p.add_argument(
        "--no-bom",
        dest="no_bom",
        action="store_true",
        help="Write .txt as plain UTF-8 without BOM (md and jsonl never write a BOM).",
    )
    p.add_argument("-q", "--quiet", action="store_true", help="Suppress progress output.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point returning POSIX exit code."""
    args = build_arg_parser().parse_args(argv)
    inputs = list(args.inputs)
    if not inputs:
        try:
            raw = input("Enter the path to a PDF book (or a folder of PDFs): ").strip()
        except (EOFError, KeyboardInterrupt):
            return 1
        if not raw:
            return 1
        inputs = [raw]

    items = collect_pdfs(inputs, args.recursive)
    if not items:
        print("No PDF files found.", file=sys.stderr)
        return 1
    if args.stdout and len(items) > 1:
        print("--stdout works with a single PDF only.", file=sys.stderr)
        return 1

    opts = Options(
        format=args.format,
        width=args.width,
        code_style=args.code_style,
        code_lang=args.code_lang.lower(),
        detect_lang=args.detect_lang,
        strip_line_numbers=args.strip_line_numbers,
        detect_code=args.detect_code,
        footnotes=args.footnotes,
        pages=args.pages,
        page_markers=args.page_markers,
        contents=args.contents,
        contents_depth=args.contents_depth,
        strip_headers=args.strip_headers,
        columns=args.columns,
        tables=args.tables,
        figures=args.figures,
        callouts=args.callouts,
        ocr=args.ocr,
        ocr_lang=args.ocr_lang,
        password=args.password,
        split_chapters=args.split_chapters,
        use_bookmarks=args.use_bookmarks,
        fix_code_quotes=args.fix_code_quotes,
        mark_inline_code=args.mark_inline_code,
        chunk_tokens=max(0, args.chunk_tokens),
        chunk_overlap=max(0, args.chunk_overlap),
        no_bom=args.no_bom,
        stdout=args.stdout,
        jobs=args.jobs,
        skip_existing=args.skip_existing,
        quiet=args.quiet or args.stdout,
    )

    out = Path(args.output).expanduser() if args.output else None
    if out is not None and (len(items) > 1 or str(args.output).endswith(("/", os.sep))):
        out.mkdir(parents=True, exist_ok=True)
    targets = plan_targets(items, out, FORMAT_EXT[opts.format], raw_output=args.output)

    jobs: list[tuple[Path, Path, Options]] = []
    skipped = 0
    for (pdf, _root), target in zip(items, targets):
        if args.skip_existing and target.exists() and target.stat().st_mtime >= pdf.stat().st_mtime:
            skipped += 1
            _log(f"Skipped (up to date): {target}", opts)
            continue
        jobs.append((pdf, target, opts))

    if not jobs:
        if len(items) > 1 or skipped:
            _log(f"\nDone: 0 converted, {skipped} skipped (up to date).", opts)
        return 0

    ok_count = 0
    try:
        if args.stdout:
            pdf, _target, _opts = jobs[0]
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8")
            ok_count = int(process_file(pdf, None, opts, to_stdout=True)[0])
        elif args.jobs != 1 and len(jobs) > 1:
            workers = args.jobs if args.jobs > 0 else (os.cpu_count() or 2)
            _log(f"Converting {len(jobs)} book(s) with {workers} parallel workers ...", opts)
            with ProcessPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(_worker, j) for j in jobs]
                for idx, fut in enumerate(as_completed(futures), 1):
                    pdf, success, summary, outp = fut.result()
                    ok_count += int(success)
                    status = "OK" if success else "FAIL"
                    _log(f"[{idx}/{len(jobs)}] {status} {pdf.name} -> {outp}\n    {summary}", opts)
        else:
            for pdf, target, job_opts in jobs:
                ok_count += int(process_file(pdf, target, job_opts)[0])
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130

    if len(items) > 1:
        extra = f", {skipped} skipped (up to date)" if skipped else ""
        _log(f"\nDone: {ok_count}/{len(jobs)} converted{extra}.", opts)
    return 0 if ok_count == len(jobs) else 2
