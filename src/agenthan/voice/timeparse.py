"""픽업 시간 파싱. 엔진은 `parse_pickup(text, now)`만 부른다.

지금은 티켓 01의 임시 구현이다: "N시", "N시 반", "N시 M분"(아라비아 숫자)만 읽는다.
티켓 04가 이 파일을 통째로 바꾼다. 인터페이스(`PickupParse`, `parse_pickup`)는 유지한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

_TIME = re.compile(r"(\d{1,2})\s*시(?:\s*(반)|\s*(\d{1,2})\s*분)?")


@dataclass(frozen=True)
class PickupParse:
    time: datetime | None
    ambiguous: bool = False  # 오전·오후가 모호해서 확인이 필요한가


def parse_pickup(text: str, now: datetime) -> PickupParse:
    """당일 픽업 시각. 12 미만 시각이 지금보다 이르면 오후로 본다."""
    m = _TIME.search(text)
    if m is None:
        return PickupParse(None)
    hour = int(m.group(1))
    minute = 30 if m.group(2) else int(m.group(3) or 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return PickupParse(None)
    at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if hour < 12 and at < now:
        at = at.replace(hour=hour + 12)
    return PickupParse(at)
