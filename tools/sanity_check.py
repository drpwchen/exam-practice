"""Pure-rule sanity check for question DB. 0 LLM calls.

Surfaces hard structural bugs that LLM audits won't catch reliably:
- Missing/empty required option text (option_a-d should never be blank)
- correct_letter pointing to an empty option
- Stem too short (<10) or too long (>2000)
- Explanation present but suspiciously short (<10) — likely parser leftover
- Cross-question stem exact duplicates
- Questions without any tag
- 考古題 (past_paper) row missing year
- Long explanation collapsed to a single line — usually a parser-paragraph
  bug (multi-bullet text mashed into one paragraph)
- Topic-code prefix leaked into stem start (e.g. "PR-10 ...") — extraction
  merged the source question-number label into the stem

Writes data/_sanity_<timestamp>.md.

Run: python -m tools.sanity_check
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.db import SessionLocal  # noqa: E402
from backend.models import Question, QuestionTag, Source  # noqa: E402


def main() -> None:
    out_path = Path(f"data/_sanity_{datetime.now().strftime('%Y%m%d_%H%M')}.md")
    findings: dict[str, list[dict]] = defaultdict(list)

    with SessionLocal() as s:
        sources = {row.id: row.code for row in s.execute(select(Source)).scalars()}
        questions = s.execute(select(Question)).scalars().all()

        # tag count per question
        tag_counts: dict[int, int] = defaultdict(int)
        for qid, _tid in s.execute(
            select(QuestionTag.question_id, QuestionTag.tag_id)
        ).all():
            tag_counts[qid] += 1

        # Stem dedup: track first occurrence so we can list pairs
        stem_seen: dict[str, list[int]] = defaultdict(list)

        for q in questions:
            src = sources.get(q.source_id, f"src#{q.source_id}")

            options = {
                "A": q.option_a,
                "B": q.option_b,
                "C": q.option_c,
                "D": q.option_d,
                "E": q.option_e,
            }
            non_empty = {L: t for L, t in options.items() if t and t.strip()}
            n_opts = len(non_empty)

            # 1. Options count < 4 (true bug — parser missed options)
            if n_opts < 4:
                findings["options_too_few"].append({
                    "id": q.id, "src": src, "year": q.year,
                    "n_opts": n_opts,
                    "letters_present": "".join(sorted(non_empty.keys())),
                    "stem_preview": (q.stem or "")[:120],
                })

            # 2. correct_letter not in non-empty options
            # Multi-select stores e.g. "ACE" — check each letter.
            correct_set = set(q.correct_letter) if q.is_multi else {q.correct_letter}
            missing = [L for L in correct_set if L not in non_empty]
            if missing:
                findings["answer_not_in_options"].append({
                    "id": q.id, "src": src, "year": q.year,
                    "correct": q.correct_letter,
                    "letters_present": "".join(sorted(non_empty.keys())),
                    "missing": "".join(sorted(missing)),
                    "stem_preview": (q.stem or "")[:120],
                })

            # 3. Stem too short / too long
            stem_len = len(q.stem or "")
            if stem_len < 10:
                findings["stem_too_short"].append({
                    "id": q.id, "src": src, "year": q.year,
                    "len": stem_len, "stem": q.stem or "<empty>",
                })
            elif stem_len > 2000:
                findings["stem_too_long"].append({
                    "id": q.id, "src": src, "year": q.year,
                    "len": stem_len, "stem_preview": (q.stem or "")[:200] + "...",
                })

            # 4. explanation present but suspiciously short
            if q.explanation is not None:
                expl = q.explanation.strip()
                if 0 < len(expl) < 10:
                    findings["explanation_suspiciously_short"].append({
                        "id": q.id, "src": src, "year": q.year,
                        "len": len(expl), "explanation": expl,
                    })
                # 4b. long explanation that ALSO contains structural markers
                # (multiple option labels, multiple bullets, etc.) but NO
                # paragraph break — that's a parser regression. Plain
                # single-paragraph prose is legitimate, don't flag.
                if len(expl) > 200 and "\n" not in expl:
                    n_opt_labels = len(re.findall(r"\([A-E]\)", expl))
                    n_bullets = len(re.findall(r"[●•‣◆▪▫]", expl))
                    if n_opt_labels >= 2 or n_bullets >= 2:
                        findings["explanation_no_paragraphs"].append({
                            "id": q.id, "src": src, "year": q.year,
                            "len": len(expl),
                            "n_markers": n_opt_labels + n_bullets,
                            "preview": expl[:160] + "...",
                        })

            # 4c. stem on a single line but contains multi-period structure
            # → likely missing line breaks between sentences. Plain
            # single-paragraph case-style stems are legit.
            stem_text = q.stem or ""
            if stem_len > 200 and "\n" not in stem_text:
                if stem_text.count("。") >= 4 or stem_text.count(".") >= 4:
                    findings["stem_no_paragraphs"].append({
                        "id": q.id, "src": src, "year": q.year,
                        "len": stem_len, "preview": stem_text[:160] + "...",
                    })

            # 5. Long-tail: option text > 2000 chars (likely contains adjacent question text)
            for L, t in non_empty.items():
                if len(t) > 2000:
                    findings["option_too_long"].append({
                        "id": q.id, "src": src, "year": q.year,
                        "letter": L, "len": len(t),
                        "preview": t[:200] + "...",
                    })

            # 6. No tags
            if tag_counts.get(q.id, 0) == 0:
                findings["no_tags"].append({
                    "id": q.id, "src": src, "year": q.year,
                    "stem_preview": (q.stem or "")[:120],
                })

            # 7. past_paper without year
            if src == "past_paper" and q.year is None:
                findings["past_paper_no_year"].append({
                    "id": q.id, "src": src, "source_ref": q.source_ref,
                    "stem_preview": (q.stem or "")[:120],
                })

            # 8. reference_page suspiciously long — usually means explanation
            # leaked into the reference field.
            if q.reference_page and len(q.reference_page) > 500:
                findings["reference_too_long"].append({
                    "id": q.id, "src": src, "year": q.year,
                    "len": len(q.reference_page),
                    "preview": q.reference_page[:160] + "...",
                })

            # 9. Explanation contains canned boilerplate but nothing else.
            # The canned template should have been stripped by the parser.
            if q.explanation and len(q.explanation) < 200:
                if re.search(r"附[^)]*?(請畫底加標選項|建議從建議教科書|資料\s*[\.。])", q.explanation):
                    findings["explanation_canned_only"].append({
                        "id": q.id, "src": src, "year": q.year,
                        "preview": q.explanation[:160],
                    })

            # 10. explanation labels (A) (B) (C) (D) (E) all present but at least
            # one has no per-option text after it (Q#297-pattern but tighter:
            # only flag when 3+ option labels are present-but-empty).
            # SKIP free-text-style explanations (Q#67/Q#110): labels there
            # are intentionally grouping markers, not per-option separators,
            # so "empty between labels" is the desired layout, not a finding.
            if q.explanation and len(q.explanation) > 30:
                expl = q.explanation
                # Free-text detection: bullets present AND <2 labels with
                # substantial inline content. Mirrors parser detection.
                has_bullets = bool(
                    re.search(r"(?:^|\n)\s*[-－–—●•‣◆▪▫](?:\s|$)", expl)
                )
                inline_content = sum(
                    1 for L in "ABCDE"
                    if re.search(r"\(" + L + r"\)[ \t]*[^\n(]{4,}", expl)
                )
                is_freetext = has_bullets and inline_content < 2
                if not is_freetext:
                    opt_labels = list(re.finditer(r"\(([A-E])\)", expl))
                    if len(opt_labels) >= 4:
                        empty_count = 0
                        for i, m in enumerate(opt_labels):
                            end = opt_labels[i + 1].start() if i + 1 < len(opt_labels) else len(expl)
                            between = expl[m.end():end].strip()
                            if len(re.sub(r"\s", "", between)) < 5:
                                empty_count += 1
                        if empty_count >= 3:
                            findings["explanation_options_empty"].append({
                                "id": q.id, "src": src, "year": q.year,
                                "empty_count": empty_count,
                                "preview": expl[:160],
                            })

            # 11. Topic-code prefix leaked into stem start. The source PDF's
            # question-number label (e.g. "PR-10") sometimes gets merged into
            # the stem during extraction. Detect by matching the topic code
            # parsed from source_ref against the stem's leading token, so real
            # spinal-level tokens (C6/T4/L1) are never flagged.
            topic_code = None
            m_code = re.search(r"[._]([A-Z]{2,4})[._題]", q.source_ref or "") or \
                re.match(r"\d+\.([A-Z]{2,4})", q.source_ref or "")
            if m_code:
                topic_code = m_code.group(1)
            if topic_code and re.match(
                rf"^\s*{topic_code}\s*[-－]?\s*\d", q.stem or ""
            ):
                leak = re.match(
                    rf"^\s*{topic_code}\s*[-－]?\s*\d+", q.stem or ""
                ).group(0)
                findings["stem_code_prefix_leak"].append({
                    "id": q.id, "src": src, "year": q.year,
                    "code": topic_code, "leak": leak.strip(),
                    "stem_preview": (q.stem or "")[:120],
                })

            # Track for dedup
            normalized = (q.stem or "").strip()
            if normalized:
                stem_seen[normalized].append(q.id)

        # 8. Duplicate stems
        for stem, ids in stem_seen.items():
            if len(ids) > 1:
                findings["duplicate_stem_exact"].append({
                    "ids": ids,
                    "stem_preview": stem[:160],
                })

    # Write report
    out_path.parent.mkdir(parents=True, exist_ok=True)
    total = sum(len(v) for v in findings.values())
    with out_path.open("w", encoding="utf-8") as f:
        f.write(f"# Sanity check — {datetime.now().isoformat(timespec='seconds')}\n\n")
        f.write(f"Total questions scanned: {len(questions)}\n\n")
        f.write(f"**Total findings: {total}**\n\n")
        f.write("Categories:\n")
        for cat, items in sorted(findings.items()):
            f.write(f"- `{cat}`: {len(items)}\n")
        f.write("\n---\n\n")

        for cat in sorted(findings.keys()):
            items = findings[cat]
            f.write(f"## {cat} ({len(items)})\n\n")
            for item in items:
                if cat == "duplicate_stem_exact":
                    f.write(
                        f"- **Q#{item['ids']}**: `{item['stem_preview']}`\n"
                    )
                elif cat == "options_too_few":
                    f.write(
                        f"- **Q#{item['id']}** ({item['src']} {item['year']}) — "
                        f"{item['n_opts']} options ({item['letters_present']}): "
                        f"`{item['stem_preview']}`\n"
                    )
                elif cat == "answer_not_in_options":
                    f.write(
                        f"- **Q#{item['id']}** ({item['src']} {item['year']}) — "
                        f"correct={item['correct']} but only {item['letters_present']} present: "
                        f"`{item['stem_preview']}`\n"
                    )
                elif cat == "stem_too_short":
                    f.write(
                        f"- **Q#{item['id']}** ({item['src']} {item['year']}) — "
                        f"len={item['len']}: `{item['stem']}`\n"
                    )
                elif cat == "stem_too_long":
                    f.write(
                        f"- **Q#{item['id']}** ({item['src']} {item['year']}) — "
                        f"len={item['len']}: `{item['stem_preview']}`\n"
                    )
                elif cat == "explanation_suspiciously_short":
                    f.write(
                        f"- **Q#{item['id']}** ({item['src']} {item['year']}) — "
                        f"len={item['len']}: `{item['explanation']}`\n"
                    )
                elif cat == "option_too_long":
                    f.write(
                        f"- **Q#{item['id']}** ({item['src']} {item['year']}) — "
                        f"option {item['letter']} len={item['len']}: `{item['preview']}`\n"
                    )
                elif cat == "no_tags":
                    f.write(
                        f"- **Q#{item['id']}** ({item['src']} {item['year']}): "
                        f"`{item['stem_preview']}`\n"
                    )
                elif cat == "past_paper_no_year":
                    f.write(
                        f"- **Q#{item['id']}** ({item['source_ref']}): "
                        f"`{item['stem_preview']}`\n"
                    )
                elif cat in ("explanation_no_paragraphs", "stem_no_paragraphs",
                             "reference_too_long"):
                    f.write(
                        f"- **Q#{item['id']}** ({item['src']} {item['year']}) — "
                        f"len={item['len']}: `{item['preview']}`\n"
                    )
                elif cat == "explanation_canned_only":
                    f.write(
                        f"- **Q#{item['id']}** ({item['src']} {item['year']}): "
                        f"`{item['preview']}`\n"
                    )
                elif cat == "stem_code_prefix_leak":
                    f.write(
                        f"- **Q#{item['id']}** ({item['src']} {item['year']}) — "
                        f"leaked prefix `{item['leak']}` (code {item['code']}): "
                        f"`{item['stem_preview']}`\n"
                    )
                elif cat == "explanation_options_empty":
                    f.write(
                        f"- **Q#{item['id']}** ({item['src']} {item['year']}) — "
                        f"{item['empty_count']} options have empty explanation: "
                        f"`{item['preview']}`\n"
                    )
            f.write("\n")

    print(f"Report: {out_path}")
    print(f"Total findings: {total}")
    for cat, items in sorted(findings.items()):
        print(f"  {cat}: {len(items)}")


if __name__ == "__main__":
    main()
