"""Parser for 考古題 解題.pdf (years 112-114). All native text.

Per-question chunk format (one question can span multiple PDF pages):

    題號 001  (or 題號001)
    <stem text spanning multiple lines>
    答案 E    (or 答案E)
    (A) <option>     ← may or may not be present (112 PDF often omits these labels)
    (B) <option>
    ...
    (E) <option>
    出處
    <reference info / explanation, possibly with embedded line breaks
     and per-page header repetitions>
    附言
    <optional notes>

Parser strategy:
1. Read all pages, concatenate.
2. Strip per-page headers (`XXX 年度考題`, `範圍ADL ...`, `解題者 ...`).
3. Split by `題號` markers.
4. For each chunk: extract question number, stem, answer, options, explanation, reference.
5. If the (A)..(E) regex yields <4 options (e.g. 112 PDF strips letters), fall
   back to a layout-aware option splitter that uses fitz line/column geometry.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz

# Line patterns we strip from page text before joining
_HEADER_LINE_PATTERNS = [
    re.compile(r"^\s*\d{3}\s*年度考題\s*$"),
    re.compile(r"^\s*範圍\s*ADL\b.*$"),
    re.compile(r"^\s*解題者\s*$"),
    # The line right after "解題者" is usually the human's name; keep one heuristic:
    # we'll strip "新光醫院" / hospital prefix lines after detecting context
]

_QUESTION_SPLIT_RE = re.compile(r"題\s*號\s*0?0?(\d+)")
# Accept ASCII A-E and full-width Ａ-Ｅ (some 解題 use 全形 letters)
_FULLWIDTH_TO_ASCII = str.maketrans("ＡＢＣＤＥａｂｃｄｅ", "ABCDEABCDE")
_ANSWER_RE = re.compile(r"答\s*案[:：]?\s*[\(\[]?([A-EＡ-Ｅａ-ｅ])[\)\]]?")
_OPT_RE = re.compile(
    r"\(([A-E])\)\s*(.+?)(?=\n\([A-E]\)|出\s*處|附\s*言|$)",
    re.DOTALL,
)
_SOURCE_BOUNDARY_RE = re.compile(r"出\s*處")
_FU_YAN_RE = re.compile(r"附\s*言")
# 出處 may be followed immediately by content ("出處復健與..."), or by
# punctuation + newline ("出處,\n..."), or by just newline ("出處\n..."),
# or even nothing (empty explanation, "出處\n附言"). Accept all variants.
_SOURCE_RE = re.compile(
    r"出\s*處[，,、:：\s]*([\s\S]*?)(?:附\s*言|\Z)", re.IGNORECASE
)


def _normalize_paragraphs(text: str) -> str:
    """Preserve paragraph breaks; collapse intra-paragraph whitespace."""
    # Normalize full-width parentheses around option letters first so all
    # downstream regexes can use a single pattern.
    text = re.sub(r"（\s*([A-Ea-e])\s*）", r"(\1)", text)
    # Common bullet markers that signal a new logical paragraph.
    text = re.sub(r"\n(\s*\([A-Ea-e0-9]+\))", r"\n\n\1", text)
    text = re.sub(r"\n(\s*\d+[\.\)])", r"\n\n\1", text)
    text = re.sub(r"\n(\s*[A-E]\s*[:：])", r"\n\n\1", text)
    text = re.sub(r"(?<=[\.\"。」])\s+([A-E]\s*[:：])", r"\n\n\1", text)
    # 全形/半形 bullet glyphs at line start ("●" / "•" / "‣" / "-")
    text = re.sub(r"\n(\s*[●•‣◆▪▫])", r"\n\n\1", text)
    text = re.sub(r"\n(\s*[-－–—]\s+)", r"\n\n\1", text)
    # Some 解題 PDFs put the option-letter at the END of a bullet
    # ("● ... text ... (A) \n● ..."). Treat the next bullet as a fresh para too.
    text = re.sub(r"\(([A-E])\)\s*\n", r"(\1)\n\n", text)
    # "(X)" that appears mid-line right after a sentence terminator — split.
    text = re.sub(r"([。．！？\)\）」])\s*(\([A-E]\))", r"\1\n\n\2", text)
    # Same with two consecutive option labels with no separator: "(A)(B)" → "(A)\n\n(B)"
    text = re.sub(r"(\([A-E]\))(\([A-E]\))", r"\1\n\n\2", text)
    # "(X).<space>" mid-paragraph marks end of option-X discussion; break.
    text = re.sub(r"\(([A-E])\)([。．\.])\s+", r"(\1)\2\n\n", text)
    # Lettered "A. B. C. D. E." statement lists. Break when preceded by:
    #   • sentence terminator + space, OR
    #   • Chinese character + space (statements often end mid-Chinese, no 。)
    text = re.sub(r"([。．！？\?])\s+([A-E][\.．]\s)", r"\1\n\n\2", text)
    text = re.sub(r"(?<=[一-鿿])\s+([A-E][\.．]\s)", r"\n\n\1", text)
    # Numbered lists "1. 2. 3." (no required space after period — Q#77 has "1.CIMT")
    text = re.sub(r"([。．！？\?])\s+(\d+[\.．\)])", r"\1\n\n\2", text)
    text = re.sub(r"(?<=[一-鿿])\s+(\d+[\.．\)])", r"\n\n\1", text)
    # Parenthetical numbered "(1) (2) (3)"
    text = re.sub(r"([。．！？\?])\s+(\([0-9]+\)\s)", r"\1\n\n\2", text)
    text = re.sub(r"(?<=[一-鿿])\s+(\([0-9]+\)\s)", r"\n\n\1", text)
    # Bullets "•" / "‣" mid-line
    text = re.sub(r"([。．！？\?])\s+([●•‣◆▪▫])", r"\1\n\n\2", text)
    text = re.sub(r"(?<=[一-鿿])\s+([●•‣◆▪▫])", r"\n\n\1", text)
    paragraphs = re.split(r"\n\s*\n+", text)
    out = []
    for p in paragraphs:
        cleaned = re.sub(r"\s+", " ", p).strip()
        if cleaned:
            out.append(cleaned)
    return "\n\n".join(out)


@dataclass
class ParsedKaoGuQuestion:
    number: int
    stem: str
    options: dict[str, str]
    correct_letter: str
    explanation: str | None
    reference: str | None
    warnings: list[str] = field(default_factory=list)
    source_page: int | None = None  # 0-indexed page where 題號 first appears


def _strip_page_headers(page_text: str) -> str:
    """Strip per-page boilerplate ('XX 年度考題', '範圍ADL ...', '解題者' + name).
    NOTE: do NOT strip standalone digit lines — some questions split 題號 across
    two lines ('題號 \\n042'), and stripping the digit breaks the split regex.
    """
    out_lines: list[str] = []
    skip_next_solver_name = False
    for ln in page_text.splitlines():
        s = ln.strip()
        if not s:
            out_lines.append("")
            continue
        if any(p.match(ln) for p in _HEADER_LINE_PATTERNS):
            if "解題者" in s:
                skip_next_solver_name = True
            continue
        if skip_next_solver_name and s:
            skip_next_solver_name = False
            if re.match(r"^[一-鿿].*醫院.*", s) or re.match(r"^[一-鿿]+\s*R\d", s):
                continue
        out_lines.append(ln)
    return "\n".join(out_lines)


def _page_question_index(doc: fitz.Document) -> dict[int, int]:
    """Map question number → page index where its 題號 marker first appears."""
    out: dict[int, int] = {}
    for pi, page in enumerate(doc):
        text = page.get_text() or ""
        for m in _QUESTION_SPLIT_RE.finditer(text):
            num = int(m.group(1))
            out.setdefault(num, pi)
    return out


def _layout_options_for_question(
    page: fitz.Page, target_q_num: int
) -> dict[str, str]:
    """Re-extract options for a question using line-level geometry.

    Strategy: find lines between 答案 and 出處 (or next 題號) on this page;
    merge spans by y; treat the leftmost x as the option-start margin; lines
    starting near that margin begin a new option, deeper-x lines wrap.

    Returns {} when the page doesn't contain enough structure to be useful.
    """
    d = page.get_text("dict")

    # ── Pass 1: find anchor y-coords for THIS question on the page ──────────
    ans_y: float | None = None
    src_y: float | None = None  # 出處 boundary
    next_q_y: float | None = None  # next 題號 boundary
    seen_target = False

    raw_lines: list[tuple[float, float, float, str]] = []  # (y, x0, x1, text)
    for blk in d.get("blocks", []):
        if blk.get("type") != 0:
            continue
        for line in blk.get("lines", []):
            bbox = line.get("bbox")
            spans = line.get("spans", [])
            text = "".join(s.get("text", "") for s in spans)
            if not text.strip():
                continue
            raw_lines.append((bbox[1], bbox[0], bbox[2], text))

    raw_lines.sort()

    # Locate this question's 題號, then 答案, then 出處
    for y, x, _x1, t in raw_lines:
        m = _QUESTION_SPLIT_RE.search(t)
        if m and int(m.group(1)) == target_q_num:
            seen_target = True
            continue
        if not seen_target:
            continue
        if ans_y is None and _ANSWER_RE.search(t):
            ans_y = y
            continue
        if ans_y is not None and src_y is None and _SOURCE_BOUNDARY_RE.search(t):
            src_y = y
            break
        if ans_y is not None and src_y is None:
            mq = _QUESTION_SPLIT_RE.search(t)
            if mq and int(mq.group(1)) != target_q_num:
                next_q_y = y
                break

    if ans_y is None:
        return {}
    # The 出處 label and the first line of "資料內容" (English explanation text)
    # often sit on roughly the same horizontal row, so a strict `y < src_y` lets
    # one explanation line bleed into the options band. Buffer by 8 px.
    if src_y is not None:
        upper = src_y - 8
    elif next_q_y is not None:
        upper = next_q_y
    else:
        upper = 1e9

    # ── Pass 2: collect lines strictly between ans_y and upper ──────────────
    # PDFs put the 答案 label in a LEFT column and the option-A text on the
    # SAME visual row but at a higher x. So allow a small ε above ans_y.
    band = [
        (y, x0, x1, t)
        for (y, x0, x1, t) in raw_lines
        if ans_y - 8 < y < upper and x0 > 100
    ]
    if not band:
        return {}

    # Merge spans on the same physical y (within 3 px) into a logical line.
    # Track both leftmost x (x0) and rightmost x (x1) so the caller can detect
    # full-width wrap-out lines.
    band.sort()
    logical: list[tuple[float, float, float, str]] = []  # (y, x0, x1, text)
    cur_y: float | None = None
    cur_parts: list[tuple[float, float, str]] = []  # (x0, x1, text)
    for y, x0, x1, t in band:
        if cur_y is None or abs(y - cur_y) <= 3:
            cur_y = y if cur_y is None else cur_y
            cur_parts.append((x0, x1, t))
        else:
            cur_parts.sort()
            merged_x1 = max(p[1] for p in cur_parts)
            logical.append((cur_y, cur_parts[0][0], merged_x1,
                            "".join(p[2] for p in cur_parts).strip()))
            cur_y = y
            cur_parts = [(x0, x1, t)]
    if cur_parts:
        cur_parts.sort()
        merged_x1 = max(p[1] for p in cur_parts)
        logical.append((cur_y, cur_parts[0][0], merged_x1,
                        "".join(p[2] for p in cur_parts).strip()))

    if not logical:
        return {}

    # ── Pass 3: group by start-x ────────────────────────────────────────────
    # Pick the smallest start_x bucket whose frequency is ≥ 4 (matching the
    # expected 4–5 option starts). Plain min(start_x) catches stray
    # left-overflow wraps; plain mode loses to wrap-line buckets when wraps
    # outnumber starts (e.g. multi-line options).
    from collections import Counter
    buckets = Counter(round(sx / 3) * 3 for _, sx, _x1, _t in logical)
    sorted_buckets = sorted(buckets.keys())

    # Build candidate (margin, tolerance) pairs. Each represents a guess at
    # where the option-start column is.  Single-bucket clusters get NARROW
    # tolerance; adjacent-bucket merges get a tolerance that spans the merge.
    # Some PDFs put wraps to the LEFT of starts (Q#7 pattern), some to the
    # RIGHT (Q#5 pattern); we'll try every plausible candidate and keep the
    # one whose grouping yields exactly 4–5 options.
    candidates_pairs: list[tuple[float, float]] = []
    for b in sorted_buckets:
        if buckets[b] >= 4:
            candidates_pairs.append((b, 1.5))
    for i, b in enumerate(sorted_buckets):
        combined = buckets[b]
        j = i + 1
        while j < len(sorted_buckets) and sorted_buckets[j] - b <= 6:
            combined += buckets[sorted_buckets[j]]
            j += 1
        if combined >= 4:
            tol = max(1.5, sorted_buckets[j - 1] - b + 1.5)
            candidates_pairs.append((float(b), tol))

    def _emit_groups(g: list[list[str]]) -> dict[str, str]:
        out: dict[str, str] = {}
        for i, parts in enumerate(g):
            text = " ".join(parts)
            text = re.sub(r"\s+", " ", text).strip()
            text = re.sub(r"^\([A-E]\)\s*", "", text)
            if text:
                out[chr(ord("A") + i)] = text
        return out

    def _group_with_margin(margin: float, tol: float) -> list[list[str]]:
        groups: list[list[str]] = []
        current: list[str] = []
        for _, sx, _x1, t in logical:
            is_start = -1.5 <= (sx - margin) <= tol
            if is_start and current:
                groups.append(current)
                current = [t]
            elif is_start:
                current = [t]
            else:
                if not current:
                    current = [t]
                else:
                    current.append(t)
        if current:
            groups.append(current)
        return groups

    # Try every candidate margin; pick the first whose grouping yields 4–5.
    for cand_margin, cand_tol in candidates_pairs:
        groups = _group_with_margin(cand_margin, cand_tol)
        if 4 <= len(groups) <= 5:
            return _emit_groups(groups)

    # ── Pass 4 fallback: width-based grouping ───────────────────────────────
    # A line whose right edge (x1) sits near the column's right margin was
    # likely cut off and continues on the next line. So a line is a
    # "wrap-out" when x1 ≥ page_right - 20. Lines that end well before the
    # right margin close their option. We try a couple of thresholds and
    # accept the first that yields exactly 4–5 options — different questions
    # have slightly different right-margin alignments, so being adaptive
    # avoids picking one wrong threshold for the whole PDF.
    if 4 <= len(logical) <= 12:
        page_right = max(x1 for _, _x0, x1, _t in logical)
        for buffer in (15, 20, 25, 30):
            wide_threshold = page_right - buffer
            groups: list[list[str]] = []
            current: list[str] = []
            for _y, _x0, x1, t in logical:
                current.append(t)
                if x1 < wide_threshold:
                    groups.append(current)
                    current = []
            if current:
                groups.append(current)
            if 4 <= len(groups) <= 5:
                return _emit_groups(groups)

    # ── Pass 5 fallback: y-gap analysis (uniform-x layouts) ─────────────────
    if not candidates_pairs or len({round(sx) for _, sx, _x1, _t in logical}) == 1:
        # Q#489 pattern: 5 wide option lines + 1 short wrap continuation,
        # all sharing x0. Pass 3 sees 6 starts (uniform x0); Pass 4 closes
        # only at the short L2 / wrap line, yielding 2 groups; Pass 5 with
        # uniform gaps emits 6 groups. Solution: when uniform-x AND there
        # are more lines than expected option count, collapse short trailing
        # wraps into the preceding wrap-out line before gap analysis.
        is_uniform_x = (
            len({round(sx) for _, sx, _x1, _t in logical}) == 1
        )
        if is_uniform_x and len(logical) > 5:
            page_right = max(x1 for _, _, x1, _ in logical)
            merged: list[tuple[float, float, float, str]] = [logical[0]]
            for i in range(1, len(logical)):
                cur = logical[i]
                prev = merged[-1]
                same_x0 = abs(cur[1] - prev[1]) <= 3
                prev_wraps = prev[2] >= page_right - 30
                cur_short = cur[2] < page_right * 0.5
                if same_x0 and prev_wraps and cur_short:
                    merged[-1] = (
                        prev[0], prev[1], max(prev[2], cur[2]),
                        (prev[3].rstrip() + cur[3]).strip(),
                    )
                else:
                    merged.append(cur)
            logical = merged
        if len(logical) < 4 or len(logical) > 12:
            return {}
        gaps = [logical[i + 1][0] - logical[i][0] for i in range(len(logical) - 1)]
        wrap_gap = min(gaps) if gaps else 0
        gap_max = max(gaps) if gaps else 0

        groups2: list[list[str]]
        if gap_max - wrap_gap <= 2:
            # Uniform gaps → each line IS a complete option (no wraps).
            groups2 = [[t] for _, _, _x1, t in logical]
        else:
            boundary_threshold = wrap_gap + 1.5
            groups2 = [[logical[0][3]]]
            for i, gap in enumerate(gaps):
                if gap > boundary_threshold:
                    groups2.append([logical[i + 1][3]])
                else:
                    groups2[-1].append(logical[i + 1][3])

        if 4 <= len(groups2) <= 5:
            return _emit_groups(groups2)

    return {}


_TESS_EXPLANATION_MIN = 50
"""Below this many chars of fitz-extracted explanation, attempt to
supplement from Tesseract OCR if a .tess.tsv is available for the page.
Empirically, fitz-broken explanations are 0–30 chars (Q#048: expl=0,
Q#060: expl=9, Q#069: expl=8); healthy explanations are >>200 chars."""


def _tess_dir_for(pdf_path: Path) -> Path | None:
    """Return data/ocr_raw/kao_gu/{stem}/ if it exists, else None."""
    candidate = Path("data/ocr_raw/kao_gu") / pdf_path.stem
    return candidate if candidate.is_dir() else None


def _supplement_explanation_from_tess(
    pq: "ParsedKaoGuQuestion",
    doc: fitz.Document,
    page_no: int,
    tess_dir: Path,
) -> "ParsedKaoGuQuestion":
    """Re-extract explanation/reference for one question from Tesseract OCR.

    Used when fitz produced an empty/short explanation, which on 112年解題.pdf
    indicates vector-glyph rendering fitz can't read. The .tess.tsv supplies
    text; fitz drawings still supply markdown styling.
    """
    tsv = tess_dir / f"page_{page_no:03d}.tess.tsv"
    if not tsv.exists():
        return pq

    # Lazy import so production paths without OCR data don't pay the cost
    from tools.render_markdown import page_to_markdown_with_tess

    md = page_to_markdown_with_tess(doc[page_no], tsv)
    if not md.strip():
        return pq

    # Strip markdown markup (==, **, *, <u>, </u>) for anchor-matching purposes.
    # Keep the original `md` for the actual content output. We use this stripped
    # view ONLY to find the 出處 boundary, then map the index back.
    _MD_TAGS = re.compile(r"(?:</?u>|\*\*|==|\*)")
    plain_chars: list[str] = []
    char_map: list[int] = []  # plain_idx → md_idx
    i = 0
    while i < len(md):
        m = _MD_TAGS.match(md, i)
        if m:
            i = m.end()
            continue
        plain_chars.append(md[i])
        char_map.append(i)
        i += 1
    plain = "".join(plain_chars)

    # Locate the explanation start. OCR often mangles 出處 → just "處" or
    # drops the 出. Anchor strategy (in order of reliability):
    #   1. 出處 / 出\s*處 / 岀處 (variant glyphs) anywhere in plain text.
    #   2. Plain `處` at the start of a (possibly-blockquoted) line.
    #   3. The line immediately AFTER the last `(E)` option line (last
    #      because explanation text may also contain "(E)" mid-paragraph).
    src_m = (
        re.search(r"(?:出|岀)\s*處", plain)
        or re.search(r"(?:^|\n)[>\s|]*處(?=\s)", plain)
    )
    if src_m:
        # Map plain end index back to md index, then continue past 處
        end_plain = src_m.end() - 1  # last matched char index in plain
        end_md = char_map[end_plain] + 1 if end_plain < len(char_map) else len(md)
        post_src = md[end_md:]
    else:
        lines = md.split("\n")
        e_line = -1
        for i, ln in enumerate(lines):
            stripped_ln = _MD_TAGS.sub("", ln)
            if re.match(r"^\s*[>|]?\s*\(E\)\s+\S", stripped_ln):
                e_line = i
        if e_line < 0:
            return pq
        post_src = "\n".join(lines[e_line + 1:])

    # Truncate at 附言 (footer) if present
    fu_m = _FU_YAN_RE.search(post_src)
    if fu_m:
        post_src = post_src[:fu_m.start()]

    # Strip canned-template message
    _CANNED = re.compile(r"\(\s*附[^)]*?資料\s*[\.。]\s*\)?\s*", re.DOTALL)
    post_src = _CANNED.sub("", post_src, count=1)

    # Split reference (citation column) vs explanation (per-option discussion).
    # Tesseract often interleaves citation column with explanation column row-by-row;
    # the 資料內容 marker is the most reliable splitter when present. OCR also
    # mangles 資料內容 → 賢料內客 / 賢料內 / 料內 etc., so accept partial matches.
    data_m = (
        re.search(r"[資賢費]\s*料\s*內\s*[容客]", post_src)
        or re.search(r"料\s*內\s*[容客]", post_src)  # 資 dropped
        or re.search(r"^\s*[\|>]?\s*料\s*內", post_src)  # 資 + 容 both lost
    )
    if data_m:
        new_reference = re.sub(r"\s+", " ",
                                post_src[:data_m.start()]).strip() or None
        new_explanation = post_src[data_m.end():].strip() or None
    else:
        new_explanation = post_src.strip() or None
        new_reference = pq.reference  # keep fitz's reference if any

    # Only replace fields that were broken; preserve healthy ones.
    out_explanation = (new_explanation
                       if (not pq.explanation
                           or len(pq.explanation) < _TESS_EXPLANATION_MIN)
                       else pq.explanation)
    out_reference = (new_reference if not pq.reference else pq.reference)

    new_warnings = list(pq.warnings)
    if out_explanation is not pq.explanation:
        new_warnings.append("explanation-from-tess")

    return ParsedKaoGuQuestion(
        number=pq.number,
        stem=pq.stem,
        options=pq.options,
        correct_letter=pq.correct_letter,
        explanation=out_explanation,
        reference=out_reference,
        warnings=new_warnings,
    )


def parse_pdf(pdf_path: Path) -> list[ParsedKaoGuQuestion]:
    doc = fitz.open(pdf_path)
    cleaned_pages: list[str] = []
    for page in doc:
        text = page.get_text() or ""
        cleaned_pages.append(_strip_page_headers(text))

    full_text = "\n".join(cleaned_pages)
    page_idx = _page_question_index(doc)
    tess_dir = _tess_dir_for(pdf_path)

    # Find all 題號 markers and their positions
    matches = list(_QUESTION_SPLIT_RE.finditer(full_text))
    questions: list[ParsedKaoGuQuestion] = []

    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(full_text)
        chunk = full_text[start:end].strip()
        number = int(m.group(1))

        ans_m = _ANSWER_RE.search(chunk)
        if not ans_m:
            continue
        correct = ans_m.group(1).translate(_FULLWIDTH_TO_ASCII)

        # Stem = text from start of chunk up to 答案. Run through the
        # paragraph normaliser so case-style stems with embedded
        # statement lists ("...錯誤? A. ... B. ...") render with breaks.
        stem_text = chunk[: ans_m.start()].strip()
        if not stem_text:
            continue
        stem = _normalize_paragraphs(stem_text)

        # Options are confined to the section BETWEEN 答案 and 出處 — anything
        # after 出處 is reference/explanation, even if it contains (A)..(E).
        post_ans = chunk[ans_m.end():]
        boundary = _SOURCE_BOUNDARY_RE.search(post_ans)
        options_section = post_ans[: boundary.start()] if boundary else post_ans

        options: dict[str, str] = {}
        for om in _OPT_RE.finditer(options_section):
            letter = om.group(1)
            opt_text = re.sub(r"\s+", " ", om.group(2)).strip()
            if opt_text and letter not in options:
                options[letter] = opt_text

        # Layout-aware fallback if regex failed to find ≥4 options
        if len(options) < 4:
            page_no = page_idx.get(number)
            if page_no is not None:
                layout_opts = _layout_options_for_question(doc[page_no], number)
                if len(layout_opts) >= 4:
                    options = layout_opts

        warnings: list[str] = []
        for required in "ABCD":
            if required not in options:
                warnings.append(f"missing-option-{required}")
        if correct not in options:
            warnings.append(f"answer-letter-{correct}-not-in-options")

        # Explanation/reference: after 出處.
        # Most 解題 PDFs put the textbook citation FIRST then a marker like
        # "資料內容" introducing the per-option discussion. Splitting on
        # "資料內容" is far more reliable than splitting on the first "(A)" —
        # the latter mis-fires when the explanation contains "(A)" mid-text
        # (Q#978 had ref=1446 / expl=216 because (A) only appeared in the
        # SECOND-to-last paragraph, leaving everything else stuck in ref).
        src_m = _SOURCE_RE.search(post_ans)
        explanation: str | None = None
        reference: str | None = None
        if src_m:
            src_text = _normalize_paragraphs(src_m.group(1))
            data_m = re.search(r"資\s*料\s*內\s*容", src_text)
            if data_m:
                reference = re.sub(r"\s+", " ", src_text[:data_m.start()]).strip()
                explanation = src_text[data_m.end():].strip() or None
            else:
                # Without 資料內容 marker, try in order of decreasing reliability:
                #   1. First bullet glyph (●) — bullets are the explanation,
                #      anything before is the citation. (Q#625 case)
                #   2. First "(A)" at the START of a paragraph (\n\n(A)).
                #   3. Last resort: first "(A)" anywhere — fragile.
                bullet_m = re.search(r"[●•‣◆▪▫]", src_text)
                para_a_m = re.search(r"(?:^|\n\s*\n)\s*\(A\)", src_text)
                any_a_idx = src_text.find("(A)")
                if bullet_m and bullet_m.start() < (any_a_idx if any_a_idx >= 0 else len(src_text)):
                    reference = re.sub(r"\s+", " ", src_text[:bullet_m.start()]).strip()
                    explanation = src_text[bullet_m.start():].strip() or None
                elif para_a_m:
                    reference = re.sub(r"\s+", " ", src_text[:para_a_m.start()]).strip()
                    explanation = src_text[para_a_m.start():].strip() or None
                elif any_a_idx > 0:
                    reference = re.sub(r"\s+", " ", src_text[:any_a_idx]).strip()
                    explanation = src_text[any_a_idx:].strip() or None
                else:
                    reference = re.sub(r"\s+", " ", src_text[:120]).strip()
                    explanation = src_text

            # Strip the canned-template note that immediately follows 資料內容:
            # "(附[最新版本]相關內文節錄, 相符敘述處請畫底加標選項,每個選項都要找答案
            #   建議從建議教科書先找,若無,可附journal 或研討會資料."
            # User: this is meaningless boilerplate that should never reach the
            # web UI. Drop it from BOTH fields if present.
            _CANNED = re.compile(
                r"\(\s*附[^)]*?資料\s*[\.。]\s*\)?\s*",
                re.DOTALL,
            )
            if explanation:
                explanation = _CANNED.sub("", explanation, count=1).strip() or None
            if reference:
                reference = _CANNED.sub("", reference, count=1).strip() or None

        pq = ParsedKaoGuQuestion(
            number=number,
            stem=stem,
            options=options,
            correct_letter=correct,
            explanation=explanation,
            reference=reference,
            warnings=warnings,
            source_page=page_idx.get(number),
        )

        # Tesseract supplement: only when fitz's explanation is empty or
        # very short AND we have OCR data available for this page.
        if tess_dir is not None and (
            not explanation or len(explanation) < _TESS_EXPLANATION_MIN
        ):
            page_no = page_idx.get(number)
            if page_no is not None:
                pq = _supplement_explanation_from_tess(pq, doc, page_no, tess_dir)

        questions.append(pq)

    doc.close()
    return questions
