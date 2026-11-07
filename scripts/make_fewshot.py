"""학습 매장에서 정답이 16개 특징 모두 확인된 짧은 리뷰로 few-shot 예시를 만든다 (특징별 최소 1회
등장)."""

import json
import random
from pathlib import Path

from naver_review_clf import dataset
from naver_review_clf.schema import FEATURES
from naver_review_clf.segment import split_clauses

ROOT = Path(__file__).resolve().parents[1]

random.seed(7)
rows = [
    r
    for r in dataset.load(ROOT / "data" / "reviews.jsonl", "train")
    if len(r["labels"]) + len(r.get("negatives", [])) == len(FEATURES) and 15 < len(r["text"]) < 140
]
random.shuffle(rows)


def to_items(r):
    clauses = split_clauses(r["text"])
    items = []
    for i, c in enumerate(clauses):
        fs = [
            f
            for f, sp in r["labels"].items()
            if any(
                min(c.end, e) - max(c.start, s) >= 0.5 * min(c.end - c.start, e - s) for s, e in sp
            )
        ]
        if fs:
            items.append({"clause": i, "features": sorted(fs, key=FEATURES.index)})
    return items


picked, covered = [], set()
for f in FEATURES:  # 희귀 특징부터 보장
    for r in rows:
        if f in r["labels"] and r not in picked:
            items = to_items(r)
            if items:
                picked.append(r)
                covered |= set(r["labels"])
                break
    if len(picked) >= 14:
        break
out = [{"store": r["store"], "text": r["text"], "items": to_items(r)} for r in picked]
(ROOT / "data" / "fewshot.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
)
print(len(out), "examples; covered:", [f for f in FEATURES if f in covered])
