"""조사 엑셀(근거리뷰_원문 시트) → 학습/평가용 JSONL.

라벨 성격 (중요)
- 각 행은 '특징 X 필터를 켰을 때 나온 리뷰'이므로 X에 대해서는 정답(양성 + 구간)이 확실하다.
- 같은 리뷰가 여러 특징 필터에 나오면 하나로 합쳐 멀티라벨로 만든다.
- 하지만 특징별 최신 20건만 수집했으므로, 어떤 리뷰가 Y 필터에 없다고 해서 Y가 아니라고 단정할 수
없다.
  → 평가는 '관측된 특징' 안에서만 재현율을 보고, 정밀도는 같은 리뷰 안의 비하이라이트 절로 본다.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

import openpyxl

from .schema import FEATURES

SHEET = "근거리뷰_원문"


def _split_of(store: str, test_ratio: float = 0.2) -> str:
    # 매장 단위로 나눠야 같은 매장의 메뉴명 등이 평가에 새지 않는다
    h = int(hashlib.md5(store.encode()).hexdigest(), 16) % 1000
    return "test" if h < test_ratio * 1000 else "train"


def build(xlsx_path: str | Path, out_path: str | Path) -> dict:
    wb = openpyxl.load_workbook(xlsx_path, read_only=True)
    ws = wb[SHEET]
    it = ws.iter_rows(values_only=True)
    header = next(it)
    col = {name: i for i, name in enumerate(header)}

    reviews: dict[tuple[str, str], dict] = {}
    for r in it:
        store, text, feat = (
            r[col["매장명"]],
            r[col["리뷰 전문 (근거 구간 굵게·파랑)"]],
            r[col["특징"]],
        )
        if not text or feat not in FEATURES:
            continue
        key = (store, text)
        rec = reviews.setdefault(
            key,
            {
                "store": store,
                "category": r[col["업종"]],
                "star": r[col["별점"]],
                "visit": r[col["방문일"]],
                "text": text,
                "labels": defaultdict(list),
            },
        )
        for seg in (r[col["굵게 표시된 근거 텍스트"]] or "").split(" | "):
            if not seg:
                continue
            i = text.find(seg)
            if i >= 0 and [i, i + len(seg)] not in rec["labels"][feat]:
                rec["labels"][feat].append([i, i + len(seg)])
        rec["labels"].setdefault(feat, [])

    _add_negatives(reviews, _store_counts(wb))

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    stats = {"train": 0, "test": 0}
    with out_path.open("w", encoding="utf-8") as f:
        for idx, rec in enumerate(reviews.values()):
            rec["id"] = idx
            rec["split"] = _split_of(rec["store"])
            rec["labels"] = dict(rec["labels"])
            stats[rec["split"]] += 1
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    stats["reviews"] = len(reviews)
    return stats


def _parse_visit(v) -> tuple[int, int, int] | None:
    """'11.6.금' → (2026, 11, 6), '25.10.3.금' → (2025, 10, 3)"""
    parts = [p for p in str(v or "").split(".") if p.isdigit()]
    if len(parts) == 2:
        return (CURRENT_YEAR, int(parts[0]), int(parts[1]))
    if len(parts) == 3:
        return (2000 + int(parts[0]), int(parts[1]), int(parts[2]))
    return None


CURRENT_YEAR = 2026  # 조사일 2026-11-07, 올해 리뷰는 연도 없이 표시됨


def _store_counts(wb) -> dict[tuple[str, str], int]:
    ws = wb["매장목록"]
    it = ws.iter_rows(values_only=True)
    header = list(next(it))
    out = {}
    for r in it:
        for f in FEATURES:
            out[(r[1], f)] = int(r[header.index(f)] or 0)
    return out


def _add_negatives(reviews: dict, counts: dict) -> None:
    """확실한 음성 라벨: 특징 f 표본이 최신순 상위 20건이므로
    (a) 매장의 f 리뷰 총수가 표본 수 이하면 표본 = 전체 → 표본에 없는 리뷰는 f가 아님
    (b) 표본의 가장 오래된 날짜보다 '엄격히' 최근인 리뷰가 표본에 없으면 f가 아님
    (방문일 기준 정렬로 보이므로 같은 날짜는 판단하지 않는다)"""
    sample = defaultdict(list)  # (store, f) -> dates
    for rec in reviews.values():
        for f in rec["labels"]:
            sample[(rec["store"], f)].append(_parse_visit(rec["visit"]))
    for rec in reviews.values():
        d = _parse_visit(rec["visit"])
        neg = []
        for f in FEATURES:
            if f in rec["labels"]:
                continue
            dates = [x for x in sample.get((rec["store"], f), []) if x]
            total = counts.get((rec["store"], f), 0)
            if total <= len(sample.get((rec["store"], f), [])):
                neg.append(f)
            elif d and dates and len(dates) >= 20 and d > min(dates):
                neg.append(f)
        rec["negatives"] = neg


def load(path: str | Path, split: str | None = None) -> list[dict]:
    with Path(path).open(encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    return [r for r in rows if split is None or r["split"] == split]
