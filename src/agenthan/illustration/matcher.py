"""메뉴 이름(과 있으면 분류)으로 대표 그림 id를 고른다. HCX는 부르지 않는다.

순서(spec):
  1. 정규화한 이름(공백·기호 제거)이 카탈로그 이름·별칭과 정확히 같으면 그 그림
  2. 이름에 카탈로그 키워드가 들어 있으면 가장 긴 키워드의 그림
  3. (티켓 06) CLOVA 임베딩 코사인 유사도 — embed 인자는 지금 받기만 하고 쓰지 않는다
  4. 분류 기본 그림: 메뉴 분류 필드 → 없으면 이름 끝말 → 그래도 없으면 "기타"
같은 입력이면 늘 같은 id를 돌려준다(동점은 id 순).
"""

import re
import unicodedata
from collections.abc import Callable

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


def pick_illustration(
    name: str, category: str | None = None, embed: Embed | None = None
) -> str:
    """메뉴 이름 → 그림 id. 늘 카탈로그에 있는 id를 돌려주고 예외를 던지지 않는다.

    category: 메뉴 기록의 분류 필드(없으면 None). embed: 임베딩 함수(티켓 06에서 쓴다. 테스트는
    가짜, 운영은 CLOVA 임베딩 v2).
    """
    del embed  # 티켓 06: 키워드 다음, 분류 기본 그림 전에 임베딩 단계를 넣는다
    key = normalize(name) if isinstance(name, str) else ""
    if key:
        found = _exact(key) or _keyword(key)
        if found:
            return found
    if isinstance(category, str) and normalize(category):
        found = _by_ending(normalize(category))
        if found:
            return found
    return (_by_ending(key) if key else None) or DEFAULT_IDS["other"]


def assign_missing(menus: list[dict], embed: Embed | None = None) -> bool:
    """그림 id가 없거나 카탈로그에 없는 메뉴에 그림을 정해 넣는다. 하나라도 넣었으면 True.

    메뉴 이름은 실행 저장소의 name, 픽스처의 name_ko. 분류는 category(있을 때만)."""
    known = catalog_ids()
    changed = False
    for menu in menus:
        if menu.get("illustration") in known:
            continue
        name = menu.get("name") or menu.get("name_ko") or ""
        menu["illustration"] = pick_illustration(name, menu.get("category"), embed)
        changed = True
    return changed
