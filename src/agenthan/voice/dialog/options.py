"""옵션 말 → `MenuOption` 찾기. 메뉴처럼 코드가 최종 판정한다 (spec D5).

NLU는 손님이 말한 옵션 조각(`Act.option_text`)만 넘긴다. 여기서 그 메뉴가 받을 수 있는
옵션(필수 선택지 + 선택 옵션) 가운데 어느 것인지 고른다.

옵션 이름 하나에서 알아들을 말 여러 개를 만든다.
- 이름 그대로: "디카페인 변경", "샷 추가"
- 끝의 "변경"을 뗀 말: "디카페인", "홀케이크"
- "~빼기"는 "~빼", "~없이"도 받는다: "사워크림 빼", "사워크림 없이"
- 온도처럼 다르게 부르는 말은 `_SYNONYMS`에 둔다: "아이스" → 차갑게
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from agenthan.core.contracts import MenuOption

_NOISE = re.compile(r"[\s.,!?~]+")

_SYNONYMS: dict[str, tuple[str, ...]] = {
    "차갑게": ("차가운", "차게", "아이스", "시원하게", "시원한"),
    "따뜻하게": ("따뜻한", "따듯하게", "따듯한", "뜨겁게", "뜨거운", "따뜻이", "핫"),
}


def _normalize(text: str) -> str:
    return _NOISE.sub("", text)


def option_keys(name: str) -> tuple[str, ...]:
    """옵션 이름 → 알아들을 말(정규화한 것). 긴 말부터."""
    base = _normalize(name)
    keys = {base, *(_normalize(s) for s in _SYNONYMS.get(name, ()))}
    if base.endswith("변경") and len(base) > 2:
        keys.add(base[: -len("변경")])
    if base.endswith("빼기") and len(base) > 2:
        stem = base[: -len("빼기")]
        keys |= {stem + "빼", stem + "없이"}
    return tuple(sorted(keys, key=len, reverse=True))


def find_options(candidates: Iterable[MenuOption], text: str) -> list[MenuOption]:
    """`text`에 나온 옵션을 말한 순서대로. 하나도 없으면 빈 목록."""
    said = _normalize(text)
    if not said:
        return []
    found: list[tuple[int, MenuOption]] = []
    for option in candidates:
        positions = [said.find(k) for k in option_keys(option.name) if k and k in said]
        if positions:
            found.append((min(positions), option))
    found.sort(key=lambda pair: pair[0])
    return [option for _, option in found]
