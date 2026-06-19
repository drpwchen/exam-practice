"""Load 考古題 questions to DB. Run: python -m pipeline.load_kao_gu"""

from pathlib import Path

from sqlalchemy import select

from backend.config import settings
from backend.db import SessionLocal
from backend.models import Question, Source
from pipeline.parsers.kao_gu import parse_pdf


def upsert(s, source_id: int, source_ref: str, year: int, pq) -> str:
    existing = s.execute(
        select(Question).where(
            Question.source_id == source_id, Question.source_ref == source_ref
        )
    ).scalar_one_or_none()
    payload = dict(
        year=year,
        stem=pq.stem,
        option_a=pq.options.get("A", ""),
        option_b=pq.options.get("B", ""),
        option_c=pq.options.get("C", ""),
        option_d=pq.options.get("D", ""),
        option_e=pq.options.get("E"),
        correct_letter=pq.correct_letter,
        explanation=pq.explanation,
        reference_page=pq.reference,
        source_page=pq.source_page,
        ocr_confidence=1.0,
        ocr_status="auto",
        needs_review=bool(pq.warnings),
    )
    if existing is None:
        s.add(Question(source_id=source_id, source_ref=source_ref, **payload))
        return "created"
    if existing.ocr_status == "reviewed":
        return "skipped"
    for k, v in payload.items():
        setattr(existing, k, v)
    return "updated"


def main() -> None:
    counts = {"created": 0, "updated": 0, "skipped": 0}
    by_year: dict[int, int] = {}
    with SessionLocal() as s:
        source = s.execute(select(Source).where(Source.code == "past_paper")).scalar_one()
        for year in settings.past_paper_years_list:
            pdf = settings.past_paper_dir / f"{year}年解題.pdf"
            if not pdf.exists():
                print(f"  [skip] {pdf.name}: not found")
                continue
            qs = parse_pdf(pdf)
            ok = 0
            for pq in qs:
                source_ref = f"{year}/Q{pq.number:03d}"
                action = upsert(s, source.id, source_ref, year, pq)
                counts[action] += 1
                ok += 1
            by_year[year] = ok
            print(f"  {year}年: {ok} questions parsed")
        s.commit()

    print(f"\nTotal: created={counts['created']} updated={counts['updated']} "
          f"skipped={counts['skipped']}")
    print(f"By year: {by_year}")


if __name__ == "__main__":
    main()
