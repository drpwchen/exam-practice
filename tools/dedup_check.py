"""Find near-duplicate questions using bge-m3 embeddings.

Sends each question's stem to local Ollama (`bge-m3:latest`), computes pairwise
cosine similarity, flags pairs >= threshold (default 0.92).

0 Claude tokens (local Ollama only).

Run: python -m tools.dedup_check [--threshold 0.92] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
import numpy as np
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.config import settings  # noqa: E402
from backend.db import SessionLocal  # noqa: E402
from backend.models import Question, Source  # noqa: E402

EMBED_URL = settings.ollama_url + "/api/embeddings"
EMBED_MODEL = "bge-m3:latest"


def embed(text: str, client: httpx.Client) -> np.ndarray | None:
    try:
        r = client.post(
            EMBED_URL,
            json={"model": EMBED_MODEL, "prompt": text},
            timeout=120,
        )
        r.raise_for_status()
        data = r.json()
        emb = data.get("embedding")
        if not emb:
            return None
        return np.asarray(emb, dtype=np.float32)
    except Exception as e:
        print(f"  embed error: {e}", file=sys.stderr)
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.92)
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    ap.add_argument(
        "--cache",
        type=Path,
        default=Path("data/_dedup_embeddings.npz"),
        help="cache embeddings here so re-runs are cheap",
    )
    args = ap.parse_args()

    out_path = Path(f"data/_dedup_{datetime.now().strftime('%Y%m%d_%H%M')}.md")

    with SessionLocal() as s:
        sources = {row.id: row.code for row in s.execute(select(Source)).scalars()}
        rows = s.execute(
            select(
                Question.id, Question.source_id, Question.year, Question.stem
            ).order_by(Question.id)
        ).all()

    if args.limit:
        rows = rows[: args.limit]

    n = len(rows)
    print(f"Embedding {n} stems with {EMBED_MODEL}...")

    qids = [r[0] for r in rows]
    metas = [(r[0], sources.get(r[1], "?"), r[2]) for r in rows]
    stems = [r[3] or "" for r in rows]

    # Try cache
    cache = args.cache
    embeddings: np.ndarray | None = None
    cached_qids: list[int] = []
    if cache.exists():
        try:
            data = np.load(cache, allow_pickle=True)
            cached_qids = list(data["qids"].tolist())
            cached_emb = data["embeddings"]
            if cached_qids == qids:
                embeddings = cached_emb
                print(f"  Cache hit: {cache}")
        except Exception as e:
            print(f"  Cache read failed: {e}")

    if embeddings is None:
        with httpx.Client() as client:
            arr_list: list[np.ndarray] = []
            t0 = time.time()
            for i, stem in enumerate(stems):
                emb = embed(stem, client)
                if emb is None:
                    print(f"  ❌ Failed on row {i} (Q#{qids[i]}). Aborting.")
                    return
                arr_list.append(emb)
                if (i + 1) % 50 == 0:
                    rate = (i + 1) / (time.time() - t0)
                    eta = (n - i - 1) / rate if rate > 0 else 0
                    print(f"  [{i + 1}/{n}] rate={rate:.1f}/s eta={eta:.0f}s")
        embeddings = np.stack(arr_list, axis=0)
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(cache, qids=np.array(qids), embeddings=embeddings)
        print(f"  Saved embeddings cache: {cache}")

    # Cosine similarity
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0] = 1
    normed = embeddings / norms
    sim = normed @ normed.T  # n x n

    # Pairs above threshold (i < j to avoid dup)
    pairs: list[tuple[float, int, int]] = []
    n = len(qids)
    for i in range(n):
        for j in range(i + 1, n):
            if sim[i, j] >= args.threshold:
                pairs.append((float(sim[i, j]), i, j))
    pairs.sort(reverse=True)

    lines: list[str] = []
    lines.append(
        f"# Dedup check — {datetime.now().isoformat(timespec='seconds')}\n"
    )
    lines.append(f"Threshold: cosine ≥ {args.threshold}")
    lines.append(f"Questions embedded: {n}")
    lines.append(f"Near-duplicate pairs: **{len(pairs)}**\n")
    lines.append("---\n")

    for score, i, j in pairs:
        qid_i, src_i, year_i = metas[i]
        qid_j, src_j, year_j = metas[j]
        cross_source = "🔀 cross-source" if src_i != src_j else "🟰 same-source"
        lines.append(
            f"## sim={score:.4f}  Q#{qid_i} ({src_i}/{year_i}) ↔ Q#{qid_j} ({src_j}/{year_j}) — {cross_source}\n"
        )
        lines.append(f"- Q#{qid_i}: `{stems[i][:200]}`")
        lines.append(f"- Q#{qid_j}: `{stems[j][:200]}`\n")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport: {out_path}")
    print(f"Pairs found: {len(pairs)}")


if __name__ == "__main__":
    main()
