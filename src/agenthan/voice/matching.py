"""메뉴 매처. 엔진은 `MenuMatcher.match()`의 판정만 본다 (spec §6, D5).

지금은 티켓 01의 임시 구현이다.
공백·문장부호를 뺀 이름·별칭 정확 일치만 `accept`, 나머지는 `reject`.
티켓 02가 이 파일을 바꾼다(자모 편집거리, confirm/choose/sold_out 판정).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from agenthan.core.contracts import MenuItem

MatchDecision = Literal["accept", "confirm", "choose", "reject", "sold_out"]

_NOISE = re.compile(r"[\s.,!?~]+")


def normalize(text: str) -> str:
    return _NOISE.sub("", text)


@dataclass(frozen=True)
class MatchResult:
    decision: MatchDecision
    item: MenuItem | None = None  # accept·confirm·sold_out일 때
    candidates: tuple[MenuItem, ...] = ()  # choose일 때 후보들
    score: float = 0.0


class MenuMatcher:
    def __init__(self, menu: list[MenuItem]) -> None:
        self._menu = list(menu)

    def match(self, menu_text: str, menu_guess: str = "") -> MatchResult:
        for query in (menu_text, menu_guess):
            q = normalize(query)
            if not q:
                continue
            for item in self._menu:
                if q in {normalize(n) for n in (item.name_ko, *item.aliases)}:
                    return MatchResult("accept", item, score=1.0)
        return MatchResult("reject")
