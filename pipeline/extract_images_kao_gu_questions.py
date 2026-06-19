"""Extract STEM images from 考古題 題目/考題 PDFs (109-114).

Distinct from `extract_images_kao_gu.py` which only handles 解題 PDFs and
classifies all images as is_explanation=1. This script targets the question
PDFs (where stem images live) and writes is_explanation=0.

Question association strategy:
- For each page, parse all question markers (the same combined regex used by
  the question parser). Track which Q owns each portion of the page by
  y-coordinate (image's y_top is BETWEEN this Q's marker y and the next Q's
  marker y).
- If a page has no question marker, all images on that page belong to the
  most-recent prior Q (continuation page).
- Option-letter assignment: collect (option_letter, y_top) blocks; image
  whose y_top is below an option's y_top but above the next option's y_top
  is associated to that option.

Run: python -m pipeline.extract_images_kao_gu_questions
"""

from __future__ import annotations

import re
from pathlib import Path

import fitz
from sqlalchemy import select

from backend.config import settings
from backend.db import SessionLocal
from backend.models import Question, QuestionImage, Source
from pipeline.parsers.kao_gu_questions import _Q_MARKER_RE, filename_to_year

IMG_ROOT = Path("data/question_images")
MIN_DIM_PX = 40
MIN_AREA_PX = 8000
MIN_BYTES = 3000

QUESTION_PDFS = [
    "109年題目.pdf",
    "110年題目.pdf",
    "111年題目.pdf",
    "112年題目.pdf",
    "113考題.pdf",
    "114考題.pdf",
]


def _question_y_blocks(page) -> list[tuple[int, float, float]]:
    """Return [(q_num, y_top, y_bottom), ...] for question markers found
    on this page, using line-level layout (`get_text("dict")`).
    y_bottom is roughly the y-position of the next marker on this page,
    or page-bottom if last.
    """
    d = page.get_text("dict")
    raw_lines: list[tuple[float, str]] = []
    for blk in d.get("blocks", []):
        if blk.get("type") != 0:
            continue
        for line in blk.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(s.get("text", "") for s in spans).strip()
            if not text:
                continue
            y_top = line.get("bbox", [0, 0, 0, 0])[1]
            raw_lines.append((y_top, text))

    raw_lines.sort()
    # Find marker lines and their y-positions. The marker may span 2 lines
    # ("E\n001") OR appear as 3 spans on the same y (some PDFs put letter,
    # number, and stem-start on the same row).
    blocks: list[tuple[int, float]] = []  # (q_num, y_top)
    used_indices: set[int] = set()
    for i, (y, text) in enumerate(raw_lines):
        if i in used_indices:
            continue
        # Try inline first: "E 001" or "E 001."
        m_inline = re.match(r"^([A-EＡ-Ｅ])\s+(\d{1,3})\b", text)
        if m_inline:
            q_num = int(m_inline.group(2))
            if 1 <= q_num <= 250:
                blocks.append((q_num, y))
            continue
        # Solo letter line — peek nearby (within 5 lines OR same y) for number.
        # The number may appear on its own line (e.g. "001\n") OR as the prefix
        # of a same-y line that also contains the stem text (114考題 layout:
        # letter "E" and "001 下列..." share y=62.9 but split across two lines).
        # Strategy: pass 1 — prefer pure-digit lines (most reliable). pass 2 —
        # fall back to "NNN <stem>" same-y splits. Without pass-1 priority, a
        # stem like "72 歲女性..." would be mis-parsed as q_num=72 when the
        # real q_num "160" is one line away (110年題目 page 32 case).
        if re.match(r"^[A-EＡ-Ｅ]$", text):
            chosen = None
            # pass 1: pure-digit lines
            for j in range(max(0, i - 3), min(len(raw_lines), i + 4)):
                if j == i:
                    continue
                jy, jtext = raw_lines[j]
                m_pure = re.match(r"^(\d{1,3})\s*$", jtext)
                if m_pure and abs(jy - y) <= 30:
                    q_num = int(m_pure.group(1))
                    if 1 <= q_num <= 250:
                        chosen = (q_num, j)
                        break
            # pass 2: "NNN stem-text" same-y split (114考題)
            if chosen is None:
                for j in range(max(0, i - 3), min(len(raw_lines), i + 4)):
                    if j == i:
                        continue
                    jy, jtext = raw_lines[j]
                    if abs(jy - y) > 5:
                        # require near-same y to avoid grabbing an in-stem
                        # numeric prefix like "72 歲女性..." from a neighbour.
                        continue
                    m_mix = re.match(r"^(\d{1,3})\s+\S", jtext)
                    if m_mix:
                        q_num = int(m_mix.group(1))
                        if 1 <= q_num <= 250:
                            chosen = (q_num, j)
                            break
            if chosen is not None:
                q_num, j = chosen
                blocks.append((q_num, y))
                used_indices.add(j)
            continue

    # Convert to ranges with y_bottom = next marker y_top, or page bottom
    page_bottom = page.rect.height
    out: list[tuple[int, float, float]] = []
    for i, (qn, y_top) in enumerate(blocks):
        y_bot = blocks[i + 1][1] if i + 1 < len(blocks) else page_bottom
        out.append((qn, y_top, y_bot))
    return out


def _option_y_for_question(page, q_top: float, q_bot: float) -> list[tuple[str, float]]:
    """Find (A)..(E) option labels within a question's y-range on this page.
    Returns [(letter, y_top), ...] sorted by y."""
    d = page.get_text("dict")
    out: list[tuple[str, float]] = []
    for blk in d.get("blocks", []):
        if blk.get("type") != 0:
            continue
        for line in blk.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(s.get("text", "") for s in spans).strip()
            if not text:
                continue
            y = line.get("bbox", [0, 0, 0, 0])[1]
            if y < q_top or y > q_bot:
                continue
            m = re.match(r"^\(([A-E])\)", text)
            if m:
                out.append((m.group(1), y))
    out.sort(key=lambda r: r[1])
    return out


def extract_for_pdf(
    pdf_path: Path, year: int, source_id: int, s,
    skip_existing: bool = False,
) -> tuple[int, int, int, int]:
    """Extract stem images. Returns (saved, linked_stem, linked_opt, skipped).

    skip_existing: when True, questions that already have ANY image row in
    `question_images` are entirely skipped (no save, no link). Use this when
    re-running after manual image corrections to avoid re-introducing
    mis-assignments.
    """
    questions = {
        q.source_ref: q
        for q in s.execute(
            select(Question).where(
                Question.source_id == source_id, Question.year == year
            )
        ).scalars()
    }
    if not questions:
        print(f"  [warn] no DB rows for year {year}, skipping")
        return (0, 0, 0, 0)

    IMG_ROOT.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    seen_xrefs: dict[int, str] = {}  # xref → saved filename
    saved = linked_stem = linked_opt = skipped = 0

    last_q_on_prior_page: int | None = None

    for page_num, page in enumerate(doc):
        q_blocks = _question_y_blocks(page)
        if not q_blocks and last_q_on_prior_page is None:
            continue

        # Get all images on the page
        for img_index, img_info in enumerate(page.get_images(full=True)):
            xref = img_info[0]
            try:
                rects = page.get_image_rects(xref)
            except Exception:
                continue
            if not rects:
                continue
            rect = rects[0]
            if (
                rect.width < MIN_DIM_PX
                or rect.height < MIN_DIM_PX
                or rect.width * rect.height < MIN_AREA_PX
            ):
                continue

            # Find which question owns this image
            q_num: int | None = None
            for qn, y_top, y_bot in q_blocks:
                if y_top <= rect.y0 < y_bot:
                    q_num = qn
                    break
            if q_num is None:
                # Image on continuation page (no marker before image y0):
                # use last question on prior page or first on this page.
                q_num = last_q_on_prior_page or (q_blocks[0][0] if q_blocks else None)
            if q_num is None:
                skipped += 1
                continue

            ref = f"{year}/Q{q_num:03d}"
            q = questions.get(ref)
            if q is None:
                skipped += 1
                continue

            # Safety: skip Qs that already have any image rows when caller
            # asked for it. Prevents re-extraction overwriting manually-fixed
            # assignments (e.g. phase 3D 114-US fix).
            if skip_existing:
                has_any = s.execute(
                    select(QuestionImage.id).where(
                        QuestionImage.question_id == q.id
                    )
                ).first() is not None
                if has_any:
                    skipped += 1
                    continue

            # Option-letter assignment: which (A)..(E) sits immediately above
            # this image (within the question's y-range).
            opt_letter: str | None = None
            for qn, y_top, y_bot in q_blocks:
                if qn != q_num:
                    continue
                opts = _option_y_for_question(page, y_top, y_bot)
                # candidate options: those above the image
                above = [(L, y) for L, y in opts if y < rect.y0]
                if above:
                    opt_letter = above[-1][0]
                break

            # Save image (clip render to capture surrounding context)
            if xref in seen_xrefs:
                fname = seen_xrefs[xref]
            else:
                pix = page.get_pixmap(clip=rect, dpi=200, colorspace=fitz.csRGB)
                if (
                    pix.width < MIN_DIM_PX
                    or pix.height < MIN_DIM_PX
                    or pix.width * pix.height < MIN_AREA_PX
                ):
                    continue
                tag = f"opt{opt_letter}" if opt_letter else "stem"
                fname = f"q_{q.id}_{tag}_p{page_num:03d}_i{img_index}.png"
                fpath = IMG_ROOT / fname
                pix.save(fpath)
                if fpath.stat().st_size < MIN_BYTES:
                    fpath.unlink(missing_ok=True)
                    continue
                seen_xrefs[xref] = fname
                saved += 1

            # Insert / update QuestionImage row (skip if same q+filename exists)
            existing = s.execute(
                select(QuestionImage).where(
                    QuestionImage.question_id == q.id,
                    QuestionImage.filename == fname,
                )
            ).scalar_one_or_none()
            if existing is None:
                s.add(
                    QuestionImage(
                        question_id=q.id,
                        filename=fname,
                        display_order=img_index,
                        is_explanation=False,
                        option_letter=opt_letter,
                    )
                )
                if opt_letter:
                    linked_opt += 1
                else:
                    linked_stem += 1
                if not q.has_image:
                    q.has_image = True

        # Update continuation tracker
        if q_blocks:
            last_q_on_prior_page = q_blocks[-1][0]

    doc.close()
    return (saved, linked_stem, linked_opt, skipped)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--skip-existing", action="store_true",
        help="Skip questions that already have any image row (safety for "
             "re-runs after manual image corrections, e.g. phase 3D 114-US).",
    )
    args = ap.parse_args()

    total_saved = total_stem = total_opt = total_skip = 0
    with SessionLocal() as s:
        source = s.execute(
            select(Source).where(Source.code == "past_paper")
        ).scalar_one()
        for filename in QUESTION_PDFS:
            year = filename_to_year(filename)
            if year is None:
                continue
            pdf = settings.past_paper_dir / filename
            if not pdf.exists():
                print(f"  [skip] {filename}: not found")
                continue
            saved, link_stem, link_opt, skip = extract_for_pdf(
                pdf, year, source.id, s,
                skip_existing=args.skip_existing,
            )
            total_saved += saved
            total_stem += link_stem
            total_opt += link_opt
            total_skip += skip
            print(
                f"  {year}: saved={saved} stem_linked={link_stem} "
                f"opt_linked={link_opt} skipped={skip}"
            )
        s.commit()

    print(
        f"\nTotal: saved={total_saved} stem_linked={total_stem} "
        f"opt_linked={total_opt} skipped={total_skip}"
    )


if __name__ == "__main__":
    main()
