"""NLU에 줄 가게 메뉴 정보 (5단계 티켓 05). 가게 데이터에서 메뉴마다 한 줄을 만든다.

예전에는 메뉴 이름만 줬다. 그래서 HCX가 "샤워 크림 빼"가 블린의 옵션인 줄 모르고 메뉴 빼기로
봤고, "아이스"를 메뉴 이름 쪽에 넣었다. 그 가게의 다른 이름·꼭 고를 것·옵션을 같이 주면
가게마다 규칙을 더하지 않고 LLM이 그 가게 말로 해석한다.

    메도빅 | 다른 이름: 꿀케이크, 허니케이크 | 옵션: 홀케이크 변경
    아메리카노 | 꼭 고를 것 온도: 따뜻하게/차갑게 | 옵션: 디카페인 변경, 샷 추가
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from agenthan.core.contracts import MenuItem
from agenthan.voice.store_config import RequiredOption


def menu_catalog(
    menu: Iterable[MenuItem], required: Mapping[str, Sequence[RequiredOption]]
) -> tuple[str, ...]:
    return tuple(_line(item, required.get(item.id, ())) for item in menu)


def _line(item: MenuItem, required: Sequence[RequiredOption]) -> str:
    parts = [item.name_ko + (" (오늘 품절)" if item.sold_out else "")]
    if item.aliases:
        parts.append(f"다른 이름: {', '.join(item.aliases)}")
    for group in required:
        parts.append(f"꼭 고를 것 {group.name}: {'/'.join(c.name for c in group.choices)}")
    if item.options:
        names = [o.name + ("" if o.available else " (오늘 안 됨)") for o in item.options]
        parts.append(f"옵션: {', '.join(names)}")
    return " | ".join(parts)
