"""Load 考古題 question content from 題目/考題 PDFs (years 109-114).

Distinct from `pipeline.load_kao_gu` (which reads 解題 PDFs). The 解題 path is
the source of truth for explanation+reference text and correct_letter for
112-114 (already imported). This loader's job is to:

  1. ADD years 109-111 to the DB (no rows exist yet for those years).
  2. RE-EXTRACT stem + option text for 112-114 from the cleaner 題目 PDF
     source, but only when the new extraction is meaningfully different from
     the DB and the existing row is not user-reviewed.

Idempotency contract (same as load_kao_gu):
  - Match by (source_id=kao_gu, source_ref="{year}/Q{n:03d}")
  - existing.ocr_status == 'reviewed'  → SKIP entirely
  - existing absent                    → INSERT (new question)
  - else                               → UPDATE only fields that changed by
                                          more than whitespace; never change
                                          correct_letter (preserve DB value
                                          which came from 解題 = answer key)

Run: PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m pipeline.load_kao_gu_questions
"""

from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from backend.config import settings
from backend.db import SessionLocal
from backend.models import Question, Source
from pipeline.parsers.kao_gu_questions import (
    ParsedKaoGuQuestion,
    filename_to_year,
    parse_pdf,
)

# Filenames of question PDFs we know about. Order = chronological.
QUESTION_PDFS = [
    "109年題目.pdf",
    "110年題目.pdf",
    "111年題目.pdf",
    "112年題目.pdf",
    "113考題.pdf",
    "114考題.pdf",
]


def _normalize_for_compare(text: str) -> str:
    """Reduce text to a comparable form: strip whitespace + full-width punct."""
    if not text:
        return ""
    # Convert full-width to half-width for the comparison only
    t = text.translate(str.maketrans({
        "？": "?", "！": "!", "，": ",", "：": ":", "；": ";",
        "（": "(", "）": ")", "　": " ",
    }))
    return re.sub(r"\s+", "", t)


# Trailing artifacts from 解題 PDF page footers — option text often got
# stuck-together with the next page's number. Pattern: end of sentence
# (period or Chinese char) + whitespace + 1-3 digit page number.
# Restrict to "ends with substantial text + space + small int" so we don't
# misclassify list options like "1, 2, 4" or "60-70%".
_TRAILING_PAGENUM_RE = re.compile(
    r"(?:[一-鿿。．\.\)\]》】])\s+\d{1,3}\s*$"
)


def _has_trailing_pagenum(text: str | None) -> bool:
    if not text:
        return False
    return bool(_TRAILING_PAGENUM_RE.search(text))


# Detect figure-label appendix in NEW that isn't in OLD: a tail of
# whitespace-separated short Chinese labels (axis labels from a figure).
_FIG_LABEL_TAIL = re.compile(
    r"(?:[一-鿿]{1,4}|\＊|\*)(?:\s+(?:[一-鿿]{1,4}|\＊|\*))+\s*$"
)


def _looks_like_figure_label_tail_only(old: str, new: str) -> bool:
    """True if NEW = OLD + " <fig labels>" — not a real improvement."""
    norm_old = _normalize_for_compare(old)
    norm_new = _normalize_for_compare(new)
    if not norm_new.startswith(norm_old):
        return False
    extra = new[len(old):].strip() if new.startswith(old) else None
    # Robust path: look at NEW's tail directly
    m = _FIG_LABEL_TAIL.search(new)
    if not m:
        return False
    # The tail must be NEW-only content (not present in OLD)
    tail = new[m.start():].strip()
    return tail not in old


# Highlight-overlay dup patterns that the OLD (解題-derived) text often
# contains and that the IMPROVED parser drops. If OLD has these AND NEW
# doesn't, NEW is the cleaner version → replace.
_DUP_PATTERNS = [
    re.compile(r"有關\s+有關"),
    re.compile(r"關於\s+關於"),
    re.compile(r"何者\s+何者"),
    re.compile(r"下列\s+有關"),
    re.compile(r"下列\s+關於"),
    re.compile(r"下列有關\s+有關"),
    re.compile(r"下列關於\s+關於"),
    re.compile(r"敘述何者\s+敘述何者"),
    re.compile(r"何者\s+敘述"),
    # Longer Chinese-char repetition (highlight overlay, e.g. "魚際肌萎縮 那一種疾病最不可能會引起魚際肌萎縮")
    re.compile(r"([一-鿿]{4,})\s+\1"),
]


def _has_dup_artifact(text: str | None) -> bool:
    if not text:
        return False
    return any(pat.search(text) for pat in _DUP_PATTERNS)


def _should_replace(old: str | None, new: str) -> bool:
    """Conservative replace policy.

    The 題目/考題 PDFs were observed to introduce font-extraction artifacts
    (vector glyphs missed: 1↔l, m↔m̄, dropped chars in English words). The
    解題 PDF text was cleaner for 113/114. So only replace when there's clear
    evidence NEW is BETTER than OLD:

      1. OLD has a trailing page-number artifact (常見 in 解題 option_e)
         and NEW does not.
      2. NEW is meaningfully longer than OLD (suggests OLD was truncated).
      3. OLD is empty / missing.
      4. OLD has highlight-overlay dup artifacts (有關 有關, 何者 何者) that
         NEW resolved.

    Reject regressions:
      • NEW = OLD + figure-label tail (e.g. "...療法 內側 ＊ 外側")
      • NEW is shorter than OLD by >5% (truncation in NEW), unless OLD
        has dup artifacts.

    Otherwise: keep OLD.
    """
    if not new:
        return False
    if not old:
        return True
    norm_old = _normalize_for_compare(old)
    norm_new = _normalize_for_compare(new)
    if norm_old == norm_new:
        return False  # only whitespace/punct differs — no point in churning
    # REJECT: NEW just appends figure labels — not an improvement
    if _looks_like_figure_label_tail_only(old, new):
        return False
    # Rule 1: OLD ends in trailing pagenum, NEW doesn't.
    if _has_trailing_pagenum(old) and not _has_trailing_pagenum(new):
        return True
    # Rule 4: OLD has dup artifacts, NEW doesn't → NEW is the cleaner version
    if _has_dup_artifact(old) and not _has_dup_artifact(new):
        # Only replace if the substantial content is preserved: NEW must
        # contain ≥80% of NORM_OLD's unique characters (set overlap).
        old_chars = set(norm_old)
        new_chars = set(norm_new)
        if len(old_chars) == 0 or len(old_chars & new_chars) / len(old_chars) >= 0.80:
            return True
    # Rule 2: NEW is at least 5% longer (in normalized chars).
    if len(norm_new) > len(norm_old) * 1.05:
        return True
    # Rule 3: NEW has the same Chinese chars as OLD plus more (NEW is OLD-prefix-of).
    if norm_old in norm_new and len(norm_new) > len(norm_old):
        return True
    # Default: KEEP OLD (don't risk replacing clean text with garbled text).
    return False


def _diff_summary(old: str | None, new: str) -> str:
    """Short visual diff for the change log."""
    old = (old or "").strip()
    new = new.strip()
    if old == new:
        return "(identical)"
    if not old:
        return f"OLD=∅ NEW={new[:80]!r}"
    return f"OLD={old[:80]!r}  NEW={new[:80]!r}"


def upsert(s, source_id: int, year: int, pq: ParsedKaoGuQuestion,
           changes: list[str], dry_run: bool) -> str:
    """UPSERT one parsed question. Returns 'created'|'updated'|'skipped'|'noop'."""
    source_ref = f"{year}/Q{pq.number:03d}"
    existing = s.execute(
        select(Question).where(
            Question.source_id == source_id, Question.source_ref == source_ref
        )
    ).scalar_one_or_none()

    if existing is not None and existing.ocr_status == "reviewed":
        return "skipped"

    new_payload = dict(
        stem=pq.stem,
        option_a=pq.options.get("A", ""),
        option_b=pq.options.get("B", ""),
        option_c=pq.options.get("C", ""),
        option_d=pq.options.get("D", ""),
        option_e=pq.options.get("E"),
    )

    if existing is None:
        # New row (years 109-111). Insert with parser's correct_letter.
        if not new_payload["stem"] or not new_payload["option_a"]:
            return "noop"  # malformed parse, skip
        if not dry_run:
            s.add(Question(
                source_id=source_id,
                source_ref=source_ref,
                year=year,
                correct_letter=pq.correct_letter,
                ocr_confidence=1.0,
                ocr_status="auto",
                needs_review=bool(pq.warnings),
                source_page=pq.source_page,
                **new_payload,
            ))
        changes.append(
            f"  CREATED {source_ref}  letter={pq.correct_letter}\n"
            f"    stem: {new_payload['stem'][:80]!r}"
        )
        return "created"

    # Existing row (years 112-114). Only update fields that meaningfully differ
    # AND have non-empty new content.
    field_map = [
        ("stem", "stem"),
        ("option_a", "option_a"),
        ("option_b", "option_b"),
        ("option_c", "option_c"),
        ("option_d", "option_d"),
        ("option_e", "option_e"),
    ]
    updated_fields: list[tuple[str, str, str]] = []
    for parser_key, db_attr in field_map:
        new_val = new_payload[parser_key]
        if not new_val:
            continue  # never clobber existing content with empty new content
        old_val = getattr(existing, db_attr)
        if _should_replace(old_val, new_val):
            updated_fields.append((db_attr, old_val or "", new_val))
            if not dry_run:
                setattr(existing, db_attr, new_val)
    # Update source_page if missing (new info from question PDF)
    if existing.source_page is None and pq.source_page is not None:
        if not dry_run:
            existing.source_page = pq.source_page
        updated_fields.append(("source_page", "None", str(pq.source_page)))
    # Don't touch correct_letter — answer key from 解題 PDF is more authoritative

    if not updated_fields:
        return "noop"

    lines = [f"  UPDATED {source_ref}"]
    for fname, oldv, newv in updated_fields:
        lines.append(f"    {fname}: {_diff_summary(oldv, newv)}")
    changes.append("\n".join(lines))
    return "updated"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="parse only, no DB writes")
    ap.add_argument("--years", type=str, default=None,
                    help="comma-list of years to process (default: all 109-114)")
    args = ap.parse_args()

    target_years = (
        {int(y.strip()) for y in args.years.split(",") if y.strip()}
        if args.years else None
    )

    counts = {"created": 0, "updated": 0, "skipped": 0, "noop": 0}
    by_year: dict[int, dict[str, int]] = {}
    changes: list[str] = []
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    with SessionLocal() as s:
        source = s.execute(select(Source).where(Source.code == "past_paper")).scalar_one()
        for filename in QUESTION_PDFS:
            year = filename_to_year(filename)
            if year is None:
                continue
            if target_years and year not in target_years:
                continue
            pdf = settings.past_paper_dir / filename
            if not pdf.exists():
                print(f"  [skip] {filename}: not found")
                continue
            qs = parse_pdf(pdf)
            year_counts = {"created": 0, "updated": 0, "skipped": 0, "noop": 0}
            for pq in qs:
                action = upsert(s, source.id, year, pq, changes, args.dry_run)
                counts[action] += 1
                year_counts[action] += 1
            by_year[year] = year_counts
            print(
                f"  {year}: parsed={len(qs)} "
                f"created={year_counts['created']} updated={year_counts['updated']} "
                f"skipped={year_counts['skipped']} noop={year_counts['noop']}"
            )
        if not args.dry_run:
            s.commit()

    # Write change log
    log_path = Path(f"data/_kao_gu_reload_{timestamp}.md")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"# kao_gu question reload — {timestamp}\n\n"
        f"Source: 題目/考題 PDFs (109-114)\n"
        f"Mode: {'DRY-RUN' if args.dry_run else 'LIVE'}\n\n"
        f"## Summary\n\n"
    )
    summary_lines = [
        f"- Total: created={counts['created']} updated={counts['updated']} "
        f"skipped={counts['skipped']} noop={counts['noop']}\n"
    ]
    for y, yc in sorted(by_year.items()):
        summary_lines.append(
            f"- {y}: created={yc['created']} updated={yc['updated']} "
            f"skipped={yc['skipped']} noop={yc['noop']}\n"
        )
    body = "\n## Per-question changes\n\n" + "\n\n".join(changes) if changes else ""
    log_path.write_text(header + "".join(summary_lines) + body, encoding="utf-8")

    print(
        f"\nTotal: created={counts['created']} updated={counts['updated']} "
        f"skipped={counts['skipped']} noop={counts['noop']}"
    )
    print(f"Change log: {log_path}")


if __name__ == "__main__":
    main()
