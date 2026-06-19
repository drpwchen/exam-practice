#!/usr/bin/env python
"""Auto-crop white/light-grey borders from exam page images.

Detection: pixel grayscale value < BG_THRESHOLD = content. Bounding box
of all content pixels, then add PAD pixels of margin. Handles both pure
white (255) and light grey blocks (~245) at edges.

Usage:
    python scripts/crop_whitespace.py --dry-run data/source_page_cache/kao_gu/110年解題
    python scripts/crop_whitespace.py --apply data/source_page_cache data/question_images
"""
import argparse
import os
import sys
from pathlib import Path
from PIL import Image
import numpy as np

BG_THRESHOLD = 240  # pixel grayscale > this = background
TOLERANCE = 0.005   # row/col is bg if < this fraction of pixels are content (handles JPEG noise inside grey blocks)
PAD = 5
MIN_SHRINK = 20  # don't bother saving if savings < this many pixels per dimension

EXTS = {'.jpg', '.jpeg', '.png'}


def compute_bbox(img_path: Path):
    im = Image.open(img_path)
    a = np.array(im.convert('L'))
    H, W = a.shape
    content = a < BG_THRESHOLD
    # Tolerance-based: row/col is "content" only if >TOLERANCE fraction of its pixels are dark.
    # This treats uniform light-grey blocks (with sparse JPEG noise pixels) as background.
    col_frac = content.mean(axis=0)
    row_frac = content.mean(axis=1)
    content_cols = np.where(col_frac > TOLERANCE)[0]
    content_rows = np.where(row_frac > TOLERANCE)[0]
    if len(content_cols) == 0 or len(content_rows) == 0:
        return None
    top, bottom = content_rows.min(), content_rows.max()
    left, right = content_cols.min(), content_cols.max()
    # padding, clamped
    top = max(0, top - PAD)
    left = max(0, left - PAD)
    bottom = min(H - 1, bottom + PAD)
    right = min(W - 1, right + PAD)
    return (left, top, right + 1, bottom + 1), (W, H), im


def process(img_path: Path, apply: bool):
    try:
        result = compute_bbox(img_path)
    except Exception as e:
        return ('error', str(e), None)
    if result is None:
        return ('blank', None, None)
    bbox, (W, H), im = result
    new_w = bbox[2] - bbox[0]
    new_h = bbox[3] - bbox[1]
    saved_w = W - new_w
    saved_h = H - new_h
    if saved_w < MIN_SHRINK and saved_h < MIN_SHRINK:
        return ('skip', f'{W}x{H} (no notable margin)', None)
    if apply:
        cropped = im.crop(bbox)
        save_kwargs = {}
        if img_path.suffix.lower() in ('.jpg', '.jpeg'):
            # Reuse original quantization tables to keep file size close to original.
            qt = getattr(im, 'quantization', None)
            if qt:
                save_kwargs = {'qtables': qt, 'subsampling': 2, 'optimize': True}
            else:
                save_kwargs = {'quality': 85, 'optimize': True}
        cropped.save(img_path, **save_kwargs)
    return ('crop', f'{W}x{H} -> {new_w}x{new_h} (saved {saved_w}w {saved_h}h)', bbox)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('paths', nargs='+', help='Files or directories')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--dry-run', action='store_true')
    g.add_argument('--apply', action='store_true')
    ap.add_argument('--limit', type=int, default=0, help='process at most N files (debug)')
    args = ap.parse_args()

    files = []
    for p in args.paths:
        pp = Path(p)
        if pp.is_file() and pp.suffix.lower() in EXTS:
            files.append(pp)
        elif pp.is_dir():
            for ext in EXTS:
                files.extend(pp.rglob(f'*{ext}'))
    files = sorted(set(files))
    if args.limit:
        files = files[:args.limit]
    print(f'Found {len(files)} images')

    counts = {'crop': 0, 'skip': 0, 'blank': 0, 'error': 0}
    total_saved_pixels = 0
    examples = {'crop': []}
    for i, f in enumerate(files):
        status, msg, bbox = process(f, apply=args.apply)
        counts[status] += 1
        if status == 'crop':
            if len(examples['crop']) < 8:
                examples['crop'].append((f, msg))
        elif status == 'error':
            print(f'  ERROR {f}: {msg}')
        if (i + 1) % 200 == 0:
            print(f'  ...{i+1}/{len(files)}')

    print('\n=== Summary ===')
    for k, v in counts.items():
        print(f'  {k}: {v}')
    print(f'\nMode: {"APPLY (overwrote files)" if args.apply else "DRY-RUN (no changes)"}')
    print('\nSample crops:')
    for f, msg in examples['crop']:
        rel = str(f).replace(str(Path.home()), '~')
        print(f'  {rel}: {msg}')


if __name__ == '__main__':
    main()
