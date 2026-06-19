"""Auto-tag 考古題 questions using bge-m3 embeddings + cosine similarity.

For each question:
1. Embed (stem + reference_page).
2. Compute cosine similarity with all 26 tag corpora.
3. Assign top-1 tag (and optionally top-2 if top-1 confidence is low).
4. Mark question.needs_review=True if top score < THRESHOLD.

Tags can be edited later via admin UI.
"""

import argparse
import math
import time
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import settings
from backend.db import SessionLocal
from backend.models import Question, QuestionTag, Source, Tag

OLLAMA_EMBED_URL = settings.ollama_url + "/api/embeddings"
EMBED_MODEL = "bge-m3"
THRESHOLD_SOLO = 0.55      # if top score < this, also include top-2
LOW_CONFIDENCE = 0.45      # if top score < this, mark needs_review

# Extra Chinese keywords per tag, to make embedding more discriminative.
TAG_KEYWORDS: dict[str, str] = {
    "ADL": "日常生活活動 工作能力 職能治療 OT FCE return to work 居家環境 無障礙設計 ICF 失能評估",
    "AMP_PRO": "截肢 義肢 PVD 周邊血管 amputation prosthesis socket 殘肢",
    "ART": "風濕關節炎 RA OA 紅斑性狼瘡 強直性脊椎炎 rheumatoid osteoarthritis lupus ankylosing",
    "BMC": "生物力學 biomechanics 力矩 force moment 步態 gait 動作分析 motion analysis",
    "CP": "兒童復健 腦性麻痺 cerebral palsy 發展遲緩 GMFCS 動作發展 早療",
    "CR": "心臟復健 cardiac rehabilitation MI 心肌梗塞 心衰 EF CABG STEMI 冠心病",
    "CVA": "中風 腦中風 stroke 偏癱 hemiplegia 半側忽略 unilateral neglect Brunnstrom",
    "ED": "電學診斷 EMG NCS 神經傳導 肌電圖 myopathy 肌肉病變 SFEMG ALS MUAP",
    "EP": "運動生理 exercise physiology VO2 心輸出 lactate 有氧無氧 訓練原則",
    "NMD": "神經調控 neuromodulation rTMS tDCS 經顱磁刺激 functional electrical stimulation FES robot 機器人 AI 人工智慧",
    "GER": "老年復健 geriatric 跌倒 frailty 失能 sarcopenia 肌少症 跌倒風險",
    "NB": "神經性膀胱 neurogenic bladder bowel 大小便失禁 catheter cystometry urodynamic",
    "ORR": "骨科復健 orthopedic 五十肩 frozen shoulder rotator cuff 椎間盤 disc herniation 膝關節置換 TKA",
    "AST": "輔具 assistive technology orthotics 矯具 wheelchair 輪椅 AFO KAFO 鞋墊",
    "PM": "物理因子治療 modality 熱療 冷療 超音波治療 短波 TENS 牽引 traction electrotherapy",
    "PN": "周邊神經 peripheral neuropathy CIDP GBS guillain barre carpal tunnel CTS 神經病變",
    "PR": "肺部復健 pulmonary rehabilitation COPD asthma 呼吸訓練 6MWT pulmonary function",
    "RN": "復健護理 burn 燒燙傷 wound care 壓瘡 pressure ulcer skin graft 皮膚移植",
    "REH": "復健概念 rehabilitation concept ICF 醫學倫理 medical ethics 法律 health insurance 健保 disability evaluation",
    "SM": "運動醫學 sports medicine 運動傷害 injury 禁藥 doping anti-doping 殘障運動",
    "SCI": "脊髓損傷 spinal cord injury ASIA 自律神經反射 autonomic dysreflexia tetraplegia paraplegia",
    "SPE": "語言 speech 吞嚥 swallowing dysphagia VFSS aphasia 失語症 dysarthria 構音",
    "TBI": "頭部外傷 traumatic brain injury concussion GCS 認知 cognitive 行為",
    "PAIN": "疼痛醫學 pain medicine chronic pain CRPS 神經痛 neuropathic 椎間盤注射 trigger point",
    "US": "肌肉骨骼超音波 musculoskeletal ultrasound sonography MSK 注射 injection 影像 dynamic",
    "CAN": "癌症復健 cancer rehabilitation 淋巴水腫 lymphedema 化療 chemo 放療 radiation",
}


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def embed(client: httpx.Client, text: str) -> list[float]:
    r = client.post(
        OLLAMA_EMBED_URL,
        json={"model": EMBED_MODEL, "prompt": text[:2000]},
        timeout=60,
    )
    return r.json().get("embedding", [])


def build_tag_corpus(tag: Tag) -> str:
    parts = [
        tag.name,
        tag.short_zh or "",
        tag.display_name or "",
        TAG_KEYWORDS.get(tag.name, ""),
    ]
    return " ".join(p for p in parts if p)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--reset", action="store_true",
                    help="Delete existing tags before classifying")
    args = ap.parse_args()

    with SessionLocal() as s, httpx.Client() as client:
        kao_gu = s.execute(select(Source).where(Source.code == "past_paper")).scalar_one()
        tags = s.scalars(
            select(Tag).where(Tag.level == "major").order_by(Tag.sort_order)
        ).all()
        print(f"Embedding {len(tags)} tags...")
        tag_embeds: dict[int, list[float]] = {}
        for t in tags:
            corpus = build_tag_corpus(t)
            tag_embeds[t.id] = embed(client, corpus)
            print(f"  {t.name}: corpus_len={len(corpus)}")

        questions = s.scalars(
            select(Question).where(Question.source_id == kao_gu.id).order_by(Question.id)
        ).all()
        if args.limit:
            questions = questions[: args.limit]
        print(f"\nClassifying {len(questions)} questions...")

        classified = unclassified = 0
        t_start = time.time()
        for idx, q in enumerate(questions, 1):
            text = (q.stem or "") + " " + (q.reference_page or "") + " " + (q.explanation or "")[:500]
            qe = embed(client, text)
            scored = [(t.id, t.name, cosine(qe, tag_embeds[t.id])) for t in tags]
            scored.sort(key=lambda x: x[2], reverse=True)
            top = scored[0]

            if args.reset:
                s.execute(
                    QuestionTag.__table__.delete().where(QuestionTag.question_id == q.id)
                )

            existing_tag_ids = {
                tid for (tid,) in s.execute(
                    select(QuestionTag.tag_id).where(QuestionTag.question_id == q.id)
                ).all()
            }
            if top[0] not in existing_tag_ids:
                s.add(QuestionTag(question_id=q.id, tag_id=top[0]))
            classified += 1

            if top[2] < LOW_CONFIDENCE:
                q.needs_review = True
                unclassified += 1

            if idx % 50 == 0:
                elapsed = time.time() - t_start
                rate = idx / elapsed
                eta = (len(questions) - idx) / rate
                s.commit()  # checkpoint
                print(
                    f"  [{idx}/{len(questions)}] last: {q.id}={top[1]} ({top[2]:.2f}) "
                    f"| rate {rate:.1f}/s eta {eta:.0f}s"
                )

        s.commit()
        print(f"\nDone: classified={classified} flagged_review={unclassified}")


if __name__ == "__main__":
    main()
