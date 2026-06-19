"""Build demo image assets for the public sample dataset.

Generates, under demo/images/:
  * expl_NN.png  — a clean "PDF-style" page image of each rewritten explanation.
    The app shows explanations as page screenshots (image-based, OCR-error-free
    by design); for the public sample we render our OWN explanation text into a
    document-style PNG. No textbook screenshots are used.
  * cmap_demo.png — a fully synthetic nerve-conduction (CMAP) waveform schematic
    used by one synthetic image-question, so the image feature can be showcased
    without republishing any copyrighted figure.

Run:  python demo/build_demo_assets.py
Pure stdlib + Pillow. CJK font auto-detected (Microsoft JhengHei on Windows,
Noto/Droid fallbacks elsewhere).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
IMG_DIR = HERE / "images"
IMG_DIR.mkdir(parents=True, exist_ok=True)

# ---- fonts -----------------------------------------------------------------
_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msjh.ttc",        # Microsoft JhengHei (Win, 繁中)
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]
_FONT_BOLD_CANDIDATES = [
    "C:/Windows/Fonts/msjhbd.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
]


def _find(cands: list[str]) -> str | None:
    for c in cands:
        if os.path.exists(c):
            return c
    return None


_REG = _find(_FONT_CANDIDATES)
_BLD = _find(_FONT_BOLD_CANDIDATES) or _REG
if _REG is None:
    raise SystemExit("No CJK font found; install Noto Sans CJK or run on Windows.")


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_BLD if bold else _REG, size)


# ---- layout constants ------------------------------------------------------
W = 880
PAD = 48
INK = (33, 37, 41)
MUTED = (108, 117, 125)
ACCENT = (13, 110, 253)
GOOD = (25, 135, 84)
RULE = (222, 226, 230)
HILITE = (255, 243, 176)


def wrap(text: str, fnt: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    """Character-level wrap that works for CJK (no spaces) and Latin."""
    lines: list[str] = []
    for para in text.split("\n"):
        if not para:
            lines.append("")
            continue
        cur = ""
        for ch in para:
            if fnt.getlength(cur + ch) <= max_w:
                cur += ch
            else:
                lines.append(cur)
                cur = ch
        lines.append(cur)
    return lines


def _line_h(fnt) -> int:
    return int(fnt.size * 1.5)


def _render_page(blocks: list[tuple], out: Path) -> None:
    """Render a list of (kind, lines, font, color) blocks to a PNG page."""
    # measure height
    y = PAD
    for kind, lines, fnt, _c in blocks:
        if kind == "rule":
            y += 18
            continue
        if kind in ("lbl", "h1"):
            y += 10
        for _ln in lines:
            y += _line_h(fnt)
        if kind in ("lbl", "h1", "meta"):
            y += 4
        if kind == "opt":
            y += 2
    H = y + PAD

    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([6, 6, W - 7, H - 7], outline=RULE, width=2)   # subtle page frame

    y = PAD
    for kind, lines, fnt, color in blocks:
        if kind == "rule":
            d.line([PAD, y + 4, W - PAD, y + 4], fill=RULE, width=1)
            y += 18
            continue
        if kind in ("lbl", "h1"):
            y += 10
        for ln in lines:
            if kind == "lbl" and ln in ("題目", "詳解"):
                d.rectangle([PAD - 12, y + 3, PAD - 6, y + fnt.size], fill=ACCENT)
            d.text((PAD, y), ln, font=fnt, fill=color)
            y += _line_h(fnt)
        if kind in ("lbl", "h1", "meta"):
            y += 4
        if kind == "opt":
            y += 2
    img.save(out, "PNG")


def render_explanation(q: dict, out_p1: Path, out_p2: Path) -> None:
    """Render the explanation as TWO pages so the in-app viewer paginates:
    page 1 = question + options + answer; page 2 = explanation + citation."""
    f_h1 = font(30, bold=True)
    f_lbl = font(20, bold=True)
    f_body = font(22)
    f_small = font(17)
    inner = W - 2 * PAD
    expl = re.sub(r"==", "", q["explanation"])  # drop highlight markers

    # ── page 1: 題目 + 選項 + 正解 ──
    p1: list[tuple] = [
        ("h1", [f"{q['year']} 年 復健科專科考試・第 {q['n']} 題"], f_h1, INK),
        ("meta", [f"科別：{q['topic_major_zh']}　·　歷屆考題 (Past Paper)　·　1 / 2 頁"], f_small, MUTED),
        ("rule", [""], f_small, RULE),
        ("lbl", ["題目"], f_lbl, ACCENT),
        ("body", wrap(q["stem"], f_body, inner), f_body, INK),
    ]
    for letter, txt in q["options"].items():
        mark = "✓ " if letter == q["correct_letter"] else "　"
        color = GOOD if letter == q["correct_letter"] else INK
        p1.append(("opt", wrap(f"{mark}({letter}) {txt}", f_body, inner), f_body, color))
    p1.append(("lbl", [f"正解：{q['correct_letter']}"], f_lbl, GOOD))

    # ── page 2: 詳解 + 出處 ──
    p2: list[tuple] = [
        ("h1", [f"{q['year']} 年・第 {q['n']} 題　詳解"], f_h1, INK),
        ("meta", [f"科別：{q['topic_major_zh']}　·　2 / 2 頁"], f_small, MUTED),
        ("rule", [""], f_small, RULE),
        ("lbl", ["詳解"], f_lbl, ACCENT),
        ("body", wrap(expl, f_body, inner), f_body, INK),
        ("rule", [""], f_small, RULE),
        ("cite", wrap(f"出處：{q['citation']}", f_small, inner), f_small, MUTED),
        ("cite", wrap("※ 詳解為獨立重寫、僅供學習參考，可能有誤。", f_small, inner), f_small, MUTED),
    ]

    _render_page(p1, out_p1)
    _render_page(p2, out_p2)


def render_cmap_demo(out: Path) -> None:
    """Fully synthetic CMAP waveform schematic (no copyrighted source)."""
    w, h = 760, 420
    img = Image.new("RGB", (w, h), (250, 250, 252))
    d = ImageDraw.Draw(img)
    f = font(18)
    f_s = font(15)
    base_y = 250
    x0 = 90
    # axes
    d.line([x0, 40, x0, h - 60], fill=(150, 150, 160), width=2)      # y axis
    d.line([x0, base_y, w - 30, base_y], fill=(150, 150, 160), width=2)  # baseline
    d.text((30, 36), "mV", font=f_s, fill=MUTED)
    d.text((w - 60, base_y + 8), "ms", font=f_s, fill=MUTED)
    # stimulus artifact
    d.line([x0 + 20, base_y, x0 + 20, base_y - 18], fill=(180, 180, 190), width=2)
    d.text((x0 + 8, base_y + 8), "stim", font=f_s, fill=MUTED)
    # CMAP: flat → negative (up) deflection → return → small positive (down)
    onset = x0 + 120
    pts = [
        (x0 + 30, base_y), (onset, base_y),
        (onset + 55, base_y - 150),   # negative peak (up)
        (onset + 130, base_y + 5),
        (onset + 175, base_y + 40),   # positive peak (down)
        (onset + 240, base_y),
        (w - 40, base_y),
    ]
    d.line(pts, fill=ACCENT, width=3)
    # annotations: onset latency, amplitude, duration
    d.line([onset, base_y, onset, base_y + 70], fill=(200, 80, 80), width=1)
    d.line([x0 + 20, base_y + 60, onset, base_y + 60], fill=(200, 80, 80), width=1)
    d.text(((x0 + 20 + onset) // 2 - 24, base_y + 64), "潛伏期", font=f_s, fill=(200, 80, 80))
    # amplitude arrow
    ax = onset + 55
    d.line([ax, base_y, ax, base_y - 150], fill=(200, 80, 80), width=1)
    d.text((ax + 6, base_y - 90), "振幅", font=f_s, fill=(200, 80, 80))
    d.text((20, 14), "圖：運動神經傳導 CMAP 波形（示意，合成圖非真實資料）", font=f, fill=INK)
    img.save(out, "PNG")


def main() -> None:
    qs = json.loads((HERE / "demo_questions.json").read_text(encoding="utf-8"))
    for q in qs:
        n = q["n"]
        render_explanation(q, IMG_DIR / f"expl_{n:02d}.png", IMG_DIR / f"expl_{n:02d}_2.png")
    render_cmap_demo(IMG_DIR / "cmap_demo.png")
    print(f"rendered {len(qs)} × 2 explanation pages + 1 synthetic figure into {IMG_DIR}")


if __name__ == "__main__":
    main()
