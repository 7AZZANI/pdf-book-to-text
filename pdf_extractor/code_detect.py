"""Code-likeness scoring, language identification, and listing line-number stripping."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Union

from pdf_extractor.constants import (
    CODE_LINE_PATTERNS,
    LANG_RULES,
    LISTING_NUM_RE,
    PROSE_WORD_RE,
)

if TYPE_CHECKING:
    from pdf_extractor.models import Code


def code_score(text: str) -> int:
    """Return heuristic source-code score for one line (0 = ordinary prose)."""
    t = text.rstrip()
    if not t.strip():
        return 0
    score = 0
    for rx, weight in CODE_LINE_PATTERNS:
        if rx.search(t):
            if score < 2:
                score = max(score, weight)
            else:
                score += 1 if weight >= 2 else 0
    if score >= 3:
        return score

    words = t.split()
    if len(words) >= 5 and not re.search(r"[()=\[\]{}<>_#\"]", t):
        prose = sum(1 for w in words if PROSE_WORD_RE.match(w))
        if prose >= 0.8 * len(words):
            return 0
    if len(words) >= 12 and not t.endswith((":", "{", ";", ")")):
        return 0
    return score


def guess_language(text_or_lines: Union[str, list[str]]) -> str:
    """Identify the programming/config language of a code block, or '' if ambiguous."""
    if isinstance(text_or_lines, str):
        lines = text_or_lines.splitlines()
    else:
        lines = list(text_or_lines)

    text_lines = [ln for ln in lines if ln.strip()]
    if not text_lines:
        return ""

    scores: dict[str, int] = {}
    for lang, rules in LANG_RULES.items():
        total = 0
        for rx in rules:
            hits = sum(1 for ln in text_lines if rx.search(ln))
            if hits:
                total += min(3, hits)
        scores[lang] = total

    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    best_lang, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0

    if best_score >= 2 and best_score > second_score:
        return best_lang
    return ""


# Backwards-compatible name used by tests and public API
guess_code_language = guess_language


def strip_listing_numbers(lines: list[str]) -> list[str]:
    """Strip sequential printed listing line numbers (e.g. '1  import os') preserving indentation."""
    if len(lines) < 2:
        return list(lines)
    non_empty = [(idx, ln) for idx, ln in enumerate(lines) if ln.strip()]
    if len(non_empty) < 2:
        return list(lines)

    matches: list[tuple[int, int, int, str]] = []
    for idx, ln in non_empty:
        m = LISTING_NUM_RE.match(ln)
        if m:
            prefix_w = len(m.group(1)) + len(m.group(2)) + len(m.group(3))
            rest_raw = ln[m.end() :]
            sep_len = len(rest_raw) - len(rest_raw.lstrip(" "))
            col_w = prefix_w + (sep_len if rest_raw.strip() else len(rest_raw))
            matches.append((idx, int(m.group(2)), col_w, rest_raw.lstrip(" ")))

    if len(matches) < 0.75 * len(non_empty):
        return list(lines)

    nums = [m[1] for m in matches]
    # Require true consecutive sequential steps (n, n+1, ...), never arbitrary increasing values like [1, 5, 10]
    steps = sum(1 for a, b in zip(nums, nums[1:]) if b == a + 1)
    if steps < max(1, int(0.8 * (len(nums) - 1))) or steps != len(nums) - 1 and len(nums) <= 3:
        return list(lines)
    # Guard against two-line number pairs with large jump or non-unit step
    if any(b - a != 1 for a, b in zip(nums, nums[1:])) and steps < 0.8 * (len(nums) - 1):
        return list(lines)

    # Minimum column width among lines that actually contain code after the number
    code_cols = [col_w for _idx, _num, col_w, rest in matches if rest]
    base_col = min(code_cols) if code_cols else min(col_w for _idx, _num, col_w, _rest in matches)

    new_lines = list(lines)
    for idx, _num, col_w, rest in matches:
        if not rest:
            new_lines[idx] = ""
        else:
            extra_indent = " " * max(0, col_w - base_col)
            new_lines[idx] = extra_indent + rest
    return new_lines


def strip_code_listing_numbers(code: Code) -> bool:
    """In-place wrapper stripping sequential listing numbers on a Code element."""
    if len(code.lines) < 2:
        return False
    raw = [(" " * row[0] + row[1]) for row in code.lines]
    non_empty = [s for s in raw if s.strip()]
    if len(non_empty) < 2:
        return False
    stripped = strip_listing_numbers(raw)
    if stripped == raw:
        return False
    new_rows = []
    for row, s in zip(code.lines, stripped):
        content = s.lstrip(" ")
        indent = len(s) - len(content) if content else 0
        rx = row[3] if len(row) > 3 else 0.0
        new_rows.append([indent, content, row[2], rx])
    code.lines = new_rows
    return True
