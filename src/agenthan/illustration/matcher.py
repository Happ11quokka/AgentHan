"""메뉴 이름(과 있으면 분류)으로 대표 그림 id를 고른다. HCX는 부르지 않는다.

순서(spec):
  1. 정규화한 이름(공백·기호 제거)이 카탈로그 이름·별칭과 정확히 같으면 그 그림
  2. 이름에 카탈로그 키워드가 들어 있으면 가장 긴 키워드의 그림
  3. CLOVA 임베딩: 메뉴 이름 벡터와 미리 계산한 카탈로그 벡터(embedding_index)의 코사인이
     SIMILARITY_THRESHOLD 이상이면 가장 가까운 그림. 분류 단서(분류 필드나 이름 끝말)가 있으면
     같은 무리(디저트·식사·커피·차·에이드)의 그림 안에서만 찾는다("바닐라 라떼"가 "바닐라
     치즈케이크"로 가지 않게). embed가 None이거나 실패하면 건너뛴다.
  4. 분류 기본 그림: 메뉴 분류 필드 → 없으면 이름 끝말 → 그래도 없으면 "기타"
같은 입력이면 늘 같은 id를 돌려준다(동점은 id 순).
"""

import math
import re
import unicodedata
from collections.abc import Callable

from agenthan.core.embedding import validate_embedding
from agenthan.illustration import embedding_index
from agenthan.illustration.library import DEFAULT_IDS, catalog_ids, load_catalog

Embed = Callable[[str], list[float]]

# 메뉴 분류 필드(픽스처의 Cake·Tea·Coffee·Ade…, 한국어 분류)와 메뉴 이름 끝말 → 기본 그림 분류.
# 정규화한 말이 이 끝말로 끝나면 그 분류. 여러 개가 맞으면 가장 긴 끝말이 이긴다
# ("녹차라떼"는 "라떼"보다 긴 "녹차라떼"로 차, "스파게티"는 "티"보다 긴 끝말로 요리).
ENDINGS = {
    "cake": (
        "cake", "케이크", "케익", "dessert", "디저트", "tart", "타르트", "eclair", "에클레어",
        "roll", "롤", "무스", "티라미수", "파블로바",
    ),
    "bakery": (
        "bakery", "베이커리", "bread", "빵", "cookie", "쿠키", "과자", "pastry", "페이스트리",
        "크루아상", "베이글", "머핀", "스콘", "파이", "마카롱", "와플", "도넛", "브라우니",
        "바게트", "사탕",
    ),
    "savory": (
        "main", "food", "dish", "meal", "식사", "요리", "만두", "파스타", "스파게티", "샐러드",
        "샌드위치", "피자", "볶음밥", "덮밥", "커틀릿", "버거",
    ),
    "soup": ("soup", "수프", "스프", "국", "탕", "찌개"),
    "coffee": (
        "coffee", "커피", "latte", "라떼", "아메리카노", "에스프레소", "카푸치노", "모카",
        "마끼아또", "마키아토", "콜드브루",
    ),
    "tea": ("tea", "티", "차", "홍차", "녹차", "밀크티", "녹차라떼", "말차라떼", "티라떼"),
    "ade": (
        "ade", "에이드", "juice", "주스", "쥬스", "smoothie", "스무디", "beverage", "drink",
        "음료", "소다", "펀치", "모히토",
    ),
}


# 임베딩 단계 기준값. 11/21 CLOVA 임베딩 v2로 짧은 메뉴 이름을 재 보니 관계없는 이름끼리도
# 0.5~0.67이 나왔다(피로쉬키→오레쉬키 0.666, 핫초코→초코 메도빅 0.650, 에스프레소→미니 에클레어
# 0.616, 가나다→아메리카노 0.536). 맞는 짝은 0.68 이상(수제 레모네이드→레몬에이드 0.683, 블리니→블린
# 0.734, 카페 라테→카페라떼 0.770, 치즈케이크→바닐라 치즈케이크 0.796, 보르쉬→보르시 0.834).
SIMILARITY_THRESHOLD = 0.68

# 분류 단서가 있을 때 임베딩 후보를 좁히는 무리. 케이크·빵, 요리·수프는 서로 넘나든다.
GROUPS = {
    "cake": "dessert",
    "bakery": "dessert",
    "savory": "meal",
    "soup": "meal",
    "coffee": "coffee",
    "tea": "tea",
    "ade": "ade",
}


def normalize(text: str) -> str:
    """대소문자·전각/반각을 맞추고 공백·기호를 지운다("초코 메도빅!" → "초코메도빅")."""
    return re.sub(r"[\W_]", "", unicodedata.normalize("NFKC", text).casefold())


def _exact(key: str) -> str | None:
    for row in sorted(load_catalog(), key=lambda r: r["id"]):
        if key in {normalize(n) for n in (row["name"], *row["aliases"])}:
            return row["id"]
    return None


def _keyword(key: str) -> str | None:
    found = [
        (len(normalize(word)), row["id"])
        for row in load_catalog()
        for word in row["keywords"]
        if normalize(word) and normalize(word) in key
    ]
    if not found:
        return None
    return min(found, key=lambda match: (-match[0], match[1]))[1]


def _by_ending(key: str) -> str | None:
    found = [
        (len(normalize(ending)), category)
        for category, endings in ENDINGS.items()
        for ending in endings
        if key.endswith(normalize(ending))
    ]
    if not found:
        return None
    return DEFAULT_IDS[min(found, key=lambda match: (-match[0], match[1]))[1]]


def _cosine(left: list[float], right: list[float]) -> float:
    norm = math.hypot(*left) * math.hypot(*right)
    return math.fsum(a * b for a, b in zip(left, right, strict=True)) / norm


def _nearest(name: str, clue: str | None, embed: Embed) -> str | None:
    """임베딩으로 가장 가까운 그림(기준값 이상일 때만). 실패하면 None, 예외는 밖으로 내지 않는다."""
    category = {v: k for k, v in DEFAULT_IDS.items()}.get(clue or "")
    group = GROUPS.get(category or "")
    try:
        index = embedding_index.load_index()
        candidates = [
            row["id"]
            for row in load_catalog()
            if row["id"] in index
            and row["id"] != DEFAULT_IDS["other"]
            and (group is None or GROUPS.get(row["category"]) == group)
        ]
        if not candidates:
            return None
        query = validate_embedding(embed(name.strip()))
        score, found = min(
            (-_cosine(query, index[key]), key) for key in candidates  # 동점은 id 순
        )
    except Exception:  # noqa: BLE001 — 임베딩이 무슨 이유로 실패해도 그림은 빠지지 않는다
        return None
    return found if -score >= SIMILARITY_THRESHOLD else None


def pick_illustration(
    name: str, category: str | None = None, embed: Embed | None = None
) -> str:
    """메뉴 이름 → 그림 id. 늘 카탈로그에 있는 id를 돌려주고 예외를 던지지 않는다.

    category: 메뉴 기록의 분류 필드(없으면 None). embed: 메뉴 이름 임베딩 함수(운영은 CLOVA
    임베딩 v2 core.embedding.embed_text, 테스트는 가짜). None이면 임베딩 단계를 건너뛴다.
    정확한 이름·키워드로 정해지면 임베딩을 부르지 않는다.
    """
    key = normalize(name) if isinstance(name, str) else ""
    if key:
        found = _exact(key) or _keyword(key)
        if found:
            return found
    clue = None
    if isinstance(category, str) and normalize(category):
        clue = _by_ending(normalize(category))
    clue = clue or (_by_ending(key) if key else None)
    if key and embed is not None:
        found = _nearest(name, clue, embed)
        if found:
            return found
    return clue or DEFAULT_IDS["other"]


def once_per_name(embed: Embed | None) -> Embed | None:
    """같은 이름은 한 번만 임베딩하고, 한 번 실패하면 그 뒤로는 부르지 않는 임베딩 함수.

    메뉴 여러 개를 한꺼번에 고를 때 쓴다(임베딩 서버가 멈춰도 메뉴마다 기다리지 않게)."""
    if embed is None:
        return None
    vectors: dict[str, list[float]] = {}
    failed = False

    def wrapped(text: str) -> list[float]:
        nonlocal failed
        if failed:
            raise RuntimeError("이번 묶음에서 임베딩이 이미 실패했어요")
        if text not in vectors:
            try:
                vectors[text] = embed(text)
            except Exception:
                failed = True
                raise
        return vectors[text]

    return wrapped


def assign_missing(menus: list[dict], embed: Embed | None = None) -> bool:
    """그림 id가 없거나 카탈로그에 없는 메뉴에 그림을 정해 넣는다. 하나라도 넣었으면 True.

    메뉴 이름은 실행 저장소의 name, 픽스처의 name_ko. 분류는 category(있을 때만).
    그림 id가 이미 있는 메뉴는 임베딩을 부르지 않는다(세션을 다시 열어도 같은 그림)."""
    known = catalog_ids()
    embed = once_per_name(embed)
    changed = False
    for menu in menus:
        if menu.get("illustration") in known:
            continue
        name = menu.get("name") or menu.get("name_ko") or ""
        menu["illustration"] = pick_illustration(name, menu.get("category"), embed)
        changed = True
    return changed
