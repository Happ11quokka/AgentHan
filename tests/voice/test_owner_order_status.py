"""사장님 허브의 손님 주문 현황 (전화 메뉴·주문 현황·쿠폰 티켓 04): 공개 인터페이스·가짜 시계.

통화 뒤 확인 요청(통화 중 대기 0)은 처음부터 통화 뒤 단계로 열리고, 결정은 통화로 가지 않고 바로
굳는다. 주문 현황 단계는 사장님 결정을 따른다: 확인 대기 → 가게 확인 중, 수락 → 확정(늦춘 수락은
바뀐 시각과 원래 시각), 거절·자동 취소 → 주문 취소.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from agenthan.core.contracts import Order, OrderLine
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.call.fakes import FakeClock
from agenthan.voice.dialog.approval import ApprovalRequest, OwnerDecision
from agenthan.voice.owner.hub import OwnerHub, UnknownCard

NOW = datetime.fromisoformat("2026-11-08T16:02:00+09:00")
CALL = "call-1"


def _placed() -> tuple[OwnerHub, FakeClock, list[OwnerDecision]]:
    """통화 뒤 확인 요청 1번을 열고 주문서가 넘어온 뒤 통화가 끝난 허브."""
    clock = FakeClock(NOW)
    hub = OwnerHub(store=FakeStoreKnowledge.from_fixture(), clock=clock)
    order = Order(id=CALL, lines=[OrderLine("m01", 2, name="메도빅", unit_price=7_500)],
                  pickup_time="2026-11-08T18:30:00+09:00", status="confirmed",
                  created_at=NOW.isoformat())
    told: list[OwnerDecision] = []
    hub.call_started(CALL)
    hub.open_approval(CALL, ApprovalRequest(1, order, in_call=False), told.append)
    hub.submit(order)
    hub.call_ended(CALL, "confirmed")
    return hub, clock, told


def test_a_placed_order_is_checking_and_shows_its_lines_amount_and_pickup() -> None:
    hub, _, _ = _placed()
    status = hub.order_status(CALL)

    assert status["stage"] == "checking"
    assert [(line["name"], line["quantity"], line["subtotal"]) for line in status["lines"]] == [
        ("메도빅", 2, 15_000)]
    assert status["amount"] == {"subtotal": 15_000, "total": 15_000}
    assert (status["pickup"]["label"], status["pickup"]["original_label"]) == ("18:30", "")


def test_an_accept_after_the_call_confirms_without_telling_the_call() -> None:
    hub, _, told = _placed()
    hub.accept(CALL)

    assert hub.order_status(CALL)["stage"] == "confirmed"
    assert told == []  # 통화 뒤 확인 요청의 결정은 통화로 가지 않는다


def test_a_later_pickup_shows_the_new_time_with_the_original() -> None:
    hub, _, _ = _placed()
    hub.accept(CALL, delay_min=15)
    status = hub.order_status(CALL)

    assert status["stage"] == "confirmed"
    assert (status["pickup"]["label"], status["pickup"]["original_label"]) == ("18:45", "18:30")


def test_a_reject_or_five_silent_minutes_cancel_the_order() -> None:
    hub, _, _ = _placed()
    hub.reject(CALL, "busy")
    assert hub.order_status(CALL)["stage"] == "cancelled"

    hub, clock, _ = _placed()
    clock.set(NOW + timedelta(minutes=5))  # 조회가 마감 지난 카드를 먼저 자동 취소한다
    assert hub.order_status(CALL)["stage"] == "cancelled"


def test_an_order_number_the_hub_does_not_know_is_not_found() -> None:
    hub, _, _ = _placed()
    with pytest.raises(UnknownCard):
        hub.order_status("call-nope")
