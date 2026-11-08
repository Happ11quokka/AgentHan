"""가게 픽업 규칙 (spec D7, 티켓 04). 파싱된 시각을 보고 엔진이 할 일을 정한다.

검사 순서: 못 읽음 → 지금 → 영업 전 → 영업 종료 뒤 → 준비 시간보다 이름 → 확인이 필요함 → 수락.
날짜는 당일로 고정한다(`timeparse`가 이미 맞춰 준다).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Literal

from agenthan.voice.store_config import StoreConfig
from agenthan.voice.timeparse import PickupParse

VerdictKind = Literal[
    "unclear",  # 시각을 못 읽음 → 다시 묻기
    "accept",  # 그대로 잡는다
    "asap",  # "지금 갈게요" → 준비 시간 뒤로 잡는다 (at)
    "before_open",  # 문 열기 전 → 다시 묻기
    "after_close",  # 문 닫은 뒤 → 거절하고 다시 묻기
    "too_early",  # 준비 시간보다 이름 → at을 제안
    "confirm",  # 오전·오후 모호 또는 3시간 넘게 뒤 → at이 맞는지 확인
]


@dataclass(frozen=True)
class PickupVerdict:
    kind: VerdictKind
    at: datetime | None = None


def judge_pickup(parsed: PickupParse, now: datetime, config: StoreConfig) -> PickupVerdict:
    if parsed.time is None:
        return PickupVerdict("unclear")
    earliest = earliest_pickup(now, config)
    close = today_at(now, config.close_time)
    if parsed.asap:
        if earliest > close:
            return PickupVerdict("after_close")
        return PickupVerdict("asap", earliest)
    t = parsed.time
    if t < today_at(now, config.open_time):
        return PickupVerdict("before_open")
    if t > close:
        return PickupVerdict("after_close")
    if t < now + timedelta(minutes=config.prep_minutes):
        if earliest > close:
            return PickupVerdict("after_close")
        return PickupVerdict("too_early", earliest)
    if parsed.ambiguous or t - now > timedelta(minutes=config.confirm_after_minutes):
        return PickupVerdict("confirm", t)
    return PickupVerdict("accept", t)


def earliest_pickup(now: datetime, config: StoreConfig) -> datetime:
    """지금 + 준비 시간을 `pickup_step_minutes` 단위로 올린 시각. 17:10 + 15분 → 17:25."""
    ready = now.replace(second=0, microsecond=0) + timedelta(minutes=config.prep_minutes)
    if now.second or now.microsecond:
        ready += timedelta(minutes=1)
    step = config.pickup_step_minutes
    return ready + timedelta(minutes=-ready.minute % step)


def today_at(now: datetime, at: time) -> datetime:
    """`now`와 같은 날의 `at` 시각."""
    return now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
