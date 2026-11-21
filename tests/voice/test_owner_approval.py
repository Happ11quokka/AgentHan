"""사장님 허브의 주문 확인 카드 (주문 승인 티켓 02): HTTP 없이 공개 인터페이스만 본다 (이음매 3).

통화 세션이 확인 요청을 열면(`open_approval`) 확인 대기(new) 주문 카드가 바로 생긴다. 사장님이
수락하면(`accept`) accepted + 확정 문자 + 통화 세션에 결정. 결정은 한 번만. 확인 요청 없이 온 확정
주문(사장님 화면 없음·승인 꺼짐)은 자동 확정 카드다. 사장님 화면 연결 수가 `approval_ready`다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from agenthan.core.contracts import Order, OrderLine
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.call.fakes import FakeClock
from agenthan.voice.dialog.approval import ApprovalRequest, OwnerDecision
from agenthan.voice.owner.hub import AlreadyDecided, CardBusy, OwnerEvent, OwnerHub, UnknownCard

NOW = datetime.fromisoformat("2026-11-08T16:02:00+09:00")
WAIT = 2.0
MEDOVIK = OrderLine("m01", 2, name="메도빅", unit_price=7_500)
ORDER_TEXT = "주문이 확정됐어요. 메도빅 2개, 합계 15,000원. 18:30에 준비해 둘게요."


def _hub(clock: FakeClock | None = None) -> OwnerHub:
    return OwnerHub(store=FakeStoreKnowledge.from_fixture(), clock=clock or FakeClock(NOW),
                    approval_wait_s=20.0, approval_expire_s=300.0)


def _order(call_id: str = "call-1") -> Order:
    return Order(id=call_id, lines=[MEDOVIK], pickup_time="2026-11-08T18:30:00+09:00",
                 status="confirmed", created_at="2026-11-08T16:02:00+09:00")


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


def test_a_request_shows_a_waiting_card_with_an_in_call_countdown() -> None:
    async def scenario():
        hub = _hub()
        screen = Screen(hub)
        await screen.take()  # snapshot
        hub.open_approval("call-1", ApprovalRequest(1, _order()), lambda d: None)
        (event,) = await screen.take()
        await screen.close()
        return event

    event = asyncio.run(scenario())
    card = event.data
    assert event.name == "order.update"
    assert (card["id"], card["status"], card["request"], card["stage"]) == (
        "call-1", "new", 1, "in_call")
    assert card["requested_at"] == "2026-11-08T16:02:00+09:00"
    assert card["deadline_at"] == "2026-11-08T16:02:20+09:00"  # 통화 중 20초
    assert (card["decided_at"], card["auto_accepted"], card["withdrawn"]) == ("", False, "")
    assert [(line["name_ko"], line["quantity"]) for line in card["lines"]] == [("메도빅", 2)]
    assert (card["pickup_label"], card["total_label"]) == ("18:30", "15,000₩")
    assert card["sms"] == []  # 확정 문자는 수락 때


def test_accepting_texts_the_customer_and_tells_the_call_once() -> None:
    decisions: list[OwnerDecision] = []

    async def scenario():
        hub = _hub()
        screen = Screen(hub)
        await screen.take()
        hub.open_approval("call-1", ApprovalRequest(1, _order()), decisions.append)
        await screen.take()
        returned = hub.accept("call-1")
        (update,) = await screen.take()
        with pytest.raises(AlreadyDecided):
            hub.accept("call-1")  # 두 번째 결정 (두 화면에서 같이 눌러도)
        hub.approval_said("call-1", 1)  # 통화가 수락을 손님에게 말했다 → 확정 문자
        (texted,) = await screen.take()
        hub.submit(_order())  # 통화 끝 주문서: 확인 카드를 그대로 둔다
        hub.call_ended("call-1", "confirmed")
        await screen.nothing_more()
        await screen.close()
        return hub, returned, update, texted

    hub, returned, update, texted = asyncio.run(scenario())
    assert returned == update.data
    assert (update.data["status"], update.data["decided_at"]) == (
        "accepted", "2026-11-08T16:02:00+09:00")
    assert update.data["sms"] == []  # 말하기 전에는 문자 없음 (리뷰 경합)
    assert [t["text"] for t in texted.data["sms"]] == [ORDER_TEXT]
    assert decisions == [OwnerDecision(1, "accept")]
    assert [t["kind"] for t in hub.customer_texts("call-1")] == ["order"]  # 문자는 한 번


def test_a_confirmed_order_without_a_request_is_auto_accepted_with_the_text() -> None:
    async def scenario():
        hub = _hub()
        screen = Screen(hub)
        await screen.take()
        hub.submit(_order())
        (event,) = await screen.take()
        await screen.close()
        return event

    card = asyncio.run(scenario()).data
    assert (card["status"], card["auto_accepted"], card["request"], card["stage"]) == (
        "accepted", True, 0, "")
    assert card["decided_at"] == "2026-11-08T16:02:00+09:00"
    assert [t["text"] for t in card["sms"]] == [ORDER_TEXT]


def test_an_auto_accepted_card_takes_no_decision() -> None:
    hub = _hub()
    hub.submit(_order())
    with pytest.raises(AlreadyDecided):
        hub.accept("call-1")


def test_approval_is_ready_only_while_an_owner_screen_is_connected() -> None:
    async def scenario():
        hub = _hub()
        before = hub.approval_ready()
        hub.submit(_order())
        texts = hub.watch_texts("call-1")  # 손님 문자 스트림은 사장님 화면이 아니다
        await anext(texts)
        with_texts = hub.approval_ready()
        screen = Screen(hub)
        await screen.take()
        during = hub.approval_ready()
        await screen.close()
        await texts.aclose()
        return before, with_texts, during, hub.approval_ready()

    assert asyncio.run(scenario()) == (False, False, True, False)


def test_a_withdrawn_request_takes_no_decision_until_a_new_request_replaces_it() -> None:
    decisions: list[OwnerDecision] = []

    async def scenario():
        hub = _hub()
        screen = Screen(hub)
        await screen.take()
        hub.open_approval("call-1", ApprovalRequest(1, _order()), decisions.append)
        hub.withdraw_approval("call-1", 1, "changed")
        _, withdrawn = await screen.take(2)
        with pytest.raises(CardBusy):
            hub.accept("call-1")
        hub.open_approval("call-1", ApprovalRequest(2, _order()), decisions.append)
        (again,) = await screen.take()
        hub.accept("call-1")
        await screen.close()
        return withdrawn.data, again.data

    withdrawn, again = asyncio.run(scenario())
    assert (withdrawn["status"], withdrawn["withdrawn"]) == ("new", "changed")
    assert (again["request"], again["withdrawn"]) == (2, "")
    assert decisions == [OwnerDecision(2, "accept")]


def test_a_call_that_ends_before_the_decision_leaves_an_after_call_card() -> None:
    """통화 중 결정이 없으면 카드는 통화 뒤 대기(마감 = 요청 연 때 + 5분). 그 뒤 수락도 받는다."""
    clock = FakeClock(NOW)
    decisions: list[OwnerDecision] = []

    async def scenario():
        hub = _hub(clock)
        hub.call_started("call-1")
        screen = Screen(hub)
        await screen.take()
        hub.open_approval("call-1", ApprovalRequest(1, _order()), decisions.append)
        await screen.take()
        clock.set(datetime.fromisoformat("2026-11-08T16:02:25+09:00"))
        hub.call_ended("call-1", "hangup")
        after, call = await screen.take(2)
        accepted = hub.accept("call-1")
        await screen.close()
        return after.data, call.data, accepted

    after, call, accepted = asyncio.run(scenario())
    assert (after["stage"], after["deadline_at"]) == ("after_call", "2026-11-08T16:07:00+09:00")
    assert call["status"] == "ordered"  # 확인을 기다리는 주문 카드가 통화를 대신한다
    assert accepted["status"] == "accepted"
    assert [t["kind"] for t in accepted["sms"]] == ["order"]
    assert decisions == []  # 끝난 통화에는 넘기지 않는다


def test_a_snapshot_carries_the_waiting_card_and_the_hub_clock() -> None:
    async def scenario():
        hub = _hub()
        hub.open_approval("call-1", ApprovalRequest(1, _order()), lambda d: None)
        screen = Screen(hub)
        (snapshot,) = await screen.take()
        await screen.close()
        return snapshot.data

    snapshot = asyncio.run(scenario())
    (card,) = snapshot["orders"]
    assert (card["status"], card["deadline_at"]) == ("new", "2026-11-08T16:02:20+09:00")
    assert snapshot["now"] == NOW.isoformat()  # 화면이 남은 시간을 이 시계로 잰다


def test_accept_refuses_unknown_cards_and_delays_other_than_15_or_30() -> None:
    hub = _hub()
    with pytest.raises(UnknownCard):
        hub.accept("nope")
    hub.open_approval("call-1", ApprovalRequest(1, _order()), lambda d: None)
    with pytest.raises(ValueError):
        hub.accept("call-1", delay_min=20)  # 늦춘 분은 [+15분] [+30분]뿐 (티켓 04)



# --- 통화 중 결정은 통화가 말해야 굳는다 (주문 승인 리뷰 경합) ------------------------------------


class Recorded:
    def __init__(self) -> None:
        self.statuses: list[str] = []

    def record(self, decision) -> None:
        self.statuses.append(decision.owner_status)


def test_an_in_call_decision_is_texted_and_recorded_only_once_the_call_says_it() -> None:
    sink = Recorded()
    hub = OwnerHub(store=FakeStoreKnowledge.from_fixture(), clock=FakeClock(NOW),
                   decision_sink=sink)
    hub.open_approval("call-1", ApprovalRequest(1, _order()), lambda d: None)
    hub.accept("call-1")
    assert (hub.customer_texts("call-1"), sink.statuses) == ([], [])
    hub.approval_said("call-1", 2)  # 다른 번호: 그대로
    assert hub.customer_texts("call-1") == []

    hub.approval_said("call-1", 1)
    hub.approval_said("call-1", 1)  # 두 번 알려도 문자는 한 번

    assert [t["kind"] for t in hub.customer_texts("call-1")] == ["order"]
    assert sink.statuses == ["accepted"]


def test_a_withdraw_before_the_decision_is_said_undoes_it() -> None:
    sink = Recorded()
    hub = OwnerHub(store=FakeStoreKnowledge.from_fixture(), clock=FakeClock(NOW),
                   decision_sink=sink)
    hub.open_approval("call-1", ApprovalRequest(1, _order()), lambda d: None)
    hub.reject("call-1", "busy")
    hub.withdraw_approval("call-1", 1, "changed")  # 말하기 전에 손님이 고쳤다
    hub.approval_said("call-1", 1)  # 늦게 온 알림: 거둔 결정은 굳지 않는다
    hub.call_ended("call-1", "hangup")

    (card,) = asyncio.run(_snapshot(hub))["orders"]
    assert (card["status"], card["withdrawn"], card["reject_reason"], card["decided_at"]) == (
        "new", "cancelled", "", "")
    assert hub.customer_texts("call-1") == []
    assert sink.statuses == []


def test_a_decision_the_call_never_said_is_texted_when_the_call_ends() -> None:
    hub = _hub()
    hub.call_started("call-1")
    hub.open_approval("call-1", ApprovalRequest(1, _order()), lambda d: None)
    hub.accept("call-1")
    hub.submit(_order())  # 세션이 시간 초과 문자 약속으로 끝냈다
    hub.call_ended("call-1", "confirmed")

    assert [t["kind"] for t in hub.customer_texts("call-1")] == ["order"]


def test_a_late_reject_on_a_decided_card_is_409_even_with_a_bad_payload() -> None:
    hub = _hub()
    hub.open_approval("call-1", ApprovalRequest(1, _order()), lambda d: None)
    hub.accept("call-1")
    with pytest.raises(AlreadyDecided):
        hub.reject("call-1", "free", text=" ")  # 다른 화면의 낡은 시트: 글 검사보다 409가 먼저
    with pytest.raises(AlreadyDecided):
        hub.reject("call-1", "out_of_stock", menu_ids=["m99"])


async def _snapshot(hub: OwnerHub) -> dict:
    stream = hub.events()
    snapshot = await asyncio.wait_for(anext(stream), WAIT)
    await stream.aclose()
    return snapshot.data
