"""Output renderers for Plain Text (.txt), GitHub-Flavored Markdown (.md), and RAG JSONL (.jsonl)."""
from __future__ import annotations

import json
import re
import textwrap
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pdf_extractor.constants import FN_TOKEN_RE, __version__
from pdf_extractor.models import Code, Heading, Note, PageMark, Para, Pre
from pdf_extractor.sanitize import clean_ws, gh_slug, repair_metadata
from pdf_extractor.tables import render_md_table

if TYPE_CHECKING:
    from pdf_extractor.extractor import BookExtractor

_MD_ALERT_MAP = {
    "NOTE": "NOTE",
    "TIP": "TIP",
    "HINT": "TIP",
    "BEST PRACTICE": "TIP",
    "IMPORTANT": "IMPORTANT",
    "KEY POINT": "IMPORTANT",
    "REMEMBER": "IMPORTANT",
    "WARNING": "WARNING",
    "CAUTION": "WARNING",
    "DANGER": "CAUTION",
    "ALERT": "CAUTION",
}


def _fn_id(pno: int, mark: str) -> str:
    safe = mark if mark.isdigit() else "n" + "".join(f"{ord(c):x}" for c in mark)
    return f"p{pno + 1}-{safe}"


def _build_note_map(extractor: BookExtractor, elements: list[Any]) -> dict[tuple[int, str], str]:
    notes: dict[tuple[int, str], str] = {}
    for n in getattr(extractor, "footnotes_list", []):
        notes[(n.pno, n.mark)] = n.text
    for el in elements:
        if isinstance(el, Note):
            notes[(el.pno, el.mark)] = el.text
    return notes


def _format_refs(
    text: str,
    fmt: str,
    mode: str,
    note_map: dict[tuple[int, str], str],
) -> tuple[str, list[tuple[int, str, str]]]:
    """Expand, convert, or strip internal footnote tokens cleanly."""
    if "\ue000" not in text:
        return text, []

    refs: list[tuple[int, str, str]] = []
    seen_local: set[tuple[int, str]] = set()

    def repl(match: re.Match[str]) -> str:
        pno = int(match.group(1))
        mark = match.group(2)
        ftext = note_map.get((pno, mark), "")
        if mode == "drop":
            return ""
        if fmt == "md":
            if (pno, mark) not in seen_local:
                seen_local.add((pno, mark))
                refs.append((pno, mark, ftext))
            return f"[^{_fn_id(pno, mark)}]"
        if mode == "inline" and ftext:
            if (pno, mark) not in seen_local:
                seen_local.add((pno, mark))
                refs.append((pno, mark, ftext))
            return f" [{mark}: {ftext}]"
        return f"[{mark}]"

    rendered = FN_TOKEN_RE.sub(repl, text)
    if mode == "drop":
        rendered = re.sub(r" {2,}", " ", rendered).strip()
    return rendered, refs


def _code_body(extractor: BookExtractor, code: Code) -> list[str]:
    if code.rendered_lines is not None:
        return list(code.rendered_lines)
    return extractor._compute_code_body(code)


def _header_block(extractor: BookExtractor, rule: int, elements: list[Any]) -> list[str]:
    meta = extractor.metadata or {}
    title = extractor.book_title()
    total = getattr(extractor.doc, "page_count", len(extractor.pages)) if extractor.doc else len(extractor.pages)
    lines = ["=" * rule]
    lines += textwrap.wrap(title.upper(), rule) or [""]
    author = repair_metadata(meta.get("author") or "")
    if author:
        lines.append(f"by {author}")
    lines.append("=" * rule)
    lines.append(f"Source file : {extractor.path.name}")
    pages_txt = f"{len(extractor.pages)}" + (f" (of {total})" if total and total != len(extractor.pages) else "")
    lines.append(f"Pages       : {pages_txt}")
    lines.append(f"Extracted   : {datetime.now():%Y-%m-%d %H:%M} with pdf_book_to_text v{__version__}")
    lines.append("=" * rule)
    if extractor.opts.contents:
        toc = _contents_lines(extractor, rule, elements)
        if toc:
            lines += ["", "", "TABLE OF CONTENTS", "-----------------", ""] + toc + ["", "=" * rule]
    return lines


def _contents_lines(extractor: BookExtractor, rule: int, elements: list[Any]) -> list[str]:
    depth = extractor.opts.contents_depth
    items: list[tuple[int, str, str]] = []
    if extractor.toc_used and extractor.toc_entries:
        labels = {pd.pno: pd.label for pd in extractor.pages}
        for lvl, title, pno in extractor.toc_entries:
            if lvl <= depth and pno in labels:
                items.append((lvl, title, labels[pno]))
    else:
        for el in elements:
            if isinstance(el, Heading) and el.level <= min(depth, 2):
                items.append((el.level, el.text, el.page))

    out: list[str] = []
    for lvl, title, page in items:
        ind = "    " * max(0, lvl - 1)
        left = f"{ind}{title}"
        if page:
            room = rule - len(left) - len(page) - 2
            if room >= 3:
                out.append(f"{left} {'.' * room} {page}")
            else:
                out.append(f"{left}  {page}")
        else:
            out.append(left)
    return out


def _md_escape(text: str) -> str:
    if re.match(r"^(?:#{1,6}\s|[-+*]\s|\d{1,9}[.)]\s|>|=+\s*$|-{3,}\s*$|```|~~~|\|)", text):
        text = "\\" + text
    return re.sub(r"<(?=[A-Za-z/!?])", r"\\<", text)


def _wrap_text(text: str, width: int, init: str = "", sub: str = "") -> list[str]:
    if not width:
        return [init + text]
    return (
        textwrap.wrap(
            text,
            width,
            initial_indent=init,
            subsequent_indent=sub,
            break_long_words=False,
            break_on_hyphens=False,
        )
        or [init]
    )


def render_txt(extractor: BookExtractor, elements: list[Any], include_header: bool = True) -> str:
    """Render AST elements as cleanly wrapped Plain Text (.txt)."""
    opts = extractor.opts
    width = opts.wrap_width
    rule = width or 80
    mode = opts.footnotes
    note_map = _build_note_map(extractor, elements)
    inlined_keys: set[tuple[int, str]] = set()
    sec_notes: list[Note] = []
    out: list[str] = []

    def blank(n: int) -> None:
        if not out:
            return
        k = 0
        for s in reversed(out):
            if s == "":
                k += 1
            else:
                break
        out.extend([""] * max(0, n - k))

    def flush_notes() -> None:
        nonlocal sec_notes
        unseen = [n for n in sec_notes if mode == "section" or (n.pno, n.mark) not in inlined_keys]
        sec_notes = []
        if not unseen or mode == "drop":
            return
        blank(1)
        out.append("Notes:")
        for note in unseen:
            ftxt, _ = _format_refs(note.text, "txt", "section", note_map)
            init = f"  [{note.mark}] "
            out.extend(_wrap_text(ftxt, width, init, " " * len(init)))
        blank(1)

    if include_header:
        out.extend(_header_block(extractor, rule, elements))

    prev: Any = None
    for el in elements:
        if isinstance(el, Heading):
            if el.level <= 2:
                flush_notes()
            lines = _wrap_text(el.text, width)
            if el.level == 1:
                blank(3)
                out.extend(["=" * rule] + lines + ["=" * rule])
            elif el.level == 2:
                blank(2)
                out.extend(lines + ["-" * min(rule, max(len(x) for x in lines))])
            elif el.level == 3:
                blank(2)
                out.extend(lines + ["~" * min(rule, max(len(x) for x in lines))])
            else:
                blank(1)
                out.extend(_wrap_text(el.text, width, ">> ", "   "))
            blank(1)
        elif isinstance(el, Para):
            raw = extractor._join_para(el.lines)
            text, refs = _format_refs(raw, "txt", mode, note_map)
            for pno, mk, _ft in refs:
                inlined_keys.add((pno, mk))
            if not text:
                continue
            if el.marker:
                if not (isinstance(prev, Para) and prev.marker):
                    blank(1)
                pad = "    " * el.list_level
                init = f"{pad}{el.marker} "
                out.extend(_wrap_text(text, width, init, " " * len(init)))
            else:
                blank(1)
                out.extend(_wrap_text(text, width))
                blank(1)
        elif isinstance(el, Code):
            body = _code_body(extractor, el)
            if not any(b.strip() for b in body):
                continue
            blank(1)
            if opts.code_style == "fence":
                fence = "~~~~" if any(line.lstrip().startswith("```") for line in body) else "```"
                out.extend([fence + el.lang] + body + [fence])
            elif opts.code_style == "indent":
                out.extend([("    " + line) if line else "" for line in body])
            else:
                out.extend(body)
            blank(1)
        elif isinstance(el, Pre):
            blank(1)
            if el.kind == "figure":
                out.append(f"[Figure: {el.caption}]" if el.caption else "[Figure]")
            elif el.kind == "table":
                if el.caption:
                    out.append(el.caption)
                out.extend(line.rstrip() for line in el.lines)
            elif el.kind == "callout":
                tag = el.callout_kind or "NOTE"
                out.append(f"[{tag}]")
                body_txt = clean_ws(" ".join(el.lines))
                out.extend(_wrap_text(body_txt, width, "  ", "  "))
            else:
                out.extend(line.rstrip() for line in el.lines)
            blank(1)
        elif isinstance(el, Note):
            if mode != "drop":
                sec_notes.append(el)
        elif isinstance(el, PageMark):
            blank(1)
            out.append(f"[Page {el.label}]")
            blank(1)
        prev = el

    flush_notes()
    text = "\n".join(line.rstrip() for line in out)
    text = re.sub(r"\n{5,}", "\n\n\n\n", text)
    return text.strip("\n") + "\n"


def render_md(extractor: BookExtractor, elements: list[Any], include_header: bool = True) -> str:
    """Render AST elements as GitHub-Flavored Markdown (.md)."""
    opts = extractor.opts
    width = opts.wrap_width
    mode = opts.footnotes
    note_map = _build_note_map(extractor, elements)
    inlined_keys: set[tuple[int, str]] = set()
    sec_notes: list[Note] = []
    out: list[str] = []
    slugs: dict[str, int] = {}
    offset = 1 if include_header else 0

    def blank() -> None:
        if out and out[-1] != "":
            out.append("")

    def flush_notes() -> None:
        nonlocal sec_notes
        unseen = [n for n in sec_notes if mode == "section" or (n.pno, n.mark) not in inlined_keys]
        sec_notes = []
        if not unseen or mode == "drop":
            return
        blank()
        for note in unseen:
            ftxt, _ = _format_refs(note.text, "md", "section", note_map)
            out.append(f"[^{_fn_id(note.pno, note.mark)}]: {ftxt}")
        out.append("")

    if include_header:
        title = extractor.book_title()
        gh_slug(title, slugs)
        meta = extractor.metadata or {}
        author = repair_metadata(meta.get("author") or "")
        out.extend([f"# {_md_escape(title)}", ""])
        if author:
            out.append(f"**Author:** {_md_escape(author)}  ")
        out.extend(
            [
                f"**Source:** `{extractor.path.name}` · **Pages:** {len(extractor.pages)} · "
                f"**Extracted:** {datetime.now():%Y-%m-%d} with pdf_book_to_text v{__version__}",
                "",
            ]
        )
        heads = [e for e in elements if isinstance(e, Heading)]
        if opts.contents and heads:
            out.extend(["## Table of Contents", ""])
            gh_slug("Table of Contents", slugs)
            pre = dict(slugs)
            for h in heads:
                slug = gh_slug(h.text, pre)
                if h.level <= opts.contents_depth:
                    safe_title = h.text.replace("[", "(").replace("]", ")")
                    out.append(f"{'  ' * max(0, h.level - 1)}- [{safe_title}](#{slug})")
            out.extend(["", "---", ""])

    prev: Any = None
    for el in elements:
        if isinstance(el, Heading):
            if el.level <= 2:
                flush_notes()
            blank()
            gh_slug(el.text, slugs)
            out.extend(["#" * min(6, el.level + offset) + " " + _md_escape(el.text), ""])
        elif isinstance(el, Para):
            raw = extractor._join_para(el.lines)
            text, refs = _format_refs(raw, "md", mode, note_map)
            if not text:
                continue
            if el.marker:
                if not (isinstance(prev, Para) and prev.marker):
                    blank()
                mk = el.marker if re.fullmatch(r"\d{1,9}[.)]", el.marker) else "-"
                rest = text if mk != "-" or el.marker in ("•", "-", "*") else f"{el.marker} {text}"
                init = "    " * el.list_level + mk + " "
                body_txt = _md_escape(rest) if mk == "-" else rest
                out.extend(_wrap_text(body_txt, width, init, " " * len(init)))
            else:
                blank()
                out.extend(_wrap_text(_md_escape(text), width))
                out.append("")
            if mode == "inline" and refs:
                blank()
                for pno, mk, ftxt in refs:
                    inlined_keys.add((pno, mk))
                    if ftxt:
                        out.append(f"[^{_fn_id(pno, mk)}]: {ftxt}")
                out.append("")
        elif isinstance(el, Code):
            body = _code_body(extractor, el)
            if not any(b.strip() for b in body):
                continue
            fence = "~~~~" if any(line.lstrip().startswith("```") for line in body) else "```"
            blank()
            out.extend([fence + el.lang] + body + [fence, ""])
        elif isinstance(el, Pre):
            blank()
            if el.kind == "table" and el.rows:
                if el.caption:
                    out.extend([f"*{_md_escape(el.caption)}*", ""])
                out.extend(render_md_table(el.rows))
            elif el.kind == "figure":
                out.append(f"*[Figure: {_md_escape(el.caption)}]*" if el.caption else "*[Figure]*")
            elif el.kind == "callout":
                alert = _MD_ALERT_MAP.get((el.callout_kind or "NOTE").upper(), "NOTE")
                out.append(f"> [!{alert}]")
                for line in _wrap_text(clean_ws(" ".join(el.lines)), width):
                    out.append(f"> {_md_escape(line)}")
            else:
                out.extend(["```text"] + [line.rstrip() for line in el.lines] + ["```"])
            out.append("")
        elif isinstance(el, Note):
            if mode != "drop":
                sec_notes.append(el)
        elif isinstance(el, PageMark):
            blank()
            out.extend([f"<!-- Page {el.label} -->", ""])
        prev = el

    flush_notes()
    text = "\n".join(line.rstrip() for line in out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip("\n") + "\n"


def _est_tokens(text: str) -> int:
    words = len(text.split())
    return max(1, int(round(words * 1.33))) if words else 0


def _pack_rag_chunks(
    records: list[dict[str, Any]],
    chunk_tokens: int,
    chunk_overlap: int,
    title: str,
    source: str,
) -> list[dict[str, Any]]:
    """Bundle JSONL element records into hierarchy-aware semantic RAG chunks."""
    chunks: list[dict[str, Any]] = []
    buf_texts: list[str] = []
    buf_recs: list[dict[str, Any]] = []
    buf_tokens = 0
    seq = 0
    cur_section: tuple[str, ...] = ()

    def emit_chunk(overlap_carry: bool = True) -> list[str]:
        nonlocal seq, buf_texts, buf_recs, buf_tokens
        if not buf_recs:
            return []
        joined = "\n\n".join(buf_texts).strip()
        if not joined:
            buf_texts, buf_recs, buf_tokens = [], [], 0
            return []
        seq += 1
        first, last = buf_recs[0], buf_recs[-1]
        chunks.append(
            {
                "type": "chunk",
                "id": seq,
                "text": joined,
                "token_estimate": _est_tokens(joined),
                "chapter": last.get("chapter", ""),
                "section_path": last.get("section_path", []),
                "page": first.get("page", ""),
                "page_start": first.get("page", ""),
                "page_end": last.get("page", ""),
                "page_index_start": first.get("page_index", 1),
                "page_index_end": last.get("page_index", 1),
                "element_types": sorted({str(r.get("type", "")) for r in buf_recs}),
                "book": title,
                "source": source,
            }
        )
        carry: list[str] = []
        if overlap_carry and chunk_overlap > 0:
            words = joined.split()
            if len(words) > 0:
                carry = [" ".join(words[-chunk_overlap:])]
        buf_texts, buf_recs, buf_tokens = [], [], 0
        return carry

    for rec in records:
        sec_key = tuple(rec.get("section_path", []))
        rtype = rec.get("type")
        rtext = str(rec.get("text", "")).strip()
        if not rtext:
            continue
        if rtype == "heading":
            # Flush at major section changes so chunks never blur across chapters/sections
            if sec_key != cur_section and buf_recs:
                emit_chunk(overlap_carry=False)
            cur_section = sec_key
            heading_line = f"{'#' * int(rec.get('level', 1))} {rtext}"
            buf_texts.append(heading_line)
            buf_recs.append(rec)
            buf_tokens += _est_tokens(heading_line)
            continue

        cur_section = sec_key
        piece = rtext
        if rtype == "code":
            lang = rec.get("language", "")
            piece = f"```{lang}\n{rtext}\n```"
        elif rtype == "callout":
            ckind = rec.get("callout_kind", "NOTE")
            piece = f"[{ckind}] {rtext}"
        elif rtype == "footnote":
            piece = f"[{rec.get('mark', '')}: {rtext}]"

        ptok = _est_tokens(piece)
        if buf_recs and buf_tokens + ptok > chunk_tokens:
            carry = emit_chunk(overlap_carry=True)
            if carry:
                buf_texts.extend(carry)
                buf_tokens += _est_tokens(carry[0])

        # Split an oversized single prose element cleanly across windows
        if ptok > chunk_tokens and rtype in ("paragraph", "list_item", "callout"):
            words = piece.split()
            step_words = max(20, int(chunk_tokens / 1.33))
            ov_words = max(0, min(step_words // 2, chunk_overlap))
            start = 0
            while start < len(words):
                part = " ".join(words[start : start + step_words])
                buf_texts.append(part)
                buf_recs.append(rec)
                buf_tokens += _est_tokens(part)
                start += max(1, step_words - ov_words)
                if start < len(words):
                    emit_chunk(overlap_carry=False)
        else:
            buf_texts.append(piece)
            buf_recs.append(rec)
            buf_tokens += ptok

    emit_chunk(overlap_carry=False)
    return chunks


def render_jsonl(extractor: BookExtractor, elements: list[Any], include_header: bool = True) -> str:
    """Render AST elements (or hierarchy-aware RAG chunks) as UTF-8 JSON Lines."""
    opts = extractor.opts
    mode = opts.footnotes
    title = extractor.book_title()
    note_map = _build_note_map(extractor, elements)
    header_rows: list[dict[str, Any]] = []
    if include_header:
        meta = extractor.metadata or {}
        header_rows.append(
            {
                "type": "document",
                "title": title,
                "author": repair_metadata(meta.get("author") or ""),
                "source": extractor.path.name,
                "pages": len(extractor.pages),
                "extractor": f"pdf_book_to_text {__version__}",
            }
        )

    path: list[str] = []
    records: list[dict[str, Any]] = []
    seq = 0
    for el in elements:
        rec: dict[str, Any] | None = None
        if isinstance(el, Heading):
            path = path[: max(0, el.level - 1)]
            path.extend([""] * max(0, el.level - 1 - len(path)))
            path.append(el.text)
            rec = {"type": "heading", "level": el.level, "text": el.text}
        elif isinstance(el, Para):
            text, _ = _format_refs(extractor._join_para(el.lines), "jsonl", mode, note_map)
            if not text:
                continue
            rec = {"type": "list_item" if el.marker else "paragraph", "text": text}
            if el.marker:
                rec["marker"] = el.marker
                rec["list_level"] = el.list_level
        elif isinstance(el, Code):
            body = _code_body(extractor, el)
            if not any(b.strip() for b in body):
                continue
            rec = {"type": "code", "language": el.lang, "text": "\n".join(body)}
        elif isinstance(el, Pre):
            if el.kind == "callout":
                rec = {
                    "type": "callout",
                    "callout_kind": el.callout_kind or "NOTE",
                    "text": clean_ws(" ".join(el.lines)),
                }
            elif el.kind == "figure":
                rec = {"type": "figure", "text": el.caption or "[Figure]"}
                if el.caption:
                    rec["caption"] = el.caption
            elif el.kind == "table":
                rec = {"type": "table", "text": "\n".join(line.rstrip() for line in el.lines)}
                if el.caption:
                    rec["caption"] = el.caption
                if el.rows:
                    rec["rows"] = [[clean_ws(str(c)) if c is not None else "" for c in r] for r in el.rows]
            else:
                rec = {"type": "preformatted", "text": "\n".join(line.rstrip() for line in el.lines)}
        elif isinstance(el, Note) and mode != "drop":
            ftxt, _ = _format_refs(el.text, "jsonl", "section", note_map)
            rec = {"type": "footnote", "mark": el.mark, "text": ftxt}

        if rec is None:
            continue
        seq += 1
        crumbs = [p for p in path if p]
        rec.update(
            {
                "id": seq,
                "page": el.page,
                "page_index": el.pno + 1,
                "chapter": crumbs[0] if crumbs else "",
                "section_path": crumbs,
                "book": title,
                "source": extractor.path.name,
            }
        )
        records.append(rec)

    if opts.chunk_tokens > 0:
        out_rows = header_rows + _pack_rag_chunks(
            records,
            chunk_tokens=opts.chunk_tokens,
            chunk_overlap=opts.chunk_overlap,
            title=title,
            source=extractor.path.name,
        )
    else:
        out_rows = header_rows + records
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out_rows)
