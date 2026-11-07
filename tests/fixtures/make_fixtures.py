"""Synthetic review fixtures for review_tools tests (no real review text: the repo is public).

    python tests/fixtures/make_fixtures.py tests/fixtures

Stores and reviews are made up. The dates and counts are chosen so the review_tools tests
(written against the real 명동 snapshot) keep their meaning: 연습떡집 has 4 reviews in the
last 30 days (biweekly report), 3 unanswered under the 14-day demo rule with the negative
딸기모찌 review r00732 first; 연습반점 has 3 reviews a day (daily report); 연습돈까스 has a
high-risk (바퀴벌레) review.
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

out = Path(sys.argv[1])


def row(rid, store, cat, day, rating, text, feats):
    features = []
    for feat, span in feats:
        i = text.index(span)
        features.append(
            {
                "feature": feat,
                "highlights": [{"start": i, "end": i + len(span), "text": span}],
                "score": None,
            }
        )
    return {
        "review_id": rid,
        "store": store,
        "category": cat,
        "visit_date": day,
        "rating": rating,
        "text": text,
        "features": features,
        "has_owner_reply": None,
    }


rows = []
A, AC = "연습떡집 본점", "디저트"
rows += [
    row(
        "r00734",
        A,
        AC,
        "2026-10-30",
        5,
        "직원분들이 친절하고 매장이 깨끗해요.",
        [("서비스", "직원분들이 친절하고"), ("청결도", "매장이 깨끗해요")],
    ),
    row(
        "r00733",
        A,
        AC,
        "2026-10-29",
        5,
        "쑥모찌가 쫄깃하고 맛있어요.",
        [("맛", "쑥모찌가 쫄깃하고 맛있어요")],
    ),
    row(
        "r00732",
        A,
        AC,
        "2026-10-25",
        2,
        "딸기모찌 맛이 없어요. 기대했는데 실망이에요.",
        [("맛", "딸기모찌 맛이 없어요"), ("만족도", "기대했는데 실망이에요")],
    ),
    row(
        "r00731",
        A,
        AC,
        "2026-10-15",
        4,
        "딸기모찌 많이 안달고 맛있다. 또 올게요.",
        [("맛", "딸기모찌 많이 안달고 맛있다"), ("만족도", "또 올게요")],
    ),
    row(
        "r00720",
        A,
        AC,
        "2026-09-20",
        3,
        "딸기라떼는 가격이 조금 비싸요.",
        [("가격", "딸기라떼는 가격이 조금 비싸요")],
    ),
    row(
        "r00715",
        A,
        AC,
        "2026-08-30",
        2,
        "웨이팅이 길고 음식이 늦게 나왔어요.",
        [("대기시간", "웨이팅이 길고 음식이 늦게 나왔어요")],
    ),
    row(
        "r00710",
        A,
        AC,
        "2026-07-12",
        5,
        "딸기모찌 쫄깃하고 달달해서 좋아요.",
        [("맛", "딸기모찌 쫄깃하고 달달해서 좋아요")],
    ),
]
B, BC = "연습반점 본점", "중식"
templates = [
    (5, "짜장면이 맛있어요.", [("맛", "짜장면이 맛있어요")]),
    (4, "양이 푸짐하고 좋아요.", [("음식량", "양이 푸짐하고 좋아요")]),
    (2, "짬뽕이 너무 짜요.", [("맛", "짬뽕이 너무 짜요")]),
    (5, "음식이 빨리 나와요.", [("대기시간", "음식이 빨리 나와요")]),
]
n, d = 0, date(2026, 10, 1)
while d <= date(2026, 11, 7):
    for _ in range(3):
        rating, text, feats = templates[n % len(templates)]
        rows.append(row(f"r1{n:04d}", B, BC, d.isoformat(), rating, text, feats))
        n += 1
    d += timedelta(days=1)
C, CC = "연습돈까스 남산점", "돈가스"
rows += [
    row(
        "r02003",
        C,
        CC,
        "2026-10-20",
        1,
        "식탁 위에서 바퀴벌레가 기어다녀요. 위생 상태가 최악이에요.",
        [("청결도", "식탁 위에서 바퀴벌레가 기어다녀요"), ("청결도", "위생 상태가 최악이에요")],
    ),
    row(
        "r02002",
        C,
        CC,
        "2026-10-10",
        5,
        "돈까스가 바삭하고 맛있어요.",
        [("맛", "돈까스가 바삭하고 맛있어요")],
    ),
    row("r02001", C, CC, "2026-09-15", 2, "소스가 너무 짜요.", [("맛", "소스가 너무 짜요")]),
]
# merge same-feature entries (classifier output has one entry per feature)
for r in rows:
    merged = {}
    for f in r["features"]:
        merged.setdefault(f["feature"], {"feature": f["feature"], "highlights": [], "score": None})[
            "highlights"
        ] += f["highlights"]
    r["features"] = list(merged.values())
(out / "review_snapshot.jsonl").write_text(
    "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
)
menus = {
    "연습떡집": [
        {"name": "딸기모찌", "aliases": ["strawberry mochi", "клубничный моти", "Mochi dâu"]},
        {"name": "쑥모찌", "aliases": ["mugwort mochi"]},
        {"name": "딸기라떼", "aliases": ["strawberry latte"]},
    ],
    "연습반점": [
        {"name": "짜장면", "aliases": ["jajangmyeon"]},
        {"name": "짬뽕", "aliases": ["jjamppong"]},
    ],
    "연습돈까스": [{"name": "돈까스", "aliases": ["pork cutlet"]}],
}
(out / "menus.json").write_text(
    json.dumps(menus, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(len(rows))
