"""Stage 0: PDF → 300 DPI PNGs (one per page).

If a page has >50 chars of native text, save text alongside and skip OCR later.
Idempotent: skips pages whose PNG already exists.

Run: python -m pipeline.extract_pages [--limit N]
"""

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import fitz  # PyMuPDF

from backend.config import settings

DPI = 300
NATIVE_TEXT_MIN = 50  # threshold for has_native_text flag (parsing decision)
NATIVE_TEXT_KEEP = 5  # always SAVE native text if at least this many chars (keeps Reference: lines on figure-only pages)


@dataclass
class PageManifest:
    pdf: str
    page: int
    png: str
    has_native_text: bool
    native_text_chars: int


def _slug(name: str) -> str:
    return name.replace(" ", "_").replace("/", "_")


def extract_pdf(pdf: Path, out_dir: Path) -> list[PageManifest]:
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf)
    manifest: list[PageManifest] = []
    for i, page in enumerate(doc):
        png_path = out_dir / f"page_{i:03d}.png"
        text_path = out_dir / f"page_{i:03d}.native.txt"
        text = page.get_text() or ""
        has_native = len(text.strip()) >= NATIVE_TEXT_MIN
        # Save native text even on figure-only pages (small Reference: lines still useful)
        if len(text.strip()) >= NATIVE_TEXT_KEEP and not text_path.exists():
            text_path.write_text(text, encoding="utf-8")
        if not png_path.exists():
            pix = page.get_pixmap(dpi=DPI)
            pix.save(png_path)
        manifest.append(
            PageManifest(
                pdf=pdf.name,
                page=i,
                png=str(png_path.relative_to(out_dir.parent.parent)),
                has_native_text=has_native,
                native_text_chars=len(text),
            )
        )
    doc.close()
    return manifest


def process_past_paper(years: list[int], limit: int | None) -> int:
    """考古題: only ingest selected years (and skip ._* macOS files)."""
    out_root = Path("data/ocr_raw/past_paper")
    out_root.mkdir(parents=True, exist_ok=True)
    pdfs: list[Path] = []
    for y in years:
        for pdf in settings.past_paper_dir.glob(f"{y}年*.pdf"):
            if not pdf.name.startswith("._"):
                pdfs.append(pdf)
    pdfs.sort()
    if limit:
        pdfs = pdfs[:limit]
    total_pages = 0
    for pdf in pdfs:
        out_dir = out_root / _slug(pdf.stem)
        manifest = extract_pdf(pdf, out_dir)
        (out_dir / "_manifest.json").write_text(
            json.dumps([asdict(m) for m in manifest], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        total_pages += len(manifest)
        native = sum(1 for m in manifest if m.has_native_text)
        print(
            f"  {pdf.name}: {len(manifest)} pages "
            f"({native} native text, {len(manifest) - native} need OCR)"
        )
    return total_pages


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="Limit PDFs to process")
    args = ap.parse_args()

    print("== 考古題 ==")
    total = process_past_paper(settings.past_paper_years_list, args.limit)
    print(f"\nTotal pages processed: {total}")


if __name__ == "__main__":
    main()
