"""손님 취소 (전화 메뉴·주문 현황·쿠폰 티켓 07): 허브 공개 인터페이스·가짜 시계.

손님은 주문 현황에서 두 때만 주문을 취소할 수 있다: 가게가 아직 확인 중일 때(통화 뒤 확인 요청),
늦춘 수락으로 확정됐을 때. 제시간 수락·자동 확정·거절·자동 취소·이미 취소된 주문은 거부하고, 거부
사유 코드로 그 결과를 돌려준다. 손님 취소와 사장님 결정은 "한 주문에 결정 한 번" 규칙을 같이
따른다(먼저 온 쪽이 이긴다).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from agenthan.core.contracts import Order, OrderLine
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.call.fakes import FakeClock
from agenthan.voice.dialog.approval import ApprovalRequest, OwnerDecision
from agenthan.voice.owner.decisions import DecisionRecord
from agenthan.voice.owner.hub import AlreadyDecided, NotCancellable, OwnerHub, UnknownCard

NOW = datetime.fromisoformat("2026-11-08T16:02:00+09:00")
CALL = "call-1"


class _Records:
    def __init__(self) -> None:
        self.records: list[DecisionRecord] = []

    def record(self, decision: DecisionRecord) -> None:
        self.records.append(decision)


def _order() -> Order:
    return Order(id=CALL, lines=[OrderLine("m01", 2, name="메도빅", unit_price=7_500)],
                 pickup_time="2026-11-08T18:30:00+09:00", status="confirmed",
                 created_at=NOW.isoformat())


def _placed() -> tuple[OwnerHub, FakeClock, _Records]:
    """통화 뒤 확인 요청 1번을 열고 주문서가 넘어온 뒤 통화가 끝난 허브 (통화 중 대기 0)."""
    clock = FakeClock(NOW)
    sink = _Records()
    hub = OwnerHub(store=FakeStoreKnowledge.from_fixture(), clock=clock, decision_sink=sink)
    hub.call_started(CALL)
    hub.open_approval(CALL, ApprovalRequest(1, _order(), in_call=False), lambda _: None)
    hub.submit(_order())
    hub.call_ended(CALL, "confirmed")
    return hub, clock, sink


def _refusal(hub: OwnerHub) -> NotCancellable:
    with pytest.raises(NotCancellable) as refused:
        hub.cancel_by_customer(CALL)
    return refused.value


def test_a_checking_order_can_be_cancelled_by_the_customer() -> None:
    hub, clock, sink = _placed()
    assert hub.order_status(CALL)["can_cancel"] is True

    clock.set(NOW + timedelta(seconds=40))
    status = hub.cancel_by_customer(CALL)

    assert (status["stage"], status["outcome"], status["can_cancel"]) == (
        "cancelled", "customer_cancelled", False)
    assert status == hub.order_status(CALL)
    # 사장님 결정 기록에 손님 취소로 남는다(DB `owner_status`)
    assert [(r.owner_status, r.decided_at) for r in sink.records] == [
        ("customer_cancelled", "2026-11-08T16:02:40+09:00")]
    assert hub.customer_texts(CALL) == []  # 손님 스스로 취소했다. 취소 문자는 없다


def test_a_later_pickup_can_be_cancelled_and_the_record_keeps_the_new_time() -> None:
    hub, _, sink = _placed()
    hub.accept(CALL, delay_min=15)
    assert hub.order_status(CALL)["can_cancel"] is True

    status = hub.cancel_by_customer(CALL)

    assert (status["stage"], status["outcome"]) == ("cancelled", "customer_cancelled")
    assert [r.owner_status for r in sink.records] == ["accepted", "customer_cancelled"]
    assert sink.records[-1].owner_pickup_time == "2026-11-08T18:45:00+09:00"


def test_an_on_time_accept_cannot_be_cancelled_on_screen() -> None:
    hub, _, sink = _placed()
    hub.accept(CALL)
    assert hub.order_status(CALL)["can_cancel"] is False

    refused = _refusal(hub)

    assert refused.code == "accepted"
    assert refused.status["stage"] == "confirmed"  # 진 쪽은 이긴 결과를 본다
    assert hub.order_status(CALL)["outcome"] == "accepted"
    assert [r.owner_status for r in sink.records] == ["accepted"]


def test_an_automatically_confirmed_order_cannot_be_cancelled() -> None:
    hub = OwnerHub(store=FakeStoreKnowledge.from_fixture(), clock=FakeClock(NOW))
    hub.submit(_order())  # 사장님 화면 꺼짐: 확인 요청 없이 자동 확정

    assert hub.order_status(CALL)["can_cancel"] is False
    assert _refusal(hub).code == "auto"


@pytest.mark.parametrize(("decide", "code"), [
    (lambda hub: hub.reject(CALL, "busy"), "rejected"),
    (lambda hub: hub.reject(CALL, "out_of_stock", menu_ids=["m01"]), "out_of_stock"),
    (lambda hub: hub.cancel_by_customer(CALL), "customer_cancelled"),
])
def test_a_cancelled_order_refuses_another_cancel_with_its_reason(decide, code) -> None:
    hub, _, _ = _placed()
    decide(hub)

    assert hub.order_status(CALL)["can_cancel"] is False
    refused = _refusal(hub)
    assert refused.code == code
    assert refused.status["stage"] == "cancelled"


def test_an_auto_cancelled_order_refuses_the_cancel_even_if_the_timer_was_late() -> None:
    hub, clock, sink = _placed()
    clock.set(NOW + timedelta(minutes=5))  # 마감 타이머가 아직 안 돌았다

    assert _refusal(hub).code == "expired"
    assert [r.owner_status for r in sink.records] == ["expired"]


def test_an_unknown_order_cannot_be_cancelled() -> None:
    hub, _, _ = _placed()
    with pytest.raises(UnknownCard):
        hub.cancel_by_customer("call-nope")


def test_a_request_still_waiting_inside_the_call_is_cancelled_by_talking() -> None:
    """통화 중 대기(설정으로 켠 가게)의 확인 요청은 통화가 손님 말을 받는다. 화면 취소는 거부."""
    hub = OwnerHub(store=FakeStoreKnowledge.from_fixture(), clock=FakeClock(NOW))
    hub.call_started(CALL)
    hub.open_approval(CALL, ApprovalRequest(1, _order()), lambda _: None)

    assert hub.order_status(CALL)["can_cancel"] is False
    assert _refusal(hub).code == "in_call"


# ---------------------------------------------------------------- 한 주문에 결정 한 번


def test_the_owner_cannot_decide_after_the_customer_cancelled() -> None:
    hub, _, sink = _placed()
    hub.cancel_by_customer(CALL)

    with pytest.raises(AlreadyDecided):
        hub.accept(CALL)
    with pytest.raises(AlreadyDecided):
        hub.reject(CALL, "busy")

    assert hub.order_status(CALL)["outcome"] == "customer_cancelled"
    assert [r.owner_status for r in sink.records] == ["customer_cancelled"]


def test_a_cancelled_order_stops_its_auto_cancel_timer() -> None:
    hub, clock, sink = _placed()
    hub.cancel_by_customer(CALL)
    clock.set(NOW + timedelta(minutes=6))

    assert hub.expire_due() == []
    assert hub.order_status(CALL)["outcome"] == "customer_cancelled"
    assert [r.owner_status for r in sink.records] == ["customer_cancelled"]


def test_the_owner_screen_sees_the_customer_cancel_at_once() -> None:
    hub, _, _ = _placed()

    async def scenario() -> list[dict]:
        events = hub.events()
        await anext(events)  # snapshot
        hub.cancel_by_customer(CALL)
        update = await asyncio.wait_for(anext(events), 1)
        await events.aclose()
        assert update.name == "order.update"
        return [update.data]

    [card] = asyncio.run(scenario())
    assert card["id"] == CALL
    assert card["status"] == "customer_cancelled"


def test_the_order_stream_shows_the_customer_cancel() -> None:
    hub, _, _ = _placed()

    async def scenario() -> dict:
        stream = hub.watch_order(CALL)
        await asyncio.wait_for(anext(stream), 1)
        hub.cancel_by_customer(CALL)
        event = await asyncio.wait_for(anext(stream), 1)
        await stream.aclose()
        return event.data

    cancelled = asyncio.run(scenario())
    assert (cancelled["stage"], cancelled["outcome"]) == ("cancelled", "customer_cancelled")


def test_a_decision_the_call_has_not_spoken_yet_cannot_be_cancelled_on_screen() -> None:
    """통화 중 결정은 통화가 말해야 굳는다. 그동안 손님 현황은 확인 중이지만 화면 취소는 없다."""
    hub = OwnerHub(store=FakeStoreKnowledge.from_fixture(), clock=FakeClock(NOW))
    told: list[OwnerDecision] = []
    hub.call_started(CALL)
    hub.open_approval(CALL, ApprovalRequest(1, _order()), told.append)
    hub.accept(CALL)

    status = hub.order_status(CALL)
    assert (status["stage"], status["can_cancel"]) == ("checking", False)
    assert _refusal(hub).code == "in_call"
