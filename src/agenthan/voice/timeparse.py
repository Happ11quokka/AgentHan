"""픽업 시간 파싱. 엔진은 `parse_pickup(text, now)`만 부른다 (spec D7).

시각 표현은 `korean-datetime`이 읽는다(고유어·한자어 수사, "N분 뒤", "이따 7시", 오전·오후 표시).
라이브러리는 이 파일 밖으로 드러나지 않는다. 이 파일은 그 결과를 우리 규칙에 맞춘다.

- 날짜는 당일로 고정한다. 라이브러리가 내일로 넘긴 시각("6:30", 15시에 "두 시")은 오늘로 되돌린다.
  지난 시각이어도 그대로 돌려준다. 준비 시간·영업시간 판단은 가게 규칙(`dialog/pickup.py`)이 한다
- "지금/곧/바로/금방/잠시 후"는 `asap`이다. "이따" 혼자는 시각이 아니다(None)
- `ambiguous`: 오전·오후 표시 없이 1~12시를 말했고, 두 읽기가 모두 아직 안 지났을 때만 참이다
- "저녁에"처럼 시각이 없는 말, "내일 6시"처럼 다른 날은 None
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

import korean_datetime as kd

_HALF_DAY = timedelta(hours=12)

# 라이브러리가 모르는 "곧", "바로", "금방"과 "지금이요". 뒤에 가는 말이 오거나 문장이 끝나야 한다.
_ASAP = re.compile(
    r"(?:지금(?:\s*바로)?|바로|곧|금방|잠시\s*(?:후|뒤))"
    r"(?=\s*(?:에\s*)?(?:갈|가|출발|도착|들|방문|이요|요|$))"
)
# 혼자 쓰인 "이따". 라이브러리는 이를 '지금'으로 읽지만 손님은 "나중에"라는 뜻이다.
_LATER_ONLY = re.compile(r"(?:좀\s*)?(?:이따가?|있다가)")
# "15분 있다 갈게요": 라이브러리는 "있다가"만 안다.
_ITTA = re.compile(r"있다(?!가)")
_CLOCK_WORD = re.compile(r"(?<!잠)시(?!간)|:")  # "6시", "6:30". "잠시", "한 시간"은 아니다
_MERIDIEM_WORD = re.compile(r"오전|오후|아침|새벽|낮|저녁|밤")


@dataclass(frozen=True)
class PickupParse:
    time: datetime | None
    ambiguous: bool = False  # 오전·오후가 모호해서 확인이 필요한가
    asap: bool = False  # "지금 갈게요". time은 now(분 단위)


def parse_pickup(text: str, now: datetime) -> PickupParse:
    """당일 픽업 시각. 못 읽으면 `PickupParse(None)`."""
    now = now.replace(second=0, microsecond=0)
    found = kd.parse(_ITTA.sub("있다가", text), now=now)
    if found is None:
        return PickupParse(now, asap=True) if _ASAP.search(text) else PickupParse(None)

    span = found.text.strip()
    if found.start == now and not _CLOCK_WORD.search(span):  # 지금·이따·잠시 후
        if _LATER_ONLY.fullmatch(span):
            return PickupParse(None)
        return PickupParse(now, asap=True)
    if found.kind is kd.Kind.TIME and not _CLOCK_WORD.search(span):  # "저녁에"
        return PickupParse(None)

    at = found.start.astimezone(now.tzinfo).replace(second=0, microsecond=0)
    meridiem_unknown = bool(
        {kd.Ambiguity.MERIDIEM, kd.Ambiguity.NOON_OR_MIDNIGHT} & set(found.ambiguities)
    ) or (":" in span and at.hour <= 12 and not _MERIDIEM_WORD.search(span))  # "6:30"
    if at.date() != now.date():
        if found.kind is not kd.Kind.TIME:  # "내일 6시", 자정 넘는 "한 시간 뒤"
            return PickupParse(None)
        at = datetime.combine(now.date(), at.timetz())
        if meridiem_unknown and at.hour < 12 and at < now:
            at += _HALF_DAY
    return PickupParse(at, ambiguous=meridiem_unknown and _other_reading_ahead(at, now))


def _other_reading_ahead(at: datetime, now: datetime) -> bool:
    """오전·오후를 바꾼 시각도 오늘 안이고 아직 안 지났나."""
    other = at - _HALF_DAY if at.hour >= 12 else at + _HALF_DAY
    return other.date() == now.date() and other >= now and at >= now
