"""티켓 01 임시 파서의 최소 동작. 티켓 04가 파서를 바꾸면서 이 테스트를 넓히거나 교체한다."""

from datetime import datetime

from agenthan.voice.store_config import KST
from agenthan.voice.timeparse import parse_pickup

NOW = datetime(2026, 11, 8, 15, 0, tzinfo=KST)


def test_half_hour_in_afternoon() -> None:
    assert parse_pickup("6시 반", NOW).time == datetime(2026, 11, 8, 18, 30, tzinfo=KST)


def test_minutes() -> None:
    assert parse_pickup("5시 15분", NOW).time == datetime(2026, 11, 8, 17, 15, tzinfo=KST)


def test_24h_hour_kept() -> None:
    assert parse_pickup("19시", NOW).time == datetime(2026, 11, 8, 19, 0, tzinfo=KST)


def test_unparseable() -> None:
    assert parse_pickup("이따 갈게요", NOW).time is None
