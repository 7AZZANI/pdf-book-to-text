"""Shared constants, font regexes, translation tables, and code/language patterns."""
from __future__ import annotations

import re

try:
    import pymupdf  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover
    import fitz as pymupdf

__version__ = "2.1.0"
__author__ = "AZZANI"

TEXT_FLAGS = getattr(pymupdf, "TEXT_PRESERVE_WHITESPACE", 2) | getattr(pymupdf, "TEXT_MEDIABOX_CLIP", 64)
FORMAT_EXT = {"txt": ".txt", "md": ".md", "jsonl": ".jsonl"}

# Right-to-left script ranges (Hebrew, Arabic, Syriac, Thaana, NKo, presentation forms)
RTL_RE = re.compile(r"[\u0590-\u08ff\ufb1d-\ufdff\ufe70-\ufefc]")
# Remove NUL and unprintable control chars while keeping \t, \n, \r cleanly
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

MONO_FONT_RE = re.compile(
    r"mono|courier|consol|menlo|monaco|inconsolata|fixed|typewriter|(?<!en)code|"
    r"(?:^|[^a-z])(?:cm|sf|lm|ec|tc|tx|rm)?tt\d*(?:[^a-z]|$)|cmtt|sftt|lmtt|txtt|pcrr|ucrr|"
    r"andale|hack|letter ?gothic|prestige|ocr-?[ab]|nimbus ?mon|lucida ?console|anonymous|"
    r"terminus|profont|iosevka|cascadia|everson|fira ?(?:code|mono)|jetbrains|ubuntu ?mono|"
    r"source ?code|ptmono|pt ?mono|sometype|plex ?mono|go ?mono|input ?mono|"
    r"mplus|1mn|dejavu ?sans ?mono|liberation ?mono|noto ?(?:sans ?)?mono|pragmata|sarasa|"
    r"commit ?mono|intel ?one ?mono|victor ?mono|envy ?code|agave|dank ?mono|droid ?sans ?mono",
    re.I,
)
BOLD_FONT_RE = re.compile(r"bold|black|heavy|semibold|demi|extrab", re.I)
SYMBOL_FONT_RE = re.compile(r"symbol|wingding|webding|dingbat|zapf", re.I)
MATH_FONT_RE = re.compile(r"^(?:cmsy|cmmi|cmex|msam|msbm|euex|eurm)|cambria ?math|latinmodern-math|stix.*math", re.I)
TYPE3_FONT_RE = re.compile(r"^type3\b", re.I)

WORD_BULLETS = {"o", "§", "Ø", "ü", "\uf0a7", "\uf0b7", "\uf0d8", "\uf0fc"}

# Character normalization for extracted PDF glyphs (restores ligatures, spaces, bullets; preserves U+2500 box drawing)
CHAR_MAP: dict[str, str] = {
    "\x00": "",
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\ufb05": "st",
    "\ufb06": "st",
    "\t": " ",
    "\r": "",
    "\n": "",
}
for _c in "\u00a0\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000":
    CHAR_MAP[_c] = " "
for _c in "\u200b\u200d\u2060\ufeff\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069":
    CHAR_MAP[_c] = ""
for _c in "\uf0b7\uf0a7\uf0d8\uf076\uf0fc\uf0a8\uf06e\uf0e0\uf075\uf0d7\uf09f\uf0a2\u2219\u25cf":
    CHAR_MAP[_c] = "•"
CHAR_TRANS = str.maketrans(CHAR_MAP)

# Typographic quote/dash normalization for code listings
CODE_TRANS = str.maketrans({
    "\u2018": "'",
    "\u2019": "'",
    "\u201a": "'",
    "\u201b": "'",
    "\u2032": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u201e": '"',
    "\u201f": '"',
    "\u2033": '"',
    "\u2212": "-",
    "\u2010": "-",
    "\u2011": "-",
    "\u00ad": "-",
})

# Internal footnote placeholder token embedded in text: \ue000<pno>:<mark>\ue001
FN_TOKEN_RE = re.compile("\ue000(\\d+):([^\ue001]+)\ue001")
_FN_TAIL = "(?:\ue000[^\ue001]*\ue001)*"

TERMINAL_RE = re.compile(r"[.!?:;\u061f\u06d4\u2026][\"'\u201d\u2019)\]\u00bb]*" + _FN_TAIL + "$")
STRONG_BULLET_RE = re.compile(r"^([•○◦▪▫■□►▸‣⁃✓✔➢➤❖◆◇])\s*(?=\S)")
WEAK_BULLET_RE = re.compile(r"^([-–—*])\s+(?=\S)")
NUM_LIST_RE = re.compile(
    r"^(\(?\d{1,3}[.)]|\(?[a-z][.)]|\(?(?:i|ii|iii|iv|v|vi|vii|viii|ix|x)[.)]|\(?[A-Z]\)|\(?[٠-٩]{1,3}[.)])\s+(?=\S)"
)
PAGE_NUM_RE = re.compile(
    r"^[-–—\s]*(?:page\s*|صفحة\s*)?(?:\d{1,4}|[ivxlcdm]{1,7}|[٠-٩]{1,4})(?:\s*(?:/|of|من)\s*\d{1,4})?[-–—\s]*$",
    re.I,
)
LABEL_RE = re.compile(
    r"^(?:chapter|part|appendix|lesson|module|unit|section|book|day|hour|step|فصل|باب|قسم)?\s*"
    r"(?:\d{1,3}|[ivxlcdm]{1,6}|[a-z])[.:]?$",
    re.I,
)
CHAPTER_RE = re.compile(
    r"^(?:chapter|part|appendix)\s+(?:\d{1,3}|[ivxlcdm]{1,6}|[a-z]|one|two|three|four|"
    r"five|six|seven|eight|nine|ten)\b",
    re.I,
)
LEADER_RE = re.compile(r"(?:\s*\.){4,}\s*")
WORD_RE = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)*")
LETTERS2_RE = re.compile(r"[^\W\d_]{2}")
FN_MARK_RE = re.compile(r"^(\d{1,3}|[*†‡§¶]{1,3})[.):]?\s+(?=\S)")
LISTING_NUM_RE = re.compile(r"^(\s*)(\d{1,4})([:.|]?)(?=\s|$)")
CAPTION_RE = re.compile(r"^(?:Table|Figure|Fig\.?|Listing|Algorithm|Example)\s+\d+(?:[.\-]\d+)*\b", re.I)
CALLOUT_KIND_RE = re.compile(
    r"^(?:NOTE|TIP|WARNING|CAUTION|IMPORTANT|DANGER|HINT|INFO|KEY TAKEAWAY)\b[:\s]*",
    re.I,
)

# Prose vs source-code detection patterns (for books setting code in body/proportional fonts)
PROSE_WORD_RE = re.compile(r"^[A-Za-z\u2019'][A-Za-z\u2019',.;:!?-]*$")
CODE_LINE_PATTERNS: list[tuple[re.Pattern[str], int]] = [
    (
        re.compile(
            r"^\s*(?:import\s+[\w.]+(?:\s*,\s*[\w.]+)*(?:\s+as\s+\w+)?|"
            r"from\s+[\w.]+\s+import\s+(?:\*|[\w, ()]+))\s*(?:#.*)?$"
        ),
        3,
    ),
    (re.compile(r"^\s*(?:def|class)\s+\w+\s*[(:]"), 3),
    (re.compile(r"#include\s*[<\"]|^\s*(?:public|private|protected)\s+(?:static\s+)?\w+"), 3),
    (re.compile(r"^\s*(?:async\s+)?(?:for|while|if|elif|else|try|except|finally|with)\b[^.]*:\s*(?:#.*)?$"), 2),
    (re.compile(r"^\s*(?:return|yield|raise|break|continue|pass)\b(?!\s+(?:the|a|an|to|of|is|it)\b)"), 2),
    (re.compile(r"^\s*[A-Za-z_][\w.]*(?:\[[^\]]*\])*\s*(?:[-+*/%|&^]|//|\*\*)?=(?!=)\s*\S"), 2),
    (re.compile(r"^\s*[A-Za-z_][\w.]*\s*\(.*\)\s*;?\s*(?:#.*)?$"), 2),
    (re.compile(r"^\s*print\s*(?:\(|[\"']|\w+\s*(?:,|%|$))"), 2),
    (re.compile(r"^\s*#\s*\S"), 2),
    (re.compile(r"^\s*(?:\$|>>>|\.\.\.|C:\\>|PS>)\s+\S"), 2),
    (re.compile(r"[{};]\s*$"), 1),
    (re.compile(r"^\s*[}\])]"), 1),
]

# Language identification rules: carefully avoid weak ':' bias on YAML/JSON
_RAW_LANG_RULES: dict[str, list[str]] = {
    "python": [
        r"^\s*def\s+\w+\s*\(.*\)\s*(?:->.*)?:",
        r"^\s*class\s+\w+.*:\s*$",
        r"^\s*(?:import\s+[A-Za-z_][\w.]*(?:\s*,\s*[A-Za-z_][\w.]*)*(?:\s+as\s+\w+)?|from\s+[\w.]+\s+import\s+[^;]+)\s*(?:#.*)?$",
        r"\b(?:elif|except|finally)\b.*:",
        r"\bself\.",
        r"\b__name__\b|\b__init__\b",
        r"\b(?:True|False|None)\b",
        r"\b[fbr]r?['\"][^'\"]*['\"]",
        r"\b(?:enumerate|isinstance|range|sorted|zip|getattr|hasattr)\s*\(",
        r"\bprint\s*\(|^\s*print\s+[\"'\w]",
        r"^\s*>>>\s+",
        r"^Traceback \(most recent call last\):|^\s*<class '[^']+'>",
    ],
    "bash": [
        r"^#!/(?:usr/)?bin/(?:env\s+)?(?:ba|z|da|a)?sh\b",
        r"^\s*\$\s+(?:[a-zA-Z_./])",
        r"^\s*(?:sudo|apt(?:-get)?|pip3?|uv|poetry|docker|kubectl|systemctl|chmod|chown|mkdir|git|curl|wget|nmap|tar)\b",
        r"\bset\s+-[euox]+\b",
        r"\|\s*(?:grep|awk|sed|sort|head|tail|wc|xargs)\b",
        r"^\s*if\s+\[.*?\];\s*then\b|^\s*fi\s*$",
    ],
    "javascript": [
        r"\bfunction\s*\*?\s*\w*\s*\(",
        r"\b(?:const|let|var)\s+\w+\s*=",
        r"=>\s*[{\(]?",
        r"\bconsole\.(?:log|error|warn)\b",
        r"\bdocument\.(?:getElementById|querySelector|createElement)\b",
        r"\b(?:module\.exports|require\s*\(['\"])",
    ],
    "c": [
        r"#include\s*<[a-zA-Z0-9_/]+\.h>",
        r"\bint\s+main\s*\(",
        r"\b(?:printf|scanf|fprintf|malloc|calloc|free|sizeof)\s*\(",
        r"\bstruct\s+\w+\s*\{",
        r"\btypedef\s+(?:struct|enum)\b",
    ],
    "cpp": [
        r"#include\s*<(?:iostream|vector|string|map|memory|algorithm)>",
        r"\bstd::\w+",
        r"\b(?:cout|cerr)\s*<<|\bcin\s*>>",
        r"\btemplate\s*<",
        r"\bnamespace\s+\w+",
    ],
    "java": [
        r"\bpublic\s+(?:static\s+)?(?:class|interface|void)\s+\w+",
        r"\bSystem\.out\.print(?:ln|f)?\s*\(",
        r"\bimport\s+java\.",
        r"\bpackage\s+[\w.]+;",
    ],
    "csharp": [
        r"\busing\s+System(?:\.\w+)*;",
        r"\bConsole\.Write(?:Line)?\s*\(",
        r"\bnamespace\s+[\w.]+\s*\{",
    ],
    "go": [
        r"^\s*package\s+\w+\s*$",
        r"\bfunc\s+(?:\([^)]*\)\s*)?\w+\s*\(",
        r"\bfmt\.(?:Print|Sprintf|Errorf)",
        r":=",
    ],
    "rust": [
        r"\bfn\s+\w+\s*\(",
        r"\blet\s+mut\s+\w+",
        r"\bprintln!\s*\(",
        r"\bimpl\b.*\bfor\b|\bpub\s+(?:fn|struct|enum)\b",
    ],
    "php": [
        r"<\?php",
        r"\$\w+\s*=.*;",
        r"\becho\b.*;",
    ],
    "ruby": [
        r"^\s*def\s+\w+(?:\([^)]*\))?\s*$",
        r"^\s*end\s*$",
        r"\bputs\s+|\.each\s+do\b",
    ],
    "sql": [
        r"(?i)^\s*SELECT\b.+\bFROM\b",
        r"(?i)^\s*(?:INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM)\b",
        r"(?i)^\s*CREATE\s+(?:TABLE|INDEX|VIEW)\b",
        r"(?i)\b(?:WHERE|GROUP\s+BY|ORDER\s+BY|INNER\s+JOIN|LEFT\s+JOIN)\b",
    ],
    "html": [
        r"<!DOCTYPE\s+html>",
        r"<(?:html|head|body|div|span|form|input|script|table|tr|td|ul|li)\b[^>]*>",
        r"</(?:html|head|body|div|span|form|script|table|p|a|ul|li)>",
    ],
    "yaml": [
        r"^\s*[A-Za-z_][\w.-]*\s*:\s*$",
        r"^\s*[A-Za-z_][\w.-]*\s*:\s+(?:[^\s{}\[\]=]+.*|true|false|null|\d+(?:\.\d+)?)\s*(?:#.*)?$",
        r"^\s*-\s+[A-Za-z_][\w.-]*\s*:",
    ],
    "json": [
        r"^\s*[{\[]\s*$",
        r'^\s*"[^"\n]+"\s*:\s*(?:"|\[|\{|true|false|null|-?\d)',
    ],
    "powershell": [
        r"\b(?:Get|Set|New|Remove|Invoke|Write|Test|Start)-[A-Z]\w+",
        r"\$env:[A-Za-z_]\w*",
    ],
}

LANG_RULES: dict[str, list[re.Pattern[str]]] = {
    lang: [re.compile(pat, re.M) for pat in pats] for lang, pats in _RAW_LANG_RULES.items()
}
