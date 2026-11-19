"""검색 전처리: 표기 정규화, 말 바꾸기(정정) 처리, 여러 메뉴 나누기."""

from __future__ import annotations

import re
import unicodedata

from agenthan.rag import rules

_PUNCT = re.compile(r"[^\w\s]|_", re.UNICODE)
_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    """소문자·NFKC·표기 통일(케잌→케이크)·문장부호 제거·공백 하나로."""
    t = unicodedata.normalize("NFKC", text or "").lower()
    for src, dst in rules.SPELLING:
        t = t.replace(src, dst)
    t = _PUNCT.sub(" ", t)
    return _SPACES.sub(" ", t).strip()


def key(text: str) -> str:
    """비교용 열쇠: 정규화 후 공백까지 지운 글자. "꿀 케이크" == "꿀케잌" """
    return normalize(text).replace(" ", "")


def contains_any(text_key: str, words) -> bool:
    """공백 없앤 질문(text_key)에 words 중 하나라도 들어 있는지."""
    return any(key(w) and key(w) in text_key for w in words)


def drop_correction(text: str) -> str:
    """말을 바꾼 문장이면 마지막 정정 표시 뒤쪽만 남긴다. 뒤쪽이 비면 원문 그대로."""
    norm = normalize(text)
    cut = -1
    for marker in rules.CORRECTION_MARKERS:
        m = normalize(marker)
        # 표시 뒤에 붙은 말끝까지 한 덩어리로 자른다: "아 잠깐만요", "말고요"
        # '말고'는 앞말에도 붙는다: "나폴레옹말고 메도빅"
        start = r"" if m == "말고" else r"(?:^|\s)"
        for found in re.finditer(start + re.escape(m) + r"\S*", norm):
            cut = max(cut, found.end())
    if cut < 0:
        return norm
    rest = norm[cut:].strip()
    return rest or norm


def split_items(text: str) -> list[str]:
    """여러 메뉴를 한 번에 말한 문장을 조각으로 나눈다. 정규화된 문장을 받는다."""
    # '랑'·'하고'는 앞말에 붙어서 나온다("하나랑"). 그래서 말 끝(뒤에 공백)에 붙은 것만 자른다.
    seps = sorted(
        {normalize(s) for s in rules.ITEM_SEPARATORS if normalize(s)}, key=len, reverse=True
    )
    if not seps:
        return [text] if text else []
    pattern = r"(?:" + "|".join(re.escape(s) for s in seps) + r")(?=\s)"
    return [p.strip() for p in re.split(pattern, text) if p.strip()]
