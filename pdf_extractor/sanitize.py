"""Text sanitization, metadata repair, BiDi direction helpers, and slugification."""
from __future__ import annotations

import html
import re
import unicodedata

from pdf_extractor.constants import CTRL_RE, RTL_RE


def clean_text(s: str) -> str:
    """Strip NUL and unprintable control bytes while preserving tabs, newlines, and box drawing."""
    if not s:
        return ""
    return CTRL_RE.sub("", s)


# Backwards-compatible v1 symbol. Never rewrites printable text, box drawing, or HTML entities.
sanitize_text = clean_text


def clean_ws(s: str) -> str:
    """Collapse internal whitespace runs and trim ends."""
    return re.sub(r"\s+", " ", s).strip()


def repair_metadata(s: object) -> str:
    """Repair PDF metadata/outline strings containing UTF-16LE byte swaps or HTML entities.

    Must only be used on PDF document metadata and bookmarks, never on body code/text.
    """
    if not s:
        return ""
    text = str(s)
    swapped = [(ord(c) & 0xFF) == 0 and 0x20 <= (ord(c) >> 8) <= 0x7E for c in text]
    run = best = 0
    for is_sw in swapped:
        run = run + 1 if is_sw else 0
        if run > best:
            best = run
    if "\x00" in text or best >= 3:
        text = "".join(chr(ord(c) >> 8) if is_sw else c for c, is_sw in zip(text, swapped))
    if "&" in text and ";" in text:
        text = html.unescape(text)
    return clean_ws(clean_text(text))


def count_rtl_chars(s: str) -> int:
    """Count Right-to-Left script characters in a string."""
    return len(RTL_RE.findall(s))


def is_rtl_text(s: str) -> bool:
    """Return True if string contains any RTL script character."""
    return bool(RTL_RE.search(s))


def text_direction(s: str) -> str:
    """Return 'rtl' if strong RTL letters outnumber strong LTR letters (digits excluded)."""
    if not RTL_RE.search(s):
        return "ltr"
    rtl_count = 0
    ltr_count = 0
    for ch in s:
        bd = unicodedata.bidirectional(ch)
        if bd in ("R", "AL"):
            rtl_count += 1
        elif bd == "L":
            ltr_count += 1
    return "rtl" if rtl_count > ltr_count else "ltr"


def is_predominantly_rtl(s: str) -> bool:
    """Return True if strong RTL letters outnumber strong LTR letters."""
    return text_direction(s) == "rtl"


def norm_key(s: str) -> str:
    """Script-independent comparison key supporting Latin, Arabic, Cyrillic, CJK, etc."""
    s_clean = unicodedata.normalize("NFKC", s).casefold().replace("\u0640", "")
    return re.sub(r"[\W_]+", "", s_clean, flags=re.UNICODE)


def gh_slug(text: str, used: dict[str, int]) -> str:
    """Generate a unique GitHub-style heading anchor slug."""
    base = re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")
    count = used.get(base, 0)
    used[base] = count + 1
    return f"{base}-{count}" if count else base


def safe_filename(s: str, maxlen: int = 70) -> str:
    """Sanitize an arbitrary title into a clean cross-platform filename component."""
    cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f\ufffd]+", " ", clean_text(s))
    return clean_ws(cleaned)[:maxlen].rstrip(". ") or "untitled"


def slugify(s: str, maxlen: int = 56) -> str:
    """Convert heading titles into readable underscore-separated chapter file slugs."""
    s_norm = unicodedata.normalize("NFKC", clean_text(s)).casefold().replace("\u0640", "")
    s_norm = re.sub(r"[^\w\s-]", " ", s_norm, flags=re.UNICODE)
    s_norm = re.sub(r"[\s\-_]+", "_", s_norm).strip("_")
    return s_norm[:maxlen].rstrip("_") or "chapter"
