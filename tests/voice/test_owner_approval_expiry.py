"""사장님 허브의 통화 뒤 대기와 자동 취소 (주문 승인 티켓 03, 이음매 3): 공개 인터페이스·가짜 시계.

결정 없이 통화가 끝나면 확인 카드는 통화 뒤 대기(마감 = 요청을 연 때 + 5분)다. 그 안에 수락하면
확정 문자, 마감이 지나면 expired(결정 409) + 자동 취소 문자. 마감은 허브 시계로 잰다. 시험은 시계를
옮기고 `expire_due`를 부르거나(결정적), 허브가 스스로 건 타이머가 도는 것을 본다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from agenthan.core.contracts import Order, OrderLine
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.call.fakes import FakeClock
from agenthan.voice.dialog.approval import ApprovalRequest, OwnerDecision
from agenthan.voice.owner.hub import AlreadyDecided, OwnerEvent, OwnerHub

NOW = datetime.fromisoformat("2026-11-08T16:02:00+09:00")
WAIT = 2.0
MEDOVIK = OrderLine("m01", 2, name="메도빅", unit_price=7_500)
ORDER_TEXT = "주문이 확정됐어요. 메도빅 2개, 합계 15,000원. 18:30에 준비해 둘게요."
EXPIRED_TEXT = "가게에서 확인하지 못해 주문이 취소됐어요. 다시 전화 주세요."


def _hub(clock: FakeClock, expire_s: float = 300.0) -> OwnerHub:
    return OwnerHub(store=FakeStoreKnowledge.from_fixture(), clock=clock,
                    approval_wait_s=20.0, approval_expire_s=expire_s)


def _order(call_id: str = "call-1") -> Order:
    return Order(id=call_id, lines=[MEDOVIK], pickup_time="2026-11-08T18:30:00+09:00",
                 status="confirmed", created_at="2026-11-08T16:02:00+09:00")


def _at(seconds: float) -> datetime:
    return NOW + timedelta(seconds=seconds)


class Screen:
    """사장님 화면 하나: 구독해서 카드 이벤트를 받는다(자막은 건너뜀)."""

    def __init__(self, hub: OwnerHub) -> None:
        self._stream = hub.events()

    async def take(self, count: int = 1) -> list[OwnerEvent]:
        async def card() -> OwnerEvent:
            while (event := await anext(self._stream)).name == "call.caption":
                pass
            return event
        return [await asyncio.wait_for(card(), WAIT) for _ in range(count)]

    async def nothing_more(self) -> None:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(anext(self._stream), 0.05)

    async def close(self) -> None:
        await self._stream.aclose()


async def _left_after_call(hub: OwnerHub, clock: FakeClock, screen: Screen,
                           reply=lambda d: None) -> None:
    """통화 중 확인 요청 → 20초 지나 문자 약속 → 주문서(`submit`) → 통화 끝 → 통화 뒤 대기."""
    hub.call_started("call-1")
    hub.open_approval("call-1", ApprovalRequest(1, _order()), reply)
    clock.set(_at(21))
    hub.submit(_order())  # 문자 약속과 함께 넘어온 주문서: 확인 카드를 그대로 둔다
    hub.call_ended("call-1", "confirmed")
    await screen.take(4)  # 통화 카드 · 확인 카드 · 통화 카드 ordered · 통화 뒤 대기 카드


def test_an_after_call_card_expires_five_minutes_after_the_request_with_a_text() -> None:
    clock = FakeClock(NOW)
    decisions: list[OwnerDecision] = []

    async def scenario():
        hub = _hub(clock)
        screen = Screen(hub)
        await screen.take()  # snapshot
        await _left_after_call(hub, clock, screen, decisions.append)
        customer = hub.watch_texts("call-1")  # 손님 통화 화면 "받은 문자"
        assert (await anext(customer)).name == "sms.snapshot"
        clock.set(_at(299))
        early = hub.expire_due()  # 1초 전: 그대로 (화면에도 아무것도 안 간다, 다음 이벤트가 만료다)
        clock.set(_at(300))
        expired_ids = hub.expire_due()
        (update,) = await screen.take()
        sms = await asyncio.wait_for(anext(customer), WAIT)
        again = hub.expire_due()
        await customer.aclose()
        await screen.close()
        return hub, early, expired_ids, again, update, sms

    hub, early, expired_ids, again, update, sms = asyncio.run(scenario())
    assert (early, expired_ids, again) == ([], ["call-1"], [])
    card = update.data
    assert (update.name, card["status"], card["stage"]) == ("order.update", "expired", "after_call")
    assert card["decided_at"] == "2026-11-08T16:07:00+09:00"
    assert [(t["kind"], t["text"]) for t in card["sms"]] == [("expired", EXPIRED_TEXT)]
    assert (sms.name, sms.data["kind"], sms.data["text"]) == ("sms", "expired", EXPIRED_TEXT)
    with pytest.raises(AlreadyDecided):  # 자동 취소된 카드는 결정을 안 받는다 (HTTP 409)
        hub.accept("call-1")
    assert decisions == []  # 끝난 통화에는 아무것도 넘기지 않는다


def test_an_owner_who_accepts_after_the_call_but_in_time_texts_the_order() -> None:
    clock = FakeClock(NOW)

    async def scenario():
        hub = _hub(clock)
        screen = Screen(hub)
        await screen.take()
        await _left_after_call(hub, clock, screen)
        clock.set(_at(240))  # 통화가 끝나고 몇 분 뒤 (조리 중이라 20초를 놓쳤다)
        accepted = hub.accept("call-1")
        await screen.take()
        clock.set(_at(400))
        later = hub.expire_due()
        await screen.nothing_more()
        await screen.close()
        return hub, accepted, later

    hub, accepted, later = asyncio.run(scenario())
    assert (accepted["status"], accepted["stage"]) == ("accepted", "after_call")
    assert accepted["decided_at"] == "2026-11-08T16:06:00+09:00"
    assert later == []
    assert [(t["kind"], t["text"]) for t in hub.customer_texts("call-1")] == [("order", ORDER_TEXT)]


def test_a_decision_after_the_deadline_is_refused_even_before_the_timer_runs() -> None:
    clock = FakeClock(NOW)
    hub = _hub(clock)  # 루프 밖: 타이머가 없다. 결정 앞에서 마감을 잰다
    hub.open_approval("call-1", ApprovalRequest(1, _order()), lambda d: None)
    hub.call_ended("call-1", "hangup")
    clock.set(_at(301))
    with pytest.raises(AlreadyDecided):
        hub.accept("call-1")
    assert [t["kind"] for t in hub.customer_texts("call-1")] == ["expired"]


def test_the_hub_expires_cards_on_its_own_timer() -> None:
    clock = FakeClock(NOW)

    async def scenario():
        hub = _hub(clock, expire_s=0.2)  # 실제 초로 짧게: 허브가 건 루프 타이머가 돈다
        screen = Screen(hub)
        await screen.take()
        hub.open_approval("call-1", ApprovalRequest(1, _order()), lambda d: None)
        hub.call_ended("call-1", "hangup")
        await screen.take(2)
        clock.set(_at(1))
        (update,) = await screen.take()
        await screen.close()
        return update.data

    assert asyncio.run(scenario())["status"] == "expired"


def test_a_new_screen_never_sees_an_overdue_card_as_waiting() -> None:
    clock = FakeClock(NOW)

    async def scenario():
        hub = _hub(clock)
        hub.open_approval("call-1", ApprovalRequest(1, _order()), lambda d: None)
        hub.call_ended("call-1", "hangup")
        clock.set(_at(305))
        screen = Screen(hub)
        (snapshot,) = await screen.take()
        await screen.close()
        return snapshot.data

    (card,) = asyncio.run(scenario())["orders"]
    assert card["status"] == "expired"


def test_a_request_the_customer_cancelled_or_is_changing_does_not_expire() -> None:
    clock = FakeClock(NOW)
    hub = _hub(clock)
    hub.open_approval("call-1", ApprovalRequest(1, _order("call-1")), lambda d: None)
    hub.withdraw_approval("call-1", 1, "cancelled")
    hub.call_ended("call-1", "cancelled")
    hub.open_approval("call-2", ApprovalRequest(1, _order("call-2")), lambda d: None)
    hub.withdraw_approval("call-2", 1, "changed")
    hub.call_ended("call-2", "hangup")
    clock.set(_at(600))
    assert hub.expire_due() == []
    assert hub.customer_texts("call-1") == hub.customer_texts("call-2") == []


@pytest.mark.parametrize("reason", ["hangup", "error", "no_input", "confirmed"])
def test_every_way_a_call_ends_moves_an_open_request_to_the_after_call_wait(reason) -> None:
    clock = FakeClock(NOW)
    hub = _hub(clock)
    hub.call_started("call-1")
    hub.open_approval("call-1", ApprovalRequest(1, _order()), lambda d: None)
    hub.call_ended("call-1", reason)
    clock.set(_at(120))
    card = hub.accept("call-1")  # 통화 뒤에도 마감 전이면 결정을 받는다
    assert (card["status"], card["stage"], card["deadline_at"]) == (
        "accepted", "after_call", "2026-11-08T16:07:00+09:00")
