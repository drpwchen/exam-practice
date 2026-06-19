"""Parser for 考古題 *題目/考題* PDFs (years 109-114).

These PDFs are the pure-question versions (no explanations). They contain:
- correct_letter (one capital letter A-E or full-width Ａ-Ｅ) sitting BEFORE
  the question number, sometimes inline ("E 001."), sometimes on its own
  line ("E\\n001\\n下列..."), sometimes with full-width letter ("Ｂ\\n017\\n...").
- stem text (often duplicated when source had highlights — "下列有關A...\n
  有關A..." — we de-dup the stem prefix).
- options (A)-(E) each starting on a new line.

Format variants observed across years:
- 109年題目.pdf   "E 001. <stem>\\n(A) ... (E) ..."
- 110年題目.pdf   "Ｅ\\n015\\n<stem>\\n(A)..."  (full-width letter, multiline)
- 111年題目.pdf   "E\\n001\\n<stem>\\n(A)..."
- 112年題目.pdf   "B 001\\n<stem>\\n(A)..."  (letter-num inline, stem on next line)
- 113考題.pdf    "E\\n001\\n<stem>..." OR "B 002\\n<stem>..."
- 114考題.pdf    "E 001 <stem>..." OR "B\\n002\\n<stem>..."

A single combined regex handles all six.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF

# ─────────────────────── module-level regexes ───────────────────────

# Maps full-width Ａ-Ｅ to ASCII A-E for normalization.
_FULLWIDTH_TO_ASCII = str.maketrans("ＡＢＣＤＥａｂｃｄｅ", "ABCDEABCDE")

# The question marker. Captures (correct_letter, q_num).
# Tolerates: optional whitespace/newline between letter and num; optional
# period/space after num. Anchored at start-of-line OR after newline.
_Q_MARKER_RE = re.compile(
    r"(?:^|\n)\s*([A-EＡ-Ｅ])\s*\.?\s*\n?\s*(\d{1,3})[\.\s]\s*",
    re.MULTILINE,
)

# Option label. ASCII (A)..(E) only — full-width is rare in option labels and
# would have been observed in the marker counts (it wasn't).
_OPT_RE = re.compile(
    r"\(([A-E])\)\s*(.+?)(?=\n\s*\([A-E]\)|\Z)",
    re.DOTALL,
)

# Lines we strip during pre-processing (PDF page numbers).
# CAUTION: must NOT match question numbers (001-200), so we only strip 1-2 digit
# numeric lines. PDF page numbers in these PDFs are 1-50, so this is safe.
_PAGE_NUM_LINE = re.compile(r"^\s*\d{1,2}\s*$")

# Stem highlight-duplication signature: "X前綴 X前綴" where the same
# 6+ char Chinese segment appears twice with whitespace between. We strip
# the first occurrence in normalize_stem.
_DUP_PREFIX_RE = re.compile(
    r"^([一-鿿A-Za-z0-9，。：（）\(\)\s]{6,30})\s*\n\s*\1",
)


def _strip_page_lines(text: str) -> str:
    """Drop pure-numeric lines (PDF page numbers) so they don't infect the stem."""
    out: list[str] = []
    for ln in text.splitlines():
        if _PAGE_NUM_LINE.match(ln):
            continue
        out.append(ln)
    return "\n".join(out)


def _normalize_stem(text: str) -> str:
    """Collapse internal whitespace, drop highlight-overlay duplication.

    PDF highlight markup causes fitz to extract the highlighted span twice:
    once as part of the original line, once as a separate "highlighted" line.
    Two patterns observed:

    Pattern A (110/111-style):  prev = "下列關於 X 的敘述"
                                curr = "關於 X 的敘述,何者正確？"  (curr is suffix-of-prev + extra)
                                → drop prev; curr is the cleaner version

    Pattern B (other-style):    prev = "...錯誤？"
                                curr = "下列關於X的敘述,何者錯誤？"  (curr is superset)
                                → drop prev (shorter)

    Pattern C: the SHORT line is a substring of the long line → keep long.
    """
    text = text.replace("\r", "")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # First pass: drop / merge highlight-overlay duplications.
    deduped: list[str] = []
    for ln in lines:
        if not deduped:
            deduped.append(ln)
            continue
        prev = deduped[-1]
        # Skip very-short stand-alones (single chars, page nums, etc.).
        # Allow ≥2 chars so "有關" / "關於" islands can be absorbed.
        if len(ln) < 2 or len(prev) < 2:
            deduped.append(ln)
            continue
        # Overlap detection: largest k such that prev[-k:] == ln[:k].
        # An overlap of 2+ chars indicates a likely highlight-fragment split.
        # We require either:
        #   • overlap >= 4 unconditionally, OR
        #   • overlap == prev (i.e. prev is a STRICT prefix of ln) and prev
        #     consists of common opener phrasing ("下列有關", "下列關於" etc.)
        max_overlap = 0
        for k in range(min(len(prev), len(ln)), 0, -1):
            if prev[-k:] == ln[:k]:
                max_overlap = k
                break
        merge = False
        if max_overlap >= 4:
            merge = True
        elif max_overlap >= 2:
            # Smaller overlap: only merge if prev ends with a known
            # "introducer" Chinese word like 有關/關於 — these get duplicated
            # frequently by highlight overlay.
            if any(prev.endswith(kw) for kw in (
                "下列有關", "下列關於", "有關", "關於", "敘述",
                "何者", "下列", "下面", "請問",
            )):
                merge = True
            elif prev in {"下列", "下列何者", "下列敘述", "下面", "請問"}:
                merge = True
        elif max_overlap == 1:
            # 1-char overlap: only merge if the single char is a Chinese
            # char AND prev's prior char is also Chinese (i.e. NOT a sentence
            # boundary). This catches "...復健\n健之..." but not "X\nXY...".
            if (
                "\u4e00" <= prev[-1] <= "\u9fff"
                and len(prev) >= 2
                and "\u4e00" <= prev[-2] <= "\u9fff"
            ):
                merge = True
        elif (
            prev in {"下列", "下列何者", "下面", "請問"}
            or prev.endswith("，下列")
            or prev.endswith("，下面")
            or (
                # prev ends with introducer + ln starts with completion word
                prev.endswith(("下列", "下面", "有關", "關於"))
                and ln.startswith(("何者", "何處", "何種", "敘述", "關於", "有關"))
            )
            or (
                # prev ends with completion word + ln starts with new content
                # (highlight overlay broke a sentence at a Chinese word boundary)
                prev.endswith(("下列有關", "下列關於", "下面有關", "下面關於"))
            )
        ):
            # 0-overlap: prev is just "下列" / "下面" / ends with "，下列" and
            # ln starts a new phrase — concatenate with no joiner since prev
            # is an incomplete sentence-fragment.
            merge = True
            max_overlap = 0  # explicit no-overlap merge
        if merge:
            merged = prev + ln[max_overlap:]
            deduped[-1] = merged
            continue
        # Substring duplicate: ln is fully contained in prev (or vice versa)
        if len(ln) <= len(prev) and ln in prev and len(ln) >= 6:
            continue  # drop ln (redundant)
        if len(prev) <= len(ln) and prev in ln and len(prev) >= 6:
            deduped[-1] = ln  # ln is the longer/complete version
            continue
        deduped.append(ln)

    text = "\n".join(deduped)

    # Normalize whitespace inside paragraphs but preserve line breaks.
    paragraphs = re.split(r"\n\s*\n", text)
    out = []
    for p in paragraphs:
        cleaned = re.sub(r"\s+", " ", p).strip()
        if cleaned:
            out.append(cleaned)
    return "\n\n".join(out)


# Figure axis labels and page-footers that can bleed into option text on
# image-bearing pages. Stripped from option text TAIL only.
# Page-number footer pattern: requires preceding sentence-ending char so we
# don't strip legitimate trailing numbers ("1, 2, 4" or "60-70%").
_OPT_TRAIL_PAGENUM = re.compile(r"(?<=[一-鿿。．\.\)\]》】])\s+\d{1,3}\s*$")
_OPT_TRAIL_FIG_LABEL_AFTER_PERIOD = re.compile(
    r"(?<=[。．\.])\s+(?:[一-鿿]{1,4}|\＊|\*)(?:\s+(?:[一-鿿]{1,4}|\＊|\*))+\s*$"
)


def _normalize_option(text: str) -> str:
    """Collapse internal whitespace; trim figure-label / footer artifacts."""
    t = re.sub(r"\s+", " ", text).strip()
    # 1. Strip trailing isolated page numbers ("...選項 12").
    while True:
        m = _OPT_TRAIL_PAGENUM.search(t)
        if not m:
            break
        t = t[:m.start()].rstrip()
    # 2. Strip trailing figure axis labels that follow a period:
    #    "...遺漏病灶。 股骨頭 頭端 尾端" → "...遺漏病灶。"
    m = _OPT_TRAIL_FIG_LABEL_AFTER_PERIOD.search(t)
    if m:
        t = t[:m.start()].rstrip()
    # 3. Strip trailing 2+ short isolated Chinese labels with no period before.
    #    Heuristic: tail is " A B C" where each token is a 1-4 char Chinese
    #    word, possibly with ＊/*; the tail counts ≥2 such tokens AND the
    #    token immediately before the tail is itself ≥3 chars (option text
    #    proper) or ends in option-like punctuation.
    parts = t.split()
    if len(parts) >= 4:
        # Find longest trailing run of "short label" tokens
        def is_short_label(w: str) -> bool:
            return (
                w in {"＊", "*"}
                or (len(w) <= 4 and all("\u4e00" <= ch <= "\u9fff" for ch in w))
            )
        n_trail = 0
        for w in reversed(parts):
            if is_short_label(w):
                n_trail += 1
            else:
                break
        if n_trail >= 2 and len(parts) - n_trail >= 1:
            head = parts[: len(parts) - n_trail]
            # Head must contain at least one "substantive" token (≥3 chars)
            # so we don't strip e.g. "ABI" "DRE" "ROM"-style abbreviations.
            if any(len(w) >= 3 for w in head):
                t = " ".join(head)
    return t.strip()


@dataclass
class ParsedKaoGuQuestion:
    number: int
    correct_letter: str
    stem: str
    options: dict[str, str]
    # 0-indexed page where the question marker first appears (used by image
    # extractor to locate stem images).
    source_page: int | None = None
    warnings: list[str] = field(default_factory=list)


def parse_pdf(pdf_path: Path) -> list[ParsedKaoGuQuestion]:
    """Parse a 題目/考題 PDF and return parsed questions.

    Strategy:
    1. Extract text and per-question page index in two passes.
    2. Find all question markers (combined regex).
    3. For each question, the chunk runs from its marker to the next.
    4. Within the chunk, the stem is everything up to the first `(A)`;
       options come from `(A)..(E)` regex.
    """
    doc = fitz.open(pdf_path)
    page_texts: list[str] = []
    for page in doc:
        page_texts.append(_strip_page_lines(page.get_text() or ""))
    full_text = "\n".join(page_texts)

    # Build a per-question page-index by finding the marker on each page.
    page_idx: dict[int, int] = {}
    seen_qnums: set[int] = set()
    for pi, ptext in enumerate(page_texts):
        for m in _Q_MARKER_RE.finditer(ptext):
            n = int(m.group(2))
            # Only register a question once (first occurrence)
            if n not in seen_qnums and 1 <= n <= 250:
                page_idx[n] = pi
                seen_qnums.add(n)

    # Sequential match: only accept Q1, Q2, ... in order. Stale matches
    # (e.g. "(A) 1." being misread) are filtered by sequence check.
    matches = list(_Q_MARKER_RE.finditer(full_text))
    sequential: list[tuple[re.Match, int, str]] = []
    expected = 1
    for m in matches:
        num = int(m.group(2))
        if num == expected:
            letter = m.group(1).translate(_FULLWIDTH_TO_ASCII)
            sequential.append((m, num, letter))
            expected += 1
        if expected > 250:
            break

    questions: list[ParsedKaoGuQuestion] = []
    for i, (m, num, letter) in enumerate(sequential):
        start = m.end()
        end = (
            sequential[i + 1][0].start()
            if i + 1 < len(sequential)
            else len(full_text)
        )
        chunk = full_text[start:end].strip()

        # Stem = chunk up to first "(A)" or first "(A) " marker
        first_opt = re.search(r"\n?\s*\(A\)\s", chunk)
        if not first_opt:
            # Skip; no option labels — likely a bad marker hit
            continue
        stem_raw = chunk[: first_opt.start()].strip()
        opts_section = chunk[first_opt.start():]

        stem = _normalize_stem(stem_raw)
        if not stem:
            continue

        options: dict[str, str] = {}
        for om in _OPT_RE.finditer(opts_section):
            ll = om.group(1)
            opt_text = _normalize_option(om.group(2))
            if opt_text and ll not in options:
                options[ll] = opt_text

        warnings: list[str] = []
        for required in "ABCDE":
            if required not in options:
                warnings.append(f"missing-option-{required}")

        questions.append(
            ParsedKaoGuQuestion(
                number=num,
                correct_letter=letter,
                stem=stem,
                options=options,
                source_page=page_idx.get(num),
                warnings=warnings,
            )
        )

    doc.close()
    return questions


# Filename → year mapping for the question PDFs (varies between years).
_FILENAME_YEAR_MAP = {
    "109年題目.pdf": 109,
    "110年題目.pdf": 110,
    "111年題目.pdf": 111,
    "112年題目.pdf": 112,
    "113考題.pdf": 113,
    "114考題.pdf": 114,
}


def filename_to_year(name: str) -> int | None:
    return _FILENAME_YEAR_MAP.get(name)
