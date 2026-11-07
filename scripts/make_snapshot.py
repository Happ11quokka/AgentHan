"""다른 도구(리뷰 분석·요약, 보고 루틴) 검증용 고정 입력 세트를 만든다.

테스트 매장(학습에 쓰지 않은 10개 매장) 리뷰 + 분류기 출력을 한 번 저장해 두면,
분류기를 바꾸거나 다시 학습해도 도구 테스트의 입력은 그대로 유지된다.

    python scripts/make_snapshot.py                        # baseline, models/baseline.joblib
    python scripts/make_snapshot.py --model path/to/x.joblib --split test

출력
- data/snapshots/<split>_<backend>.jsonl  리뷰 1건 = 1줄
- data/snapshots/<split>_<backend>.manifest.json  만든 조건, 매장별 건수·기간, 필드 설명
"""

import argparse
import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from naver_review_clf import dataset, get_backend
from naver_review_clf.dataset import _parse_visit

ROOT = Path(__file__).resolve().parents[1]

ap = argparse.ArgumentParser()
ap.add_argument("--backend", default="baseline", choices=["baseline", "hcx"])
ap.add_argument("--model", default=None, help="baseline 모델 경로 (기본 models/baseline.joblib)")
ap.add_argument("--split", default="test", choices=["test", "train", "all"])
a = ap.parse_args()

rows = dataset.load(ROOT / "data" / "reviews.jsonl", None if a.split == "all" else a.split)
clf = get_backend(a.backend, **({"path": a.model} if a.model else {}))

FIELDS = {
    "review_id": "리뷰 고유 ID (reviews.jsonl의 id 기반). 루틴의 '새 리뷰' 판별과 중복 제거에 사용",
    "store": "매장명",
    "category": "업종",
    "visit_date": (
        "방문일 ISO (YYYY-MM-DD). 수집 데이터에 작성일이 없어 방문일을 기간 필터 기준으로 사용"
    ),
    "visit_raw": "네이버 표기 원문 (예: 11.6.금)",
    "rating": "별점. 네이버가 별점을 표시하지 않는 리뷰는 null (약 46%)",
    "text": "리뷰 본문",
    "features": (
        "분류기 출력: 특징별 굵게 표시 구간과 점수 (네이버 [특징] 16개 클래스, 긍정·부정 구분 없음)"
    ),
    "naver_features": (
        "수집 당시 네이버가 붙인 특징과 구간 (관측된 것만, 표본 한계로 누락 가능). 도구 검증 시 "
        "정답 참고용"
    ),
    "has_owner_reply": "사장님 답글 여부. 수집 데이터에 없어 null",
}

out_dir = ROOT / "data" / "snapshots"
out_dir.mkdir(parents=True, exist_ok=True)
stem = f"{a.split}_{a.backend}"
by_store = defaultdict(list)
with (out_dir / f"{stem}.jsonl").open("w", encoding="utf-8") as f:
    for r in rows:
        v = _parse_visit(r["visit"])
        vd = date(*v).isoformat() if v else None
        rec = {
            "review_id": f"r{r['id']:05d}",
            "store": r["store"],
            "category": r["category"],
            "visit_date": vd,
            "visit_raw": r["visit"],
            "rating": r["star"],
            "text": r["text"],
            "features": clf.classify(r["text"]).to_dict()["features"],
            "naver_features": r["labels"],
            "has_owner_reply": None,
        }
        by_store[r["store"]].append(rec)
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

stores = {}
for s, recs in sorted(by_store.items(), key=lambda kv: -len(kv[1])):
    ds = sorted(x["visit_date"] for x in recs if x["visit_date"])
    stores[s] = {
        "category": recs[0]["category"],
        "reviews": len(recs),
        "visit_date_min": ds[0] if ds else None,
        "visit_date_max": ds[-1] if ds else None,
        "rated": sum(x["rating"] is not None for x in recs),
    }

meta = {
    "created_at": datetime.now().isoformat(timespec="seconds"),
    "split": a.split,
    "backend": a.backend,
    "reviews": sum(len(v) for v in by_store.values()),
    "stores": stores,
    "fields": FIELDS,
    "caveats": [
        "특징별 최신 20건 수집이라 매장의 전체 리뷰가 아니며, 날짜가 오래될수록 빠진 리뷰가 많다",
        "분류기는 네이버보다 특징을 많이 붙이는 경향 (리뷰당 2.59개 vs 약 1.89개)",
        "한국어가 아닌 리뷰는 특징이 붙지 않는다",
    ],
}
if a.backend == "baseline":
    import sklearn

    meta["sklearn_version"] = sklearn.__version__
    meta["model"] = str(a.model or "models/baseline.joblib")
(out_dir / f"{stem}.manifest.json").write_text(
    json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(f"saved data/snapshots/{stem}.jsonl ({meta['reviews']} reviews, {len(stores)} stores)")
