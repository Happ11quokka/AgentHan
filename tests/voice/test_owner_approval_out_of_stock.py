"""사장님 허브의 재료 소진 거절과 오늘 품절 (주문 승인 티켓 06, 이음매 3): 공개 인터페이스·가짜
시계.

[거절] → [재료 소진] → 떨어진 메뉴(여러 개) → 카드 rejected + 메뉴, 그 메뉴는 오늘 품절(날짜가
바뀌면 비움, 되돌리기로 뺌, `owner.sold_out` 이벤트와 snapshot). 통화 중이면 손님이 빼거나 바꿀
수 있어 취소 문자를 미룬다: 떨어진 메뉴만 뺀 확정이 오면 남은 메뉴로 수락 + 확정 문자, 새 번호로
다시 요청하면 새 카드, 둘 다 없이 끝나면 메뉴 이름이 든 취소 문자. 통화 뒤 거절은 바로 취소 문자.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from test_owner_decisions import Recorded

from agenthan.core.contracts import Order, OrderLine
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.call.fakes import FakeClock
from agenthan.voice.dialog.approval import ApprovalRequest, OwnerDecision
from agenthan.voice.owner.decisions import decision_of
from agenthan.voice.owner.hub import AlreadyDecided, OwnerEvent, OwnerHub, UnknownCard

NOW = datetime.fromisoformat("2026-11-08T16:02:00+09:00")
LATER = datetime.fromisoformat("2026-11-08T16:02:07+09:00")
TOMORROW = datetime.fromisoformat("2026-11-09T00:00:01+09:00")
MEDOVIK = OrderLine("m01", 1, name="메도빅", unit_price=7_500)
NAPOLEON = OrderLine("m02", 1, name="나폴레옹", unit_price=7_000)
BLIN = OrderLine("m04", 1, name="블린", unit_price=9_000)
OUT_TEXT = ("죄송해요, 메도빅은 지금 재료가 떨어져 주문이 취소됐어요. "
            "다른 메뉴로 다시 주문해 주세요.")
WAIT = 2.0


def _hub(clock: FakeClock | None = None, *, sink: Recorded | None = None) -> OwnerHub:
    return OwnerHub(store=FakeStoreKnowledge.from_fixture(), clock=clock or FakeClock(NOW),
                    approval_wait_s=20.0, approval_expire_s=300.0, decision_sink=sink)


def _order(*lines: OrderLine) -> Order:
    return Order(id="call-1", lines=list(lines or (MEDOVIK, NAPOLEON)),
                 pickup_time="2026-11-08T18:30:00+09:00", status="confirmed",
                 created_at="2026-11-08T16:02:00+09:00")


def _requested(hub: OwnerHub, decisions: list[OwnerDecision] | None = None,
               number: int = 1, order: Order | None = None) -> None:
    if number == 1:
        hub.call_started("call-1")
    hub.open_approval("call-1", ApprovalRequest(number, order or _order()),
                      (decisions if decisions is not None else []).append)


def _menus(card: dict) -> list[tuple[str, int]]:
    return [(line["menu_id"], line["quantity"]) for line in card["lines"]]


def _texts(hub: OwnerHub) -> list[tuple[str, str]]:
    return [(t["kind"], t["text"]) for t in hub.customer_texts("call-1")]


async def _events(hub: OwnerHub, act, count: int) -> list[OwnerEvent]:
    """사장님 화면을 열고(snapshot은 버림) `act` 뒤 받은 이벤트 `count`개."""
    stream = hub.events()
    await asyncio.wait_for(anext(stream), WAIT)
    act()
    got = [await asyncio.wait_for(anext(stream), WAIT) for _ in range(count)]
    await stream.aclose()
    return got


async def _snapshot(hub: OwnerHub) -> dict:
    stream = hub.events()
    event = await asyncio.wait_for(anext(stream), WAIT)
    await stream.aclose()
    assert event.name == "snapshot"
    return event.data


def test_out_of_stock_in_call_marks_the_menus_sold_out_and_tells_the_call() -> None:
    decisions: list[OwnerDecision] = []
    hub = _hub()
    _requested(hub, decisions)

    card = hub.reject("call-1", "out_of_stock", menu_ids=["m01"])

    assert (card["status"], card["reject_reason"], card["reject_menu_ids"]) == (
        "rejected", "out_of_stock", ["m01"])
    assert card["sold_out"] == [{"menu_id": "m01", "name_ko": "메도빅", "name_owner": "Медовик"}]
    assert decisions == [OwnerDecision(1, "reject", reason="out_of_stock", menu_ids=("m01",))]
    assert hub.sold_out_today() == {"m01"}
    assert _texts(hub) == []  # 손님이 빼거나 바꿀 수 있다. 취소 문자는 아직


def test_the_sold_out_change_goes_to_the_screen_and_the_snapshot() -> None:
    hub = _hub()
    _requested(hub)

    async def scenario() -> tuple[list[OwnerEvent], dict]:
        got = await _events(
            hub, lambda: hub.reject("call-1", "out_of_stock", menu_ids=["m01", "m02"]), 2)
        return got, await _snapshot(hub)

    got, snapshot = asyncio.run(scenario())
    sold_out = {"date": "2026-11-08", "items": [
        {"menu_id": "m01", "name_ko": "메도빅", "name_owner": "Медовик"},
        {"menu_id": "m02", "name_ko": "나폴레옹", "name_owner": "Наполеон"}]}
    assert [(e.name, e.data) for e in got if e.name == "owner.sold_out"] == [
        ("owner.sold_out", sold_out)]
    assert snapshot["sold_out"] == sold_out
    (card,) = snapshot["orders"]
    assert [item["menu_id"] for item in card["sold_out"]] == ["m01", "m02"]


def test_dropping_the_sold_out_menu_turns_the_card_into_an_accept_with_the_rest() -> None:
    sink, clock = Recorded(), FakeClock(NOW)
    hub = _hub(clock, sink=sink)
    _requested(hub)
    hub.reject("call-1", "out_of_stock", menu_ids=["m01"])
    hub.approval_said("call-1", 1)  # 통화가 품절 안내를 말했다
    clock.set(LATER)

    hub.submit(_order(NAPOLEON))  # 손님이 메도빅만 뺐다 → 세션이 남은 주문서를 넘긴다
    hub.call_ended("call-1", "confirmed")

    (card,) = asyncio.run(_snapshot(hub))["orders"]
    assert (card["status"], card["reject_reason"], card["decided_at"]) == (
        "accepted", "", LATER.isoformat())
    assert _menus(card) == [("m02", 1)]
    assert [item["menu_id"] for item in card["sold_out"]] == ["m01"]  # 품절 표시는 남는다
    assert _texts(hub) == [("order", "주문이 확정됐어요. 나폴레옹 1개, 합계 7,000원. "
                                     "18:30에 준비해 둘게요.")]
    assert [(r.owner_status, r.reject_reason) for r in sink.decisions] == [
        ("rejected", "out_of_stock"), ("accepted", "")]


def test_swapping_opens_a_new_card_that_keeps_the_sold_out_mark() -> None:
    hub = _hub()
    _requested(hub)
    hub.reject("call-1", "out_of_stock", menu_ids=["m01"])
    hub.approval_said("call-1", 1)  # 통화가 품절 안내를 말했다

    _requested(hub, number=2, order=_order(NAPOLEON, BLIN))  # 바꿔서 다시 복창 → "네"
    hub.call_ended("call-1", "hangup")

    (card,) = asyncio.run(_snapshot(hub))["orders"]
    assert (card["status"], card["request"], card["stage"]) == ("new", 2, "after_call")
    assert _menus(card) == [("m02", 1), ("m04", 1)]
    assert [item["menu_id"] for item in card["sold_out"]] == ["m01"]
    assert _texts(hub) == []  # 새 요청이 대신한다. 취소 문자는 없다


def test_hanging_up_after_the_notice_sends_the_cancel_text_with_the_menu_names() -> None:
    hub = _hub()
    _requested(hub)
    hub.reject("call-1", "out_of_stock", menu_ids=["m01"])
    hub.approval_said("call-1", 1)  # 통화가 품절 안내를 말했다

    hub.call_ended("call-1", "hangup")

    assert _texts(hub) == [("rejected", OUT_TEXT)]


def test_out_of_stock_after_the_call_texts_the_customer_at_once() -> None:
    hub = _hub()
    _requested(hub)
    hub.call_ended("call-1", "hangup")  # 통화 뒤 대기

    card = hub.reject("call-1", "out_of_stock", menu_ids=["m01"])

    assert card["status"] == "rejected"
    assert _texts(hub) == [("rejected", OUT_TEXT)]
    assert hub.sold_out_today() == {"m01"}
    hub.submit(_order(NAPOLEON))  # 통화 뒤라 남은 메뉴 확정은 오지 않는다. 와도 수락이 아니다
    assert asyncio.run(_snapshot(hub))["orders"][0]["status"] == "rejected"


def test_undo_takes_the_menu_off_today_sold_out() -> None:
    hub = _hub()
    _requested(hub)
    hub.reject("call-1", "out_of_stock", menu_ids=["m01", "m02"])

    got = asyncio.run(_events(hub, lambda: hub.undo_sold_out("m01"), 1))

    assert hub.sold_out_today() == {"m02"}
    assert [(e.name, [i["menu_id"] for i in e.data["items"]]) for e in got] == [
        ("owner.sold_out", ["m02"])]
    assert hub.undo_sold_out("m01")["items"][0]["menu_id"] == "m02"  # 두 번 눌러도 된다
    with pytest.raises(UnknownCard):
        hub.undo_sold_out("m99")


def test_sold_out_clears_when_the_store_date_changes() -> None:
    clock = FakeClock(NOW)
    hub = _hub(clock)
    _requested(hub)
    hub.reject("call-1", "out_of_stock", menu_ids=["m01"])

    def midnight() -> None:  # 열린 화면이 자정을 넘긴다 (허브 자정 타이머가 부르는 것과 같다)
        clock.set(TOMORROW)
        assert hub.sold_out_today() == frozenset()  # 읽기만 해도 어제 것은 안 보인다
        assert hub.roll_day() is True

    got = asyncio.run(_events(hub, midnight, 1))

    assert [(e.name, e.data) for e in got] == [
        ("owner.sold_out", {"date": "2026-11-09", "items": []})]
    assert asyncio.run(_snapshot(hub))["sold_out"] == {"date": "2026-11-09", "items": []}
    assert hub.roll_day() is False  # 한 번만


def test_a_new_screen_after_midnight_sees_no_sold_out() -> None:
    clock = FakeClock(NOW)
    hub = _hub(clock)
    _requested(hub)
    hub.reject("call-1", "out_of_stock", menu_ids=["m01"])
    clock.set(TOMORROW)

    assert asyncio.run(_snapshot(hub))["sold_out"]["items"] == []


def test_a_swapped_order_confirmed_without_a_screen_becomes_an_automatic_accept() -> None:
    hub = _hub()
    _requested(hub)
    hub.reject("call-1", "out_of_stock", menu_ids=["m01"])
    hub.approval_said("call-1", 1)  # 통화가 품절 안내를 말했다

    hub.submit(_order(NAPOLEON, BLIN))  # 바꾼 주문이 확인 요청 없이 확정됐다(그새 화면이 꺼짐)
    hub.call_ended("call-1", "confirmed")

    (card,) = asyncio.run(_snapshot(hub))["orders"]
    assert (card["status"], card["auto_accepted"]) == ("accepted", True)
    assert _menus(card) == [("m02", 1), ("m04", 1)]
    assert [item["menu_id"] for item in card["sold_out"]] == ["m01"]
    assert [kind for kind, _ in _texts(hub)] == ["order"]  # 취소 문자는 없다


# --- 늦춘 수락(티켓 04)과 함께 ---------------------------------------------------------------


def test_out_of_stock_is_refused_once_the_card_is_a_delayed_accept() -> None:
    """늦춘 수락 뒤(손님이 바뀐 시각을 듣는 중·통화 뒤)에는 재료 소진 거절도 409, 품절도 안 건다."""
    asking = _hub()
    _requested(asking)
    asking.accept("call-1", delay_min=15)  # 통화 중: delay_reply asking
    asking.approval_said("call-1", 1)
    with pytest.raises(AlreadyDecided):
        asking.reject("call-1", "out_of_stock", menu_ids=["m01"])

    after = _hub()
    _requested(after)
    after.call_ended("call-1", "hangup")
    after.accept("call-1", delay_min=30)  # 통화 뒤 늦춘 수락
    with pytest.raises(AlreadyDecided):
        after.reject("call-1", "out_of_stock", menu_ids=["m01"])

    for hub in (asking, after):
        assert hub.sold_out_today() == frozenset()
        (card,) = asyncio.run(_snapshot(hub))["orders"]
        assert (card["status"], card["reject_reason"], card["sold_out"]) == ("accepted", "", [])


def test_the_rest_accept_is_recorded_with_the_reduced_order() -> None:
    """떨어진 메뉴만 뺀 확정: 결정 기록(`decision_of`)은 accepted + 남은 메뉴의 주문서."""
    sink = Recorded()
    hub = _hub(sink=sink)
    _requested(hub)
    hub.reject("call-1", "out_of_stock", menu_ids=["m01"])
    hub.approval_said("call-1", 1)  # 통화가 품절 안내를 말했다

    hub.submit(_order(NAPOLEON))
    hub.call_ended("call-1", "confirmed")

    (card,) = asyncio.run(_snapshot(hub))["orders"]
    record = decision_of(card, _order(NAPOLEON))
    assert record is not None
    assert (record.owner_status, record.reject_reason, record.owner_pickup_time) == (
        "accepted", "", "")
    last = sink.decisions[-1]
    assert (last.owner_status, last.reject_reason) == ("accepted", "")
    assert last.order is not None and last.order.lines == [NAPOLEON]


def test_today_sold_out_outlives_the_call_for_the_next_one() -> None:
    """통화가 남은 메뉴로 끝나도(또는 늦춘 수락이 이어져도) 오늘 품절은 다음 통화에 그대로다."""
    hub = _hub()
    _requested(hub)
    hub.reject("call-1", "out_of_stock", menu_ids=["m01"])
    hub.submit(_order(NAPOLEON))
    hub.call_ended("call-1", "confirmed")

    hub.call_started("call-2")
    hub.open_approval("call-2", ApprovalRequest(1, Order(
        id="call-2", lines=[NAPOLEON], pickup_time="2026-11-08T19:00:00+09:00",
        status="confirmed", created_at="2026-11-08T16:10:00+09:00")), [].append)
    hub.accept("call-2", delay_min=15)
    hub.call_ended("call-2", "hangup")

    assert hub.sold_out_today() == {"m01"}
    assert asyncio.run(_snapshot(hub))["sold_out"]["items"] == [
        {"menu_id": "m01", "name_ko": "메도빅", "name_owner": "Медовик"}]
