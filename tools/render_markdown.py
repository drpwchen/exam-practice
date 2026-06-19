"""Render a PDF page as markdown, preserving styling.

Sources of truth (per page):
- fitz get_text("rawdict") spans → text content + bbox + RGB color + font flags
- fitz get_drawings() → fill rects (gray bg), thin rects (underlines)
- (optional) Tesseract .tess.tsv → text + bbox for vector-glyph pages where
  fitz text extraction fails. fitz styling metadata (drawings) is still
  trustworthy on these pages, only the text stream is broken.

Markdown mapping (Obsidian-flavoured conventions):
- gray fill bg around span(s)        → `> [!note]` callout (or single-line `>`)
- non-black span color (red dominant) → `==highlight==`
- thin black rect just under a span   → `<u>...</u>` (markdown has no underline)
- bold/italic font flags              → `**...**` / `*...*`
- left x indent vs paragraph baseline → list-item nesting (` `, `  `, `   ` prefix)

CLI:
    python -m tools.render_markdown <pdf-path> <page-index> [--out PATH]
                                                            [--tess TSV]
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import fitz

# ---- styling thresholds ----
RED_R_MIN = 180
RED_GB_MAX = 80
GRAY_FILL_LO = 0.78  # 0.85 = our highlight; 0.91 = table cell sidebar (excluded)
GRAY_FILL_HI = 0.90
UNDERLINE_HEIGHT_MAX = 2.0      # px
UNDERLINE_Y_GAP_MAX = 4.0        # px below span bottom (table dividers usually further)
UNDERLINE_OVERLAP_MIN = 0.7     # underline rect must cover ≥70% of span width
UNDERLINE_WIDTH_MAX = 0.7       # underline narrower than 70% of page text width
LINE_Y_TOL = 6.0                 # px tolerance for grouping spans into a visual line
INDENT_MIN_PX = 30.0             # ignore column-offsets; only treat ≥30px as indent


@dataclass
class Span:
    text: str
    bbox: tuple[float, float, float, float]   # x0, y0, x1, y1
    color_int: int
    font: str
    flags: int

    @property
    def color_rgb(self) -> tuple[int, int, int]:
        c = self.color_int
        return ((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF)

    @property
    def is_red(self) -> bool:
        r, g, b = self.color_rgb
        return r >= RED_R_MIN and g <= RED_GB_MAX and b <= RED_GB_MAX

    @property
    def is_bold(self) -> bool:
        return bool(self.flags & 16)

    @property
    def is_italic(self) -> bool:
        return bool(self.flags & 2)


@dataclass
class Fill:
    bbox: tuple[float, float, float, float]
    rgb: tuple[float, float, float]

    @property
    def is_gray_highlight(self) -> bool:
        r, g, b = self.rgb
        if not (GRAY_FILL_LO <= r <= GRAY_FILL_HI):
            return False
        # near-equal RGB (gray)
        return abs(r - g) < 0.02 and abs(g - b) < 0.02

    @property
    def is_thin_dark(self) -> bool:
        x0, y0, x1, y1 = self.bbox
        return (y1 - y0) <= UNDERLINE_HEIGHT_MAX and sum(self.rgb) < 0.3


def _spans_for_page(page: fitz.Page) -> list[Span]:
    spans: list[Span] = []
    for blk in page.get_text("rawdict").get("blocks", []):
        if blk.get("type") != 0:
            continue
        for line in blk.get("lines", []):
            for sp in line.get("spans", []):
                text = "".join(ch.get("c", "") for ch in sp.get("chars", []))
                if not text.strip():
                    continue
                spans.append(Span(
                    text=text,
                    bbox=tuple(sp["bbox"]),
                    color_int=sp.get("color", 0),
                    font=sp.get("font", ""),
                    flags=sp.get("flags", 0),
                ))
    spans.sort(key=lambda s: (round(s.bbox[1], 0), s.bbox[0]))
    return spans


def _fills_for_page(page: fitz.Page) -> list[Fill]:
    fills: list[Fill] = []
    for d in page.get_drawings():
        if d.get("type") != "f":
            continue
        rect = d.get("rect")
        if rect is None:
            continue
        rgb = d.get("fill") or (0, 0, 0)
        fills.append(Fill(bbox=tuple(rect), rgb=tuple(rgb)))
    return fills


def _bbox_contains(outer, inner, tol: float = 1.0) -> bool:
    ox0, oy0, ox1, oy1 = outer
    ix0, iy0, ix1, iy1 = inner
    return (ix0 >= ox0 - tol and iy0 >= oy0 - tol
            and ix1 <= ox1 + tol and iy1 <= oy1 + tol)


def _underline_for_span(span: Span, fills: list[Fill],
                         page_text_width: float) -> bool:
    sx0, _, sx1, sy1 = span.bbox
    span_w = sx1 - sx0
    for f in fills:
        if not f.is_thin_dark:
            continue
        fx0, fy0, fx1, fy1 = f.bbox
        # underline must be just below the span, narrow band
        if not (0 < fy0 - sy1 < UNDERLINE_Y_GAP_MAX):
            continue
        # exclude full-width separator/divider lines (table borders span page width)
        rect_w = fx1 - fx0
        if rect_w > page_text_width * UNDERLINE_WIDTH_MAX:
            continue
        # require underline rect to cover most of span horizontally
        ov = min(sx1, fx1) - max(sx0, fx0)
        if span_w > 0 and ov / span_w >= UNDERLINE_OVERLAP_MIN:
            return True
    return False


def _gray_for_span(span: Span, fills: list[Fill]) -> Fill | None:
    for f in fills:
        if not f.is_gray_highlight:
            continue
        if _bbox_contains(f.bbox, span.bbox, tol=2.0):
            return f
    return None


def _wrap(text: str, span: Span, fills: list[Fill],
          page_text_width: float) -> str:
    out = text
    if span.is_red:
        out = f"=={out}=="
    if span.is_bold:
        out = f"**{out}**"
    if span.is_italic:
        out = f"*{out}*"
    if _underline_for_span(span, fills, page_text_width):
        out = f"<u>{out}</u>"
    return out


def _group_lines(spans: list[Span]) -> list[list[Span]]:
    """Group spans into visual lines by y-overlap (LINE_Y_TOL)."""
    lines: list[list[Span]] = []
    cur: list[Span] = []
    cur_y: float | None = None
    for sp in spans:
        if cur_y is None or abs(sp.bbox[1] - cur_y) <= LINE_Y_TOL:
            cur.append(sp)
            if cur_y is None:
                cur_y = sp.bbox[1]
        else:
            lines.append(cur)
            cur = [sp]
            cur_y = sp.bbox[1]
    if cur:
        lines.append(cur)
    return lines


def page_to_markdown(page: fitz.Page) -> str:
    spans = _spans_for_page(page)
    if not spans:
        return ""
    fills = _fills_for_page(page)

    page_text_width = page.rect.width  # for underline width filter

    lines = _group_lines(spans)

    # Indent baseline: take 25th percentile of line x0 (avoids outliers from
    # column gutters but captures typical paragraph start).
    line_x0s = sorted(min(s.bbox[0] for s in line) for line in lines)
    base_x = line_x0s[len(line_x0s) // 4] if line_x0s else 0

    out: list[str] = []
    prev_in_blockquote = False
    for line in lines:
        line.sort(key=lambda s: s.bbox[0])
        text_parts: list[str] = []
        in_gray = False
        for sp in line:
            if _gray_for_span(sp, fills):
                in_gray = True
            text_parts.append(_wrap(sp.text, sp, fills, page_text_width))
        line_text = "".join(text_parts).rstrip()

        # Indentation: only count ≥INDENT_MIN_PX past baseline; one level per ~30px
        line_x = min(s.bbox[0] for s in line)
        delta = max(0.0, line_x - base_x)
        indent = int(delta / INDENT_MIN_PX) if delta >= INDENT_MIN_PX else 0
        prefix = "  " * indent

        if in_gray:
            out.append(f"> {prefix}{line_text}")
            prev_in_blockquote = True
        else:
            if prev_in_blockquote:
                out.append("")
            out.append(f"{prefix}{line_text}" if line_text else "")
            prev_in_blockquote = False

    return "\n".join(out).rstrip() + "\n"


# ─────────────────────── Tesseract overlay path ──────────────────────────
# For vector-glyph PDFs where fitz can't extract text, use Tesseract output
# as the text source while keeping fitz drawings for styling metadata.

# Tesseract red detection: sample the rendered pixel at each word's bbox
# center. Cheaper than per-pixel scan; words are short enough that the
# center is dominated by glyph ink (or background between glyphs).
TESS_RED_R_MIN = 160
TESS_RED_GB_MAX = 90
TESS_MIN_CONF = 30  # drop garbage detections


def _tess_words(tsv_path: Path, dpi: int) -> list[dict]:
    """Parse tesseract image_to_data TSV → list of word dicts in PDF points.

    Each dict: {text, bbox=(x0,y0,x1,y1), conf, line_id}
    Coords are converted from raster pixels at `dpi` to PDF points (72dpi base).
    """
    scale = 72.0 / dpi
    out: list[dict] = []
    with tsv_path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            try:
                level = int(row["level"])
            except (KeyError, ValueError):
                continue
            if level != 5:  # word level
                continue
            text = (row.get("text") or "").strip()
            if not text:
                continue
            try:
                conf = int(float(row.get("conf", "-1")))
            except ValueError:
                conf = -1
            if conf >= 0 and conf < TESS_MIN_CONF:
                continue
            try:
                left = int(row["left"]); top = int(row["top"])
                w = int(row["width"]); h = int(row["height"])
            except (KeyError, ValueError):
                continue
            x0 = left * scale
            y0 = top * scale
            x1 = (left + w) * scale
            y1 = (top + h) * scale
            line_id = (row.get("block_num"), row.get("par_num"),
                       row.get("line_num"))
            out.append({
                "text": text, "bbox": (x0, y0, x1, y1),
                "conf": conf, "line_id": line_id,
                "px_bbox": (left, top, left + w, top + h),
            })
    return out


def _word_pixel_color(pix: fitz.Pixmap, px_bbox: tuple[int, int, int, int]
                      ) -> tuple[int, int, int]:
    """Sample center pixel of a word bbox in the rendered page pixmap.

    Returns (r,g,b) of the darkest pixel found in a small center crop —
    finds glyph ink when the center happens to land between characters.
    """
    x0, y0, x1, y1 = px_bbox
    cx = (x0 + x1) // 2
    cy = (y0 + y1) // 2
    # Small crop around center to find an inked pixel.
    radius = 3
    best_sum = 255 * 3
    best_rgb = (0, 0, 0)
    n = pix.n
    samples = pix.samples
    width = pix.width
    height = pix.height
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            x = max(0, min(width - 1, cx + dx))
            y = max(0, min(height - 1, cy + dy))
            idx = (y * width + x) * n
            r = samples[idx]
            g = samples[idx + 1] if n >= 3 else r
            b = samples[idx + 2] if n >= 3 else r
            s = r + g + b
            if s < best_sum:
                best_sum = s
                best_rgb = (r, g, b)
    return best_rgb


def _word_is_red(rgb: tuple[int, int, int]) -> bool:
    r, g, b = rgb
    return r >= TESS_RED_R_MIN and g <= TESS_RED_GB_MAX and b <= TESS_RED_GB_MAX


def _bbox_overlap_y(a: tuple[float, float, float, float],
                    b: tuple[float, float, float, float]) -> float:
    return max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _underline_for_bbox(bbox: tuple[float, float, float, float],
                        fills: list[Fill],
                        page_text_width: float) -> bool:
    """Same logic as _underline_for_span, but takes a raw bbox (no Span)."""
    sx0, _, sx1, sy1 = bbox
    span_w = sx1 - sx0
    for f in fills:
        if not f.is_thin_dark:
            continue
        fx0, fy0, fx1, fy1 = f.bbox
        if not (0 < fy0 - sy1 < UNDERLINE_Y_GAP_MAX):
            continue
        rect_w = fx1 - fx0
        if rect_w > page_text_width * UNDERLINE_WIDTH_MAX:
            continue
        ov = min(sx1, fx1) - max(sx0, fx0)
        if span_w > 0 and ov / span_w >= UNDERLINE_OVERLAP_MIN:
            return True
    return False


def _gray_for_bbox(bbox: tuple[float, float, float, float],
                   fills: list[Fill]) -> Fill | None:
    for f in fills:
        if not f.is_gray_highlight:
            continue
        if _bbox_contains(f.bbox, bbox, tol=2.0):
            return f
    return None


def page_to_markdown_with_tess(page: fitz.Page, tess_tsv: Path,
                                dpi: int = 300) -> str:
    """Tesseract-text + fitz-styling combined markdown.

    Use when fitz text extraction is unreliable (vector-glyph pages). The
    fitz page object is still needed for `get_drawings()` (gray fills,
    underlines) and rendering for color sampling.
    """
    words = _tess_words(tess_tsv, dpi)
    if not words:
        return ""

    fills = _fills_for_page(page)
    page_text_width = page.rect.width
    pix = page.get_pixmap(dpi=dpi)

    # Group words into visual lines by tesseract's line_id (more reliable
    # than y-tolerance because tesseract already did line segmentation).
    from collections import defaultdict
    lines_by_id: dict[tuple, list[dict]] = defaultdict(list)
    for w in words:
        lines_by_id[w["line_id"]].append(w)
    lines = []
    for ws in lines_by_id.values():
        ws.sort(key=lambda w: w["bbox"][0])
        lines.append(ws)
    # Sort lines by y0 of first word
    lines.sort(key=lambda ws: ws[0]["bbox"][1])

    line_x0s = sorted(min(w["bbox"][0] for w in line) for line in lines)
    base_x = line_x0s[len(line_x0s) // 4] if line_x0s else 0

    out: list[str] = []
    prev_in_blockquote = False
    for line in lines:
        text_parts: list[str] = []
        in_gray = False
        for w in line:
            txt = w["text"]
            rgb = _word_pixel_color(pix, w["px_bbox"])
            if _word_is_red(rgb):
                txt = f"=={txt}=="
            if _underline_for_bbox(w["bbox"], fills, page_text_width):
                txt = f"<u>{txt}</u>"
            if _gray_for_bbox(w["bbox"], fills):
                in_gray = True
            text_parts.append(txt)
        line_text = " ".join(text_parts).rstrip()
        # Tesseract over-segments CJK; collapse "X Y" → "XY" when both are
        # single CJK chars (heuristic — same line, no Latin context).
        line_text = _join_cjk_runs(line_text)

        line_x = min(w["bbox"][0] for w in line)
        delta = max(0.0, line_x - base_x)
        indent = int(delta / INDENT_MIN_PX) if delta >= INDENT_MIN_PX else 0
        prefix = "  " * indent

        if in_gray:
            out.append(f"> {prefix}{line_text}")
            prev_in_blockquote = True
        else:
            if prev_in_blockquote:
                out.append("")
            out.append(f"{prefix}{line_text}" if line_text else "")
            prev_in_blockquote = False

    return "\n".join(out).rstrip() + "\n"


def _join_cjk_runs(s: str) -> str:
    """Collapse "中 文 字" → "中文字". Tesseract chi_tra inserts a space
    between every CJK character; this restores natural spacing.
    """
    out: list[str] = []
    i = 0
    while i < len(s):
        ch = s[i]
        if _is_cjk(ch):
            out.append(ch)
            i += 1
            # eat space-separated CJK run
            while i + 1 < len(s) and s[i] == " " and _is_cjk(s[i + 1]):
                out.append(s[i + 1])
                i += 2
            # keep trailing space if next char is non-CJK
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _is_cjk(ch: str) -> bool:
    if not ch:
        return False
    o = ord(ch)
    return (0x3400 <= o <= 0x9FFF) or (0xF900 <= o <= 0xFAFF) \
        or (0x3000 <= o <= 0x303F)  # CJK punct


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("page", type=int)
    ap.add_argument("--out", default=None)
    ap.add_argument("--tess", default=None,
                    help="Path to .tess.tsv. When set, uses tesseract+fitz "
                         "combined extractor instead of fitz-only.")
    ap.add_argument("--dpi", type=int, default=300,
                    help="DPI of the rendered raster the .tsv was produced "
                         "from. Default 300 (matches ocr_pages_tesseract.py).")
    args = ap.parse_args()

    doc = fitz.open(args.pdf)
    if args.tess:
        md = page_to_markdown_with_tess(doc[args.page], Path(args.tess),
                                         dpi=args.dpi)
    else:
        md = page_to_markdown(doc[args.page])
    doc.close()
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"Wrote {args.out}: {len(md)} chars")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(md)


if __name__ == "__main__":
    main()
