"""Extract images from 考古題 解題 PDFs and attach to questions.

All images from 解題 PDFs are treated as explanation images (the 解題 file IS
the answer key with explanations). Question association is done by tracking
the most recent `題號 NNN` marker seen.
"""

import re
from pathlib import Path

import fitz
from sqlalchemy import select

from backend.config import settings
from backend.db import SessionLocal
from backend.models import Question, QuestionImage, Source

IMG_ROOT = Path("data/question_images")
MIN_DIM_PX = 40
MIN_AREA_PX = 8000
MIN_BYTES = 3000
QUESTION_MARKER = re.compile(r"題\s*號\s*0?0?(\d+)")


def extract_for_year(pdf_path: Path, year: int, source_id: int, s) -> tuple[int, int]:
    questions = {
        q.source_ref: q
        for q in s.execute(
            select(Question).where(
                Question.source_id == source_id, Question.year == year
            )
        ).scalars()
    }
    if not questions:
        return (0, 0)

    IMG_ROOT.mkdir(parents=True, exist_ok=True)
    extracted = linked = 0
    doc = fitz.open(pdf_path)
    seen_xrefs: dict[int, str] = {}
    current_q_number: int | None = None

    for page_num, page in enumerate(doc):
        # Update current_q_number if a new 題號 appears on this page
        text = page.get_text() or ""
        markers = list(QUESTION_MARKER.finditer(text))
        if markers:
            current_q_number = int(markers[-1].group(1))  # last marker on page

        if current_q_number is None:
            continue
        ref = f"{year}/Q{current_q_number:03d}"
        q = questions.get(ref)
        if q is None:
            continue

        for img_index, img_info in enumerate(page.get_images(full=True)):
            xref = img_info[0]
            try:
                rects = page.get_image_rects(xref)
            except Exception:
                continue
            if not rects:
                continue
            rect = rects[0]
            if (rect.width < MIN_DIM_PX or rect.height < MIN_DIM_PX
                or rect.width * rect.height < MIN_AREA_PX):
                continue

            if xref in seen_xrefs:
                fname = seen_xrefs[xref]
            else:
                pix = page.get_pixmap(clip=rect, dpi=200, colorspace=fitz.csRGB)
                if (pix.width < MIN_DIM_PX or pix.height < MIN_DIM_PX
                    or pix.width * pix.height < MIN_AREA_PX):
                    continue
                fname = f"q_{q.id}_p{page_num:03d}_i{img_index}.png"
                fpath = IMG_ROOT / fname
                pix.save(fpath)
                pix = None
                if fpath.stat().st_size < MIN_BYTES:
                    fpath.unlink(missing_ok=True)
                    continue
                seen_xrefs[xref] = fname
                extracted += 1

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
                        is_explanation=True,
                    )
                )
                linked += 1
                if not q.has_image:
                    q.has_image = True

    doc.close()
    return (extracted, linked)


def main() -> None:
    total_ext = total_link = 0
    with SessionLocal() as s:
        source = s.execute(select(Source).where(Source.code == "past_paper")).scalar_one()
        for year in settings.past_paper_years_list:
            pdf = settings.past_paper_dir / f"{year}年解題.pdf"
            if not pdf.exists():
                continue
            ext, link = extract_for_year(pdf, year, source.id, s)
            total_ext += ext
            total_link += link
            print(f"  {year}: extracted={ext} linked={link}")
        s.commit()
    print(f"\nTotal: extracted={total_ext} linked={total_link}")


if __name__ == "__main__":
    main()
