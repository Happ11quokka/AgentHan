"""topic(사장님이 말한 메뉴·항목 표현) → 메뉴 목록 + 네이버 16개 특징.

1) 메뉴 별칭 사전과 특징 키워드 사전으로 찾고
2) 못 찾으면 llm(선택)에게 묻는다. llm이 없으면 unresolved로 돌려준다.
메뉴 목록·별칭은 원래 사장님 RAG에서 받아야 하며, data/menus_sample.json은 테스트용 예시다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# 네이버 [특징] 클래스 → 사장님이 쓸 법한 표현 (한/영/러). 범위는 naver_review_clf.FEATURE_GUIDE
# 기준
ASPECT_KEYWORDS: dict[str, list[str]] = {
    "맛": [
        "맛",
        "달다",
        "단맛",
        "당도",
        "짜",
        "싱거",
        "느끼",
        "taste",
        "tasty",
        "sweet",
        "вкус",
        "сладк",
    ],
    "만족도": ["만족", "재방문", "평판", "전반", "satisf", "впечатлен"],
    "서비스": [
        "서비스",
        "친절",
        "직원",
        "응대",
        "알바",
        "사장님 태도",
        "service",
        "staff",
        "friendly",
        "персонал",
        "обслуживан",
    ],
    "음식량": ["양", "크기", "사이즈", "푸짐", "portion", "size", "порци", "размер"],
    "가격": ["가격", "가성비", "비싸", "저렴", "price", "expensive", "cheap", "цен", "дорог"],
    "분위기": [
        "분위기",
        "인테리어",
        "매장",
        "좌석",
        "atmosphere",
        "interior",
        "атмосфер",
        "интерьер",
    ],
    "메뉴": ["메뉴 구성", "메뉴 종류", "신메뉴", "품절", "menu", "меню"],
    "대기시간": [
        "웨이팅",
        "대기",
        "줄",
        "기다",
        "빨리 나",
        "늦게 나",
        "wait",
        "queue",
        "line",
        "очеред",
        "ожидан",
    ],
    "청결도": [
        "청결",
        "위생",
        "깨끗",
        "더럽",
        "화장실",
        "머리카락",
        "이물질",
        "clean",
        "hygien",
        "чист",
    ],
    "목적": ["선물", "데이트", "가족", "모임", "아이", "gift", "date", "подар"],
    "위치": ["위치", "찾기", "접근", "location", "располож"],
    "예약": ["예약", "주문", "결제", "reservation", "booking", "order", "брон", "заказ"],
    "전망": ["전망", "뷰", "야경", "view", "вид"],
    "배달": [
        "포장",
        "배달",
        "픽업",
        "테이크아웃",
        "보냉",
        "takeout",
        "delivery",
        "pickup",
        "упаков",
        "доставк",
        "навынос",
    ],
    "방역": ["방역", "환기", "간격", "마스크"],
    "주차": ["주차", "parking", "парков"],
}


class MenuCatalog:
    """{매장명 접두어: [{"name": 정식 메뉴명, "aliases": [...]}, ...]}"""

    def __init__(self, data: dict[str, list[dict]] | None = None):
        self.data = data or {}

    @classmethod
    def load(cls, path: str | Path) -> MenuCatalog:
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def menus(self, store: str) -> list[dict]:
        for key, items in self.data.items():
            if store.startswith(key) or key.startswith(store):
                return items
        return []


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s.lower())


def menu_aliases(menu: dict) -> list[str]:
    return [menu["name"], *menu.get("aliases", [])]


def mentions_menu(text: str, menu: dict) -> bool:
    t = _norm(text)
    return any(_norm(a) in t for a in menu_aliases(menu))


def resolve_topic(topic: str | None, store: str, catalog: MenuCatalog, llm=None) -> dict:
    """→ {"menus": [정식명], "aspects": [특징], "unresolved": [남은 표현], "candidates": [후보]}"""
    res = {"menus": [], "aspects": [], "unresolved": [], "candidates": []}
    if not topic or not topic.strip():
        return res
    menus = catalog.menus(store)
    rest = topic
    # 1) 메뉴: 긴 별칭부터 매칭해 '왕딸기'가 '딸기'보다 먼저 잡히게 한다
    pairs = sorted(
        ((a, m["name"]) for m in menus for a in menu_aliases(m)), key=lambda p: -len(p[0])
    )
    for alias, name in pairs:
        if _norm(alias) and _norm(alias) in _norm(rest):
            if name not in res["menus"]:
                res["menus"].append(name)
            rest = re.sub(re.escape(alias), " ", rest, flags=re.I)
    # 2) 특징 키워드
    low = rest.lower()
    for aspect, kws in ASPECT_KEYWORDS.items():
        for kw in kws:
            if kw.lower() in low:
                if aspect not in res["aspects"]:
                    res["aspects"].append(aspect)
                low = low.replace(kw.lower(), " ")
    # 3) 남은 의미 있는 단어
    stop = {
        "리뷰",
        "반응",
        "어때",
        "어때요",
        "얘기",
        "있어",
        "손님",
        "손님들",
        "요즘",
        "최근",
        "관련",
        "대한",
        "평가",
        "많아",
        "review",
        "reviews",
        "about",
        "отзыв",
        "отзывы",
    }
    leftover = [w for w in re.findall(r"[\w가-힣]+", low) if len(w) >= 2 and w not in stop]
    # 일부만 해석돼도 남은 단어가 있으면 되묻는다 ('메도빅 가격'을 가격만으로 분석하지 않도록)
    if leftover:
        # 메뉴 일부와 겹치면(예: '모찌', '케이크') 후보로 되묻게 한다
        cands = [
            m["name"]
            for m in menus
            if any(_norm(w) in _norm(a) for w in leftover for a in menu_aliases(m))
        ]
        if llm is not None:
            guess = llm.resolve_topic(topic, [m["name"] for m in menus], list(ASPECT_KEYWORDS))
            if guess.get("menus") or guess.get("aspects"):
                return {**res, "menus": guess.get("menus", []), "aspects": guess.get("aspects", [])}
        res["unresolved"] = leftover
        res["candidates"] = cands
    return res


def _stem_hit(alias: str, text: str) -> bool:
    """별칭의 모든 단어가 문장 속 어떤 단어의 앞부분(최대 5글자)과 같으면 일치.
    한국어 붙여쓰기('딸기모찌')와 러시아어 격변화('клубничное'→'клубничном')를 함께 처리한다."""
    toks = re.findall(r"[\w가-힣]+", text.lower())
    if _norm(alias) in _norm(text):
        return True
    words = [w for w in re.findall(r"[\w가-힣]+", alias.lower()) if len(w) >= 2]
    return bool(words) and all(any(t.startswith(w[:5]) for t in toks) for w in words)


def resolve_from_utterance(utterance: str, store: str, catalog: MenuCatalog, llm=None) -> dict:
    """LLM이 topic을 망가뜨렸을 때(로마자 변환 등) 시스템이 가진 사장님 원문에서 메뉴·특징을 다시
    찾는다."""
    res = {"menus": [], "aspects": [], "unresolved": [], "candidates": []}
    for m in catalog.menus(store):
        if any(_stem_hit(a, utterance) for a in menu_aliases(m)):
            res["menus"].append(m["name"])
    if not res["menus"] and llm is not None:
        guess = llm.resolve_topic(
            utterance, [m["name"] for m in catalog.menus(store)], list(ASPECT_KEYWORDS)
        )
        res["menus"], res["aspects"] = guess.get("menus", []), guess.get("aspects", [])
    return res
