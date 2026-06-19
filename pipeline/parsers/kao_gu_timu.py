"""Parser for 112年題目.pdf — the clean question PDF (no 解題).

Format per question:
    {answer_letter} {number}\n
    {stem}\n
    (A) ...\n
    (B) ...\n
    (C) ...\n
    (D) ...\n
    (E) ...\n

Way cleaner than 解題.pdf (no 答案/出處/資料內容 boilerplate). Use this when
both PDFs are available; fall back to kao_gu.parse_pdf otherwise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz

_HEADER_RE = re.compile(r"^\s*\d{3}\s*年[\s\n]*$")
# A line starting with a single A-E letter (or full-width), then question num
_Q_HEAD_RE = re.compile(
    r"(?m)^\s*([A-EＡ-Ｅ])\s+(\d{1,3})\s*$"
)
_OPT_RE = re.compile(r"\(([A-EＡ-Ｅ])\)\s*", re.MULTILINE)
_FULLWIDTH_LETTERS = str.maketrans("ＡＢＣＤＥ", "ABCDE")


@dataclass
class ParsedTimuQuestion:
    number: int
    stem: str
    options: dict[str, str]
    correct_letter: str
    source_page: int | None = None
    warnings: list[str] = field(default_factory=list)


def _normalize(text: str) -> str:
    text = re.sub(r"（\s*([A-Ea-e])\s*）", r"(\1)", text)
    return text


def _strip_pageheaders(text: str) -> str:
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if re.match(r"^\d{3}\s*年.*$", s):
            continue
        if "山野縱走" in s or "個人化練習" in s:
            continue
        out.append(ln)
    return "\n".join(out)


def parse_pdf(pdf_path: Path) -> list[ParsedTimuQuestion]:
    doc = fitz.open(pdf_path)
    page_texts: list[str] = []
    page_offsets: list[int] = []  # cumulative char offset per page
    cur = 0
    for page in doc:
        t = page.get_text() or ""
        page_texts.append(t)
        page_offsets.append(cur)
        cur += len(t) + 1

    # Build a "page index lookup": given char offset in concatenated text, find page idx
    full_text = _normalize("\n".join(page_texts))
    full_clean = _strip_pageheaders(full_text)

    # Page index lookup uses raw concat (header lines retained for offset alignment).
    # _Q_HEAD_RE requires the header line to be on its own line. The 題目.pdf
    # format puts a blank line before the answer-letter line, so 「C 102」 sits
    # at line start cleanly. After _strip_pageheaders the structure is preserved.

    matches = list(_Q_HEAD_RE.finditer(full_clean))
    questions: list[ParsedTimuQuestion] = []
    for i, m in enumerate(matches):
        answer = m.group(1).translate(_FULLWIDTH_LETTERS)
        number = int(m.group(2))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(full_clean)
        chunk = full_clean[start:end].strip()

        # Stem is text before first (A)
        first_opt = _OPT_RE.search(chunk)
        if not first_opt:
            continue
        stem = re.sub(r"\s+", " ", chunk[:first_opt.start()]).strip()
        if not stem:
            continue

        # Options: split by (X)
        opt_section = chunk[first_opt.start():]
        opt_iter = list(_OPT_RE.finditer(opt_section))
        opts: dict[str, str] = {}
        for j, om in enumerate(opt_iter):
            letter = om.group(1).translate(_FULLWIDTH_LETTERS)
            opt_start = om.end()
            opt_end = opt_iter[j + 1].start() if j + 1 < len(opt_iter) else len(opt_section)
            text = opt_section[opt_start:opt_end].strip()
            text = re.sub(r"\s+", " ", text)
            if letter not in opts and text:
                opts[letter] = text

        warnings: list[str] = []
        for required in "ABCD":
            if required not in opts:
                warnings.append(f"missing-option-{required}")
        if answer not in opts:
            warnings.append(f"answer-{answer}-not-in-options")

        # Find page index — search original concat (page-header-stripped breaks offsets)
        # so we re-search the raw page texts for the {answer} {number} pattern.
        source_page = None
        for pi, ptext in enumerate(page_texts):
            if re.search(rf"(?m)^\s*{re.escape(answer)}\s+{number}\s*$", ptext):
                source_page = pi
                break

        questions.append(ParsedTimuQuestion(
            number=number,
            stem=stem,
            options=opts,
            correct_letter=answer,
            source_page=source_page,
            warnings=warnings,
        ))

    doc.close()
    return questions
