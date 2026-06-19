"""Authoritative tagging of 考古題 questions from 共筆 PDFs.

Each 共筆 PDF is one specialty. At its end is a 考古題 section listing
each question by `XX 年 NN 題` (e.g. "114 年 45 題"). We:
1. Map filename → tag code (manual mapping below).
2. Parse all (year, qnum) markers from each PDF.
3. For each (year, qnum) found, replace the question's tag(s) with
   the authoritative tag from the filename.

Run: python -m pipeline.tag_kao_gu_from_gongbi
"""

import re
from pathlib import Path

import fitz
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db import SessionLocal
from backend.models import Question, QuestionTag, Source, Tag

GONGBI_DIR = Path("./source_pdfs/gongbi")

# Map (filename prefix or substring) → tag code in current 26-list
FILE_TAG_MAP: list[tuple[str, str]] = [
    ("1. Upper extremity and spinal orthosis", "AST"),
    ("4. LE orthosis", "AST"),
    ("5. UE prosthesis", "AMP_PRO"),
    ("6. LE prosthesis", "AMP_PRO"),
    ("17. Vascular", "AMP_PRO"),
    ("19. Wheelchair", "AST"),
    ("10. Sport medicine", "SM"),
    ("11. Biomechanics", "BMC"),
    ("12. Exercise Physiology", "EP"),
    ("13-1", "NMD"),                        # FES → NMD
    ("13-2", "ORR"),                        # Osteoporosis → ORR
    ("14. Rheuma", "ART"),
    ("15. TBI", "TBI"),
    ("16. TBI", "TBI"),
    ("18. Speech", "SPE"),
    ("2-1. Geriatric", "GER"),
    ("2-2. 肌少症", "GER"),
    ("2. Geriatric", "GER"),
    ("20. Neuropathy", "PN"),
    ("21-1. Cancer", "CAN"),
    ("21-2. Chronic pain", "PAIN"),
    ("22. Electrodiagnosis", "ED"),
    ("23. Cardiac", "CR"),
    ("24. Physical medicine", "PM"),
    ("25. CVA", "CVA"),
    ("26. MSK-UE", "ORR"),
    ("27. MSK LE", "ORR"),
    ("28. MSK Spine", "ORR"),
    ("29-1. SCI", "SCI"),
    ("30. SCI-2", "SCI"),
    ("3-1. Chronic wound", "RN"),
    ("3-2. Burn", "RN"),
    ("31-1 Bladder", "NB"),
    ("31-2. Bowel", "NB"),
    ("31-3. Sex", "NB"),
    ("32. Activities of daily living", "ADL"),
    ("33. Ethics", "REH"),
    ("7. Ped", "CP"),
    ("8-1. PED", "CP"),
    ("8-2. PED", "CP"),
    ("9. Pulmonary", "PR"),
]

# Markers like "114 年 45 題" / "114年45題" / "114 年45題" etc.
QMARK_RE = re.compile(r"(?:題目)?\s*(10[8-9]|11[0-5])\s*年\s*(\d{1,3})\s*題")


def filename_tag(name: str) -> str | None:
    for prefix, code in FILE_TAG_MAP:
        if name.startswith(prefix):
            return code
    return None


def parse_pdf_markers(pdf: Path) -> set[tuple[int, int]]:
    """Return set of (year, qnum) found in the PDF."""
    markers: set[tuple[int, int]] = set()
    try:
        doc = fitz.open(pdf)
    except Exception as e:
        print(f"  ! cannot open {pdf.name}: {e}")
        return markers
    for page in doc:
        text = page.get_text() or ""
        for m in QMARK_RE.finditer(text):
            year = int(m.group(1))
            qnum = int(m.group(2))
            if 1 <= qnum <= 250:
                markers.add((year, qnum))
    doc.close()
    return markers


def main() -> None:
    pdfs = sorted(GONGBI_DIR.glob("*.pdf"))
    pdfs = [p for p in pdfs if not p.name.startswith("._")]
    print(f"Found {len(pdfs)} 共筆 PDFs")

    # Collect mappings
    qref_to_tags: dict[tuple[int, int], set[str]] = {}
    no_map: list[str] = []
    for p in pdfs:
        tag_code = filename_tag(p.name)
        if not tag_code:
            no_map.append(p.name)
            continue
        marks = parse_pdf_markers(p)
        for ref in marks:
            qref_to_tags.setdefault(ref, set()).add(tag_code)
        print(f"  {p.name} → {tag_code}: {len(marks)} markers")
    if no_map:
        print(f"\n[no mapping] {len(no_map)} files: {no_map}")

    print(f"\nTotal (year, q#) markers: {len(qref_to_tags)}")

    # Apply to DB
    with SessionLocal() as s:
        kg = s.execute(select(Source).where(Source.code == "past_paper")).scalar_one()
        tag_id_by_code = {
            t.name: t.id for t in s.scalars(select(Tag).where(Tag.level == "major"))
        }
        applied = 0
        skipped = 0
        for (year, qnum), codes in qref_to_tags.items():
            ref = f"{year}/Q{qnum:03d}"
            q = s.execute(
                select(Question).where(
                    Question.source_id == kg.id, Question.source_ref == ref
                )
            ).scalar_one_or_none()
            if not q:
                skipped += 1
                continue
            # Replace classifier-assigned tags with authoritative ones
            s.execute(
                QuestionTag.__table__.delete().where(QuestionTag.question_id == q.id)
            )
            for code in codes:
                tid = tag_id_by_code.get(code)
                if tid:
                    s.add(QuestionTag(question_id=q.id, tag_id=tid))
            q.needs_review = False
            applied += 1
        s.commit()
    print(f"\nApplied tags to {applied} questions, skipped {skipped} (not found in DB)")


if __name__ == "__main__":
    main()
