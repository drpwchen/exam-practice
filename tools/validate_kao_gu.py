"""Validate kao_gu DB integrity post-reload.

Checks per-year:
- Each year (109-114) has exactly 200 questions.
- Every question has 5 non-empty options.
- correct_letter ∈ {A,B,C,D,E}.
- Every question has at least one tag.

Run: python -m tools.validate_kao_gu
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path("data/app.db")
EXPECTED_YEARS = [109, 110, 111, 112, 113, 114]


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    src_id = cur.execute(
        "SELECT id FROM sources WHERE code='past_paper'"
    ).fetchone()[0]

    print(f"\n{'Year':<6}{'Total':<8}{'5-opts':<8}{'correct':<10}{'tagged':<8}{'images':<8}")
    print("-" * 50)

    overall_issues: list[str] = []

    for year in EXPECTED_YEARS:
        total = cur.execute(
            "SELECT COUNT(*) FROM questions WHERE source_id=? AND year=?",
            (src_id, year),
        ).fetchone()[0]

        five_opts = cur.execute(
            """
            SELECT COUNT(*) FROM questions
            WHERE source_id=? AND year=?
              AND length(option_a) > 0 AND length(option_b) > 0
              AND length(option_c) > 0 AND length(option_d) > 0
              AND length(option_e) > 0
            """,
            (src_id, year),
        ).fetchone()[0]

        with_correct = cur.execute(
            """
            SELECT COUNT(*) FROM questions
            WHERE source_id=? AND year=?
              AND correct_letter IN ('A','B','C','D','E')
            """,
            (src_id, year),
        ).fetchone()[0]

        tagged = cur.execute(
            """
            SELECT COUNT(DISTINCT q.id) FROM questions q
            JOIN question_tags qt ON qt.question_id = q.id
            WHERE q.source_id=? AND q.year=?
            """,
            (src_id, year),
        ).fetchone()[0]

        with_imgs = cur.execute(
            """
            SELECT COUNT(DISTINCT q.id) FROM questions q
            JOIN question_images qi ON qi.question_id = q.id
            WHERE q.source_id=? AND q.year=?
            """,
            (src_id, year),
        ).fetchone()[0]

        print(f"{year:<6}{total:<8}{five_opts:<8}{with_correct:<10}{tagged:<8}{with_imgs:<8}")

        if total != 200:
            overall_issues.append(f"  - year {year}: total={total} (expected 200)")
        if five_opts != total:
            overall_issues.append(
                f"  - year {year}: {total - five_opts} question(s) lack 5 non-empty options"
            )
        if with_correct != total:
            overall_issues.append(
                f"  - year {year}: {total - with_correct} question(s) lack valid correct_letter"
            )
        if tagged != total:
            overall_issues.append(
                f"  - year {year}: {total - tagged} question(s) untagged"
            )

    # Per-year stem images / explanation images breakdown
    print(f"\n{'Year':<6}{'StemImg':<10}{'ExplImg':<10}{'TotalImg':<10}")
    print("-" * 40)
    for year in EXPECTED_YEARS:
        stem_imgs = cur.execute(
            """
            SELECT COUNT(qi.id) FROM questions q
            JOIN question_images qi ON qi.question_id = q.id
            WHERE q.source_id=? AND q.year=? AND qi.is_explanation=0
            """,
            (src_id, year),
        ).fetchone()[0]
        expl_imgs = cur.execute(
            """
            SELECT COUNT(qi.id) FROM questions q
            JOIN question_images qi ON qi.question_id = q.id
            WHERE q.source_id=? AND q.year=? AND qi.is_explanation=1
            """,
            (src_id, year),
        ).fetchone()[0]
        print(f"{year:<6}{stem_imgs:<10}{expl_imgs:<10}{stem_imgs + expl_imgs:<10}")

    if overall_issues:
        print("\n⚠️  Issues found:")
        for line in overall_issues:
            print(line)
    else:
        print("\n✓ All checks passed")

    con.close()


if __name__ == "__main__":
    main()
