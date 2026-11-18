"""리뷰 도구 테스트·데모용 더미 스냅샷을 만든다 → fixtures/review_snapshot.jsonl

가게·리뷰는 전부 지어낸 것이다. 실제 가게 이름이나 리뷰 문장을 옮겨 쓰지 않는다
(PRD §0 데이터 원칙).
특징(features)은 로컬 baseline 분류기로 붙인다. 모델 파일(models/baseline.joblib)은 레포에 없으니
팀에서 받아 둔 뒤 실행한다. 결과 파일은 커밋한다.

    uv run python scripts/make_fixture.py
"""

import json
from datetime import date, timedelta
from pathlib import Path

from agenthan.tools.review_clf import get_backend

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "fixtures" / "review_snapshot.jsonl"

# (방문일, 별점, 본문). 별점 None = 별점 없는 리뷰
MOCHI = [
    ("2026-10-30", 4.5, "귤모찌 상큼하고 맛있네요"),
    ("2026-10-29", 5, "친구랑 딸기모찌 나눠 먹었는데 둘 다 맛있다고 했어요. 다음에 또 올게요"),
    ("2026-10-25", 2, "딸기모찌 기대하고 샀는데 맛이 없어요... 딸기가 시큼하고 떡만 괜찮았어요"),
    ("2026-10-18", 5, "딸기모찌 아이가 정말 좋아했어요"),
    ("2026-09-20", 5, "샤인머스캣모찌가 달콤하고 탱글해서 맛있었어요"),
    ("2026-09-03", 4, "왕딸기모찌 크기가 커서 좋아요. 다만 가격이 좀 비싸요"),
    ("2026-08-17", 5, "포장이 예뻐서 선물용으로 샀는데 다들 맛있다고 했어요"),
    ("2026-07-22", 5, "예약하고 가서 기다리지 않고 바로 받았어요. 딸기모찌 맛있어요"),
    ("2026-06-23", 5, "많이 달지 않고 떡이 쫀득해서 맛있어요"),
    ("2026-06-02", 5, "맛있어요"),
    ("2026-05-18", 3, "예전보다 맛이 평범해진 느낌이에요"),
    ("2026-05-05", 4.5, "떡이 얇고 부드러워요. 아이가 잘 먹었어요"),
    ("2026-02-26", None, "늦게 가서 딸기모찌는 없었고 귤모찌만 샀어요. 아쉬워요"),
    ("2026-02-07", None, "딸기철마다 오는 곳이에요. 딸기모찌 맛있는데 가격이 사악해요"),
    ("2026-01-21", None, "쫀득하고 상큼해서 맛있어요"),
    ("2025-12-30", None, "모양도 예쁘고 맛도 좋아요. 가격이 비싸서 자주는 못 먹겠어요"),
    ("2025-12-24", None, "직원분이 불친절했어요. 맛은 괜찮았어요"),
    ("2025-11-15", None, "맛은 있는데 불친절하고 너무 비싸요"),
    ("2025-10-21", None, "골목 안에 있어서 찾기 어려웠지만 매장이 깨끗하고 친절했어요"),
    ("2025-09-09", None, "주말에 줄이 길어서 30분 기다렸어요. 맛은 좋았어요"),
    ("2025-06-27", None, "여름 과일모찌도 맛있네요. 또 생각나는 맛이에요"),
    ("2025-02-11", None, "사장님이 친절하시고 선물 포장도 깔끔해요"),
    ("2024-12-09", None, "겨울마다 꼭 사 먹어요. 딸기모찌 최고예요"),
    ("2024-06-04", None, "엄청 맛있지는 않아요. 앉을 자리가 없어 불편했어요"),
]

DISHES = ["짜장면", "짬뽕", "탕수육", "볶음밥", "마파두부", "깐풍기"]
GOOD = [
    "{d} 정말 맛있어요",
    "{d} 양이 많고 맛있어요. 또 올게요",
    "점심에 {d} 먹었는데 빨리 나와서 좋았어요",
    "{d} 바삭하고 소스가 맛있어요",
    "직원분들이 친절하고 {d}도 맛있었어요",
    "가족이랑 {d} 시켰는데 다들 만족했어요",
]
BAD = [
    "{d} 너무 짜서 별로였어요",
    "웨이팅이 길고 {d}이 식어서 나왔어요",
    "{d} 양이 적고 가격이 비싸요",
    "직원이 불친절했어요. {d} 맛은 보통이에요",
]


def chinese_rows(today: date) -> list[tuple]:
    """최근 4주에 하루 2~3건 (보고 주기 '매일' 제안용) + 그 전 몇 건."""
    rows = []
    for i in range(70):
        d = today - timedelta(days=i % 28)
        dish = DISHES[i % len(DISHES)]
        if i % 7 == 3:
            rows.append((d.isoformat(), 2, BAD[i % len(BAD)].format(d=dish)))
        else:
            rows.append((d.isoformat(), 5 if i % 2 else 4.5, GOOD[i % len(GOOD)].format(d=dish)))
    for k, days in enumerate([40, 55, 70, 90, 120]):
        dish = DISHES[k]
        rows.append(((today - timedelta(days=days)).isoformat(), 4, f"{dish} 무난하게 맛있어요"))
    return rows


TONKATSU = [
    ("2026-10-28", 5, "등심돈가스가 두툼하고 바삭해요"),
    ("2026-10-12", 4, "치즈돈가스 치즈가 고소하니 맛있어요. 양도 많아요"),
    ("2026-09-15", 1, "식탁 옆 벽에 바퀴벌레가 기어가는 걸 봤어요. 위생 관리 좀 해 주세요"),
    ("2026-08-30", 2, "튀김옷이 눅눅하고 소스가 너무 달아요"),
    ("2026-07-19", 5, "남산 산책하고 들렀는데 돈가스 크기가 엄청 커요"),
    ("2026-06-05", 4, "직원분이 친절하고 음식이 빨리 나와요"),
    ("2026-04-11", 3, "맛은 보통인데 가격이 조금 올랐네요"),
    ("2025-12-20", None, "옛날 경양식 돈가스 느낌이라 좋아요"),
]

STORES = [
    ("달빛모찌 본점", "카페,디저트", "m", MOCHI),
    ("황금반점 본점", "중식당", "c", None),
    ("별빛돈가스 본점", "돈가스", "t", TONKATSU),
]


def main(today: date = date(2026, 11, 7)) -> None:
    clf = get_backend("baseline")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with OUT.open("w", encoding="utf-8") as f:
        for store, category, prefix, rows in STORES:
            rows = rows if rows is not None else chinese_rows(today)
            for i, (vd, star, text) in enumerate(rows, 1):
                feats = clf.classify(text).to_dict()["features"]
                rec = {
                    "review_id": f"{prefix}{i:03d}",
                    "store": store,
                    "category": category,
                    "visit_date": vd,
                    "visit_raw": "",
                    "rating": star,
                    "text": text,
                    "features": feats,
                    "naver_features": {},
                    "has_owner_reply": None,
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n += 1
    print(f"saved {OUT.relative_to(ROOT)} ({n} reviews, {len(STORES)} stores)")


if __name__ == "__main__":
    main()
