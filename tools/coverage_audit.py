"""Verify every question in the source PDFs got loaded into the DB.

考古題 (past_paper): expected 200/year. For each configured year:
  1. Open the 解題.pdf and find every 題號 marker.
  2. Compare the set of numbers found in the PDF to the set in the DB.
  3. Report:
     - PDF total count (sanity check vs 200)
     - DB total count (vs PDF total)
     - PDF-but-not-DB (parser missed it; extract original text)
     - DB-but-not-PDF (shouldn't happen; parser hallucinated?)
  4. For each missing number, fitz extract the surrounding context (best-effort)
     so user can read the original question without opening the PDF.

Writes data/_coverage_audit.md.

Run: python -m tools.coverage_audit
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

import fitz
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.config import settings  # noqa: E402
from backend.db import SessionLocal  # noqa: E402
from backend.models import Question, Source  # noqa: E402

_QUESTION_NO_RE = re.compile(r"題\s*號\s*0?0?(\d+)")


def find_question_positions(pdf_path: Path) -> list[tuple[int, int, int]]:
    """Return list of (number, page_index_0based, char_offset_in_page).

    Detects a 題號 marker on each page and records its location.
    """
    out: list[tuple[int, int, int]] = []
    doc = fitz.open(pdf_path)
    try:
        for pi, page in enumerate(doc):
            text = page.get_text() or ""
            for m in _QUESTION_NO_RE.finditer(text):
                num = int(m.group(1))
                out.append((num, pi, m.start()))
    finally:
        doc.close()
    return out


def extract_context_for_question(
    pdf_path: Path, page_index: int, before_pages: int = 0, after_pages: int = 1
) -> str:
    """Extract a window of pages around the question for human review."""
    doc = fitz.open(pdf_path)
    try:
        n = len(doc)
        lo = max(0, page_index - before_pages)
        hi = min(n - 1, page_index + after_pages)
        chunks: list[str] = []
        for pi in range(lo, hi + 1):
            text = doc[pi].get_text() or ""
            chunks.append(f"[Page {pi + 1}]\n{text}")
        return "\n\n".join(chunks)
    finally:
        doc.close()


def main() -> None:
    out_path = Path("data/_coverage_audit.md")
    lines: list[str] = []
    lines.append("# Coverage audit — source PDFs vs DB\n")

    # ─────────────────────────── 考古題 ───────────────────────────

    with SessionLocal() as s:
        past_paper_src = s.execute(
            select(Source).where(Source.code == "past_paper")
        ).scalar_one_or_none()

        # Build DB inventory by year
        kg_db: dict[int, set[int]] = defaultdict(set)
        if past_paper_src:
            kg_rows = s.execute(
                select(Question.year, Question.source_ref).where(
                    Question.source_id == past_paper_src.id
                )
            ).all()
            for year, ref in kg_rows:
                m = re.search(r"Q(\d+)", ref or "")
                if m and year is not None:
                    kg_db[year].add(int(m.group(1)))

    lines.append("## 考古題 (past_paper)\n")
    lines.append(f"Expected per year: **200 questions**\n\n")

    known_already_missing = {(112, 102), (112, 104)}

    for year in settings.past_paper_years_list:
        pdf_path = settings.past_paper_dir / f"{year}年解題.pdf"
        lines.append(f"### {year} 年\n")
        if not pdf_path.exists():
            lines.append(f"⚠️ PDF not found at `{pdf_path}`\n\n")
            continue
        positions = find_question_positions(pdf_path)
        pdf_numbers = {n for (n, _, _) in positions}
        db_numbers = kg_db.get(year, set())

        missing_in_db = pdf_numbers - db_numbers  # parser missed
        missing_in_pdf = db_numbers - pdf_numbers  # weird: DB has but PDF doesn't
        missing_total = set(range(1, 201)) - pdf_numbers  # PDF itself short

        lines.append(f"- PDF: `{pdf_path.name}`")
        lines.append(f"- PDF 內題號數: {len(pdf_numbers)} (expected 200)")
        lines.append(f"- DB count: {len(db_numbers)}")
        lines.append(f"- PDF 缺號（不在 1..200 的）: {sorted(missing_total)}")
        lines.append(
            f"- PDF→DB 缺漏（parser 沒抓到）: {sorted(missing_in_db)} ({len(missing_in_db)} 題)"
        )
        if missing_in_pdf:
            lines.append(
                f"- ⚠️ DB 有但 PDF 沒有: {sorted(missing_in_pdf)} (suspicious!)"
            )
        lines.append("")

        # Build a number → page index map for context extraction
        first_page_for: dict[int, int] = {}
        for n, pi, _co in positions:
            first_page_for.setdefault(n, pi)

        # For each PDF→DB missing number, extract original context
        if missing_in_db:
            lines.append("#### 缺題原文（含相鄰頁面）\n")
            for num in sorted(missing_in_db):
                marker = " 【已知缺】" if (year, num) in known_already_missing else ""
                lines.append(f"##### {year} Q{num:03d}{marker}\n")
                pi = first_page_for.get(num)
                if pi is None:
                    lines.append("（PDF 內找不到對應頁面）\n")
                else:
                    ctx = extract_context_for_question(
                        pdf_path, pi, before_pages=0, after_pages=1
                    )
                    lines.append("```text")
                    lines.append(ctx[:3000])
                    if len(ctx) > 3000:
                        lines.append(f"\n... (truncated, total {len(ctx)} chars)")
                    lines.append("```\n")

    # ─────────────────────────── 寫檔 ───────────────────────────

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report: {out_path}")
    for year in settings.past_paper_years_list:
        pdf_count = sum(
            1
            for _ in (
                find_question_positions(settings.past_paper_dir / f"{year}年解題.pdf")
                if (settings.past_paper_dir / f"{year}年解題.pdf").exists()
                else []
            )
        )
        # Print quick summary
        db_count = len(kg_db.get(year, set()))
        print(f"  {year}: PDF positions ~{pdf_count}, DB unique numbers={db_count}")


if __name__ == "__main__":
    main()
