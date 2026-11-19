"""픽업 시간 파서. 실측 문장 40개 + 모호성·지금·당일 고정 (티켓 04).

`now`는 고정값이다. 날짜는 모두 2026-11-08(KST).
"""

from datetime import datetime

import pytest

from agenthan.voice.store_config import KST
from agenthan.voice.timeparse import PickupParse, parse_pickup


def at(hm: str) -> datetime:
    h, m = map(int, hm.split(":"))
    return datetime(2026, 11, 8, h, m, tzinfo=KST)


# (문장, 기준 시각, 기대 시각 또는 None). 티켓 04 실측 표와 같은 목록이다.
SENTENCES = [
    ("6시 반", "15:00", "18:30"),
    ("여섯 시 반", "15:00", "18:30"),
    ("이따 7시", "15:00", "19:00"),
    ("저녁 일곱 시", "15:00", "19:00"),
    ("20분 뒤", "15:00", "15:20"),
    ("30분 있다가", "15:00", "15:30"),
    ("지금 갈게요", "15:00", "15:00"),
    ("한 시간 후에", "15:00", "16:00"),
    ("5시 15분", "15:00", "17:15"),
    ("다섯 시 십오 분", "15:00", "17:15"),
    ("오후 두 시", "11:00", "14:00"),
    ("두 시", "11:00", "14:00"),
    ("곧 갈게요", "15:00", "15:00"),
    ("6시쯤", "15:00", "18:00"),
    ("일곱 시 반쯤 갈게요", "15:00", "19:30"),
    ("8시 10분", "15:00", "20:10"),
    ("열한 시 반", "11:00", "11:30"),
    ("열두 시", "11:00", "12:00"),
    ("4시 45분", "15:00", "16:45"),
    ("네 시 사십오 분", "15:00", "16:45"),
    ("1시간 반 뒤", "15:00", "16:30"),
    ("한 시간 반 있다가", "15:00", "16:30"),
    ("10분 후에 갈게요", "15:00", "15:10"),
    ("십 분 뒤", "15:00", "15:10"),
    ("오후 5시", "15:00", "17:00"),
    ("저녁 6시 반", "15:00", "18:30"),
    ("이따 다섯 시에 갈게요", "15:00", "17:00"),
    ("좀 이따 6시", "15:00", "18:00"),
    ("18시 30분", "15:00", "18:30"),
    ("6:30", "15:00", "18:30"),
    ("지금 바로 갈게요", "15:00", "15:00"),
    ("이따 갈게요", "15:00", None),
    ("삼십 분 뒤", "15:00", "15:30"),
    ("세 시 반", "15:00", "15:30"),
    ("오전 11시", "09:00", "11:00"),
    ("아홉 시 오 분", "15:00", "21:05"),
    ("다섯시반", "15:00", "17:30"),
    ("여섯시 이십분", "15:00", "18:20"),
    ("15분 있다 갈게요", "15:00", "15:15"),
    ("이따가 일곱 시쯤요", "15:00", "19:00"),
]


@pytest.mark.parametrize(("text", "now", "expected"), SENTENCES)
def test_sentence(text: str, now: str, expected: str | None) -> None:
    got = parse_pickup(text, at(now)).time
    assert got == (at(expected) if expected else None)


def test_at_least_25_sentences() -> None:
    assert len(SENTENCES) >= 25


# ---------------------------------------------------------------- 지금 / 곧


@pytest.mark.parametrize("text", ["지금 갈게요", "곧 갈게요", "지금 바로 갈게요", "바로 갈게요",
                                  "금방 갈게요", "잠시 후에 갈게요", "지금이요"])
def test_asap(text: str) -> None:
    assert parse_pickup(text, at("15:00")) == PickupParse(at("15:00"), asap=True)


def test_asap_drops_seconds() -> None:
    now = datetime(2026, 11, 8, 15, 7, 42, 123, tzinfo=KST)
    assert parse_pickup("지금 갈게요", now).time == at("15:07")


@pytest.mark.parametrize("text", ["이따 갈게요", "좀 이따요", "이따가요", "있다가 갈게요"])
def test_bare_later_is_unknown(text: str) -> None:
    assert parse_pickup(text, at("15:00")).time is None


def test_relative_is_not_asap() -> None:
    assert parse_pickup("20분 뒤", at("15:00")).asap is False


# ---------------------------------------------------------------- 오전·오후 모호성


def test_both_readings_ahead_is_ambiguous() -> None:
    # 9시에 "열한 시": 오전 11시도 오후 11시도 아직 안 지났다
    assert parse_pickup("열한 시", at("09:00")) == PickupParse(at("11:00"), ambiguous=True)


def test_early_morning_two_oclock_is_ambiguous() -> None:
    now = datetime(2026, 11, 8, 1, 0, tzinfo=KST)
    assert parse_pickup("두 시", now) == PickupParse(at("02:00"), ambiguous=True)


@pytest.mark.parametrize(("text", "now"), [("6시 반", "15:00"), ("두 시", "11:00"),
                                           ("열두 시", "11:00")])
def test_only_one_reading_ahead_is_not_ambiguous(text: str, now: str) -> None:
    assert parse_pickup(text, at(now)).ambiguous is False


@pytest.mark.parametrize("text", ["오전 11시", "오후 11시", "저녁 7시", "19시"])
def test_explicit_meridiem_is_not_ambiguous(text: str) -> None:
    assert parse_pickup(text, at("09:00")).ambiguous is False


# ---------------------------------------------------------------- 당일 고정


@pytest.mark.parametrize(("text", "now", "expected"), [
    ("두 시", "15:00", "14:00"),  # 지난 시각도 당일로 둔다. 판단은 가게 규칙이 한다
    ("오후 2시", "15:00", "14:00"),
    ("7시", "20:00", "19:00"),
    ("오전 9시", "15:00", "09:00"),
])
def test_same_day_even_if_past(text: str, now: str, expected: str) -> None:
    assert parse_pickup(text, at(now)).time == at(expected)


@pytest.mark.parametrize("text", ["내일 6시", "저녁에 갈게요", "메도빅 하나요", ""])
def test_no_time_of_day(text: str) -> None:
    assert parse_pickup(text, at("15:00")).time is None


def test_time_inside_longer_sentence() -> None:
    assert parse_pickup("메도빅 하나 주시고 6시 반에 갈게요", at("15:00")).time == at("18:30")
