"""가게 픽업 규칙: 준비 시간, 영업시간, 확인이 필요한 시각 (티켓 04)."""

from datetime import datetime

import pytest

from agenthan.voice.dialog.pickup import PickupVerdict, judge_pickup
from agenthan.voice.store_config import KST, StoreConfig
from agenthan.voice.timeparse import PickupParse

CONFIG = StoreConfig(store_name="테스트 가게")  # 준비 15분, 10:00~21:00, 3시간 넘으면 확인


def at(hm: str) -> datetime:
    h, m = map(int, hm.split(":"))
    return datetime(2026, 11, 8, h, m, tzinfo=KST)


def judge(parsed: PickupParse, now: str) -> PickupVerdict:
    return judge_pickup(parsed, at(now), CONFIG)


def test_unclear() -> None:
    assert judge(PickupParse(None), "15:00") == PickupVerdict("unclear")


def test_accept() -> None:
    assert judge(PickupParse(at("16:30")), "15:00") == PickupVerdict("accept", at("16:30"))


def test_exactly_prep_time_is_accepted() -> None:
    assert judge(PickupParse(at("15:15")), "15:00").kind == "accept"


@pytest.mark.parametrize(("pickup", "now", "offer"), [
    ("17:15", "17:10", "17:25"),  # spec §7 예시
    ("17:10", "17:10", "17:25"),
    ("15:00", "15:02", "15:20"),  # 15:17 → 5분 단위로 올림
    ("14:00", "15:00", "15:15"),  # 이미 지난 시각
])
def test_too_early_offers_prep_time(pickup: str, now: str, offer: str) -> None:
    assert judge(PickupParse(at(pickup)), now) == PickupVerdict("too_early", at(offer))


def test_asap_takes_prep_time() -> None:
    assert judge(PickupParse(at("17:10"), asap=True), "17:10") == PickupVerdict("asap", at("17:25"))


def test_after_close() -> None:
    assert judge(PickupParse(at("21:30")), "17:00").kind == "after_close"


def test_closing_time_itself_is_accepted() -> None:
    assert judge(PickupParse(at("21:00")), "18:30").kind == "accept"


def test_asap_too_close_to_closing() -> None:
    assert judge(PickupParse(at("20:50"), asap=True), "20:50").kind == "after_close"


def test_too_early_offer_after_close() -> None:
    assert judge(PickupParse(at("20:55")), "20:50").kind == "after_close"


def test_before_open() -> None:
    assert judge(PickupParse(at("09:30")), "08:00").kind == "before_open"


def test_ambiguous_needs_confirm() -> None:
    parsed = PickupParse(at("11:00"), ambiguous=True)
    assert judge(parsed, "09:00") == PickupVerdict("confirm", at("11:00"))


def test_more_than_3_hours_ahead_needs_confirm() -> None:
    assert judge(PickupParse(at("14:00")), "10:30") == PickupVerdict("confirm", at("14:00"))


def test_exactly_3_hours_ahead_is_accepted() -> None:
    assert judge(PickupParse(at("20:00")), "17:00").kind == "accept"
