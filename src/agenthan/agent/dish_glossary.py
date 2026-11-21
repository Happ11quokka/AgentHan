"""외국 음식 사전 찾기 (dish_glossary.json).

메뉴판에서 읽은 이름이 사전에 있으면 모델을 부르지 않고 사전의 한국어 이름·원어 이름·설명을 쓴다.
HCX는 시르니키를 "꿀케이크", 콤포트를 "잼"이라고 설명하는 등 외국 음식 지식이 약했다
(2026-11-21 실측). 한국어 이름·원어 이름·별칭이 정확히 맞으면 바로, 원어 이름은 글자 유사도
0.85 이상이면 맞춘다(Медовник → Медовик 0.93). 한글 이름은 유사도로 맞추지 않는다(짧아서 오인).
"""

from __future__ import annotations

import difflib
import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PATH = Path(__file__).with_name("dish_glossary.json")
NATIVE_MIN = 0.85
HANGUL = re.compile(r"[가-힣]")


@dataclass(frozen=True)
class Dish:
    ko: str
    native: str
    description: str


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text).casefold())


@lru_cache(maxsize=1)
def _dishes() -> tuple[tuple[Dish, frozenset[str]], ...]:
    raw = json.loads(PATH.read_text(encoding="utf-8"))["dishes"]
    out = []
    for row in raw:
        dish = Dish(row["ko"], row["native"], row["description"])
        keys = {_norm(k) for k in (row["ko"], row["native"], *row.get("aliases", [])) if k}
        out.append((dish, frozenset(keys)))
    return tuple(out)


def lookup(name: str) -> Dish | None:
    key = _norm(name)
    if not key:
        return None
    for dish, keys in _dishes():
        if key in keys:
            return dish
    if HANGUL.search(name):
        return None
    best, score = None, 0.0
    for dish, _keys in _dishes():
        ratio = difflib.SequenceMatcher(None, key, _norm(dish.native)).ratio()
        if ratio > score:
            best, score = dish, ratio
    return best if score >= NATIVE_MIN else None
