"""사장님 허브의 통화 카드(4단계 티켓 02): HTTP 없이 공개 인터페이스만 본다 (spec 이음매 3).

통화 세션이 `OwnerChannel`로 알리는 것(시작·장바구니·손님 말·끝)과 `OrderSink.submit`이 카드를
어떻게 바꾸는지, 사장님 화면에 어떤 이벤트가 가는지 본다. 통화 id마다 카드 하나:
통화 중(live) → 확정이면 새 주문 카드(통화 카드는 ordered로 빠짐) · 넘김이면 "손님 연락 필요"
(handoff) · 확정 없이 끝나면 회색 미완료(missed). 넘김·미완료는 사장님이 닫는다(closed).
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from agenthan.core.contracts import Order, OrderLine
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.call.fakes import FakeClock
from agenthan.voice.owner.hub import CardBusy, OwnerEvent, OwnerHub, UnknownCard

NOW = datetime.fromisoformat("2026-11-08T16:02:00+09:00")
WAIT = 2.0
MEDOVIK = OrderLine("m01", 1, name="메도빅", unit_price=7_500)
TWO_MEDOVIK = OrderLine("m01", 2, name="메도빅", unit_price=7_500)


def _hub() -> OwnerHub:
    return OwnerHub(store=FakeStoreKnowledge.from_fixture(), clock=FakeClock(NOW))


def _order(status: str, call_id: str = "call-1", lines=(TWO_MEDOVIK,)) -> Order:
    return Order(id=call_id, lines=list(lines), pickup_time="2026-11-08T18:30:00+09:00",
                 status=status, created_at="2026-11-08T16:05:00+09:00")


class Screen:
    """사장님 화면 하나: 구독해서 받은 이벤트를 모은다. 통화 자막(`call.caption`, 티켓 10)은
    `test_owner_captions.py` 몫이라 여기서는 건너뛴다."""

    def __init__(self, hub: OwnerHub) -> None:
        self._stream = hub.events()
        self.got: list[OwnerEvent] = []

    async def _next(self, timeout: float) -> OwnerEvent:
        async def card() -> OwnerEvent:
            while (event := await anext(self._stream)).name == "call.caption":
                pass
            return event
        return await asyncio.wait_for(card(), timeout)

    async def take(self, count: int = 1) -> list[OwnerEvent]:
        new = [await self._next(WAIT) for _ in range(count)]
        self.got += new
        return new

    async def nothing_more(self) -> None:
        with pytest.raises(TimeoutError):
            await self._next(0.05)

    async def close(self) -> None:
        await self._stream.aclose()


def _names(events: list[OwnerEvent]) -> list[tuple[str, str]]:
    return [(e.name, e.data.get("status", "")) for e in events]


def test_a_call_shows_a_live_card_whose_cart_follows_the_call() -> None:
    async def scenario():
        hub = _hub()
        screen = Screen(hub)
        await screen.take()  # snapshot
        hub.call_started("call-1")
        hub.cart_changed("call-1", [])
        hub.customer_said("call-1", "메도빅 하나요")
        hub.cart_changed("call-1", [MEDOVIK])
        hub.cart_changed("call-1", [MEDOVIK])  # 안 바뀐 장바구니는 다시 보내지 않는다
        hub.cart_changed("call-1", [TWO_MEDOVIK])
        events = await screen.take(3)
        await screen.nothing_more()
        await screen.close()
        return events

    started, one, two = asyncio.run(scenario())
    assert started.name == "call.update"
    assert started.data == {
        "id": "call-1", "status": "live", "started_at": "2026-11-08T16:02:00+09:00",
        "started_label": "16:02", "reason": "", "lines": [], "total": 0, "total_label": "0₩",
        "heard": [], "heard_owner": [],
    }
    assert [(line["name_ko"], line["quantity"]) for line in one.data["lines"]] == [("메도빅", 1)]
    assert (one.data["total_label"], two.data["total_label"]) == ("7,500₩", "15,000₩")
    assert two.data["status"] == "live"


def test_a_confirmed_call_turns_into_a_new_order_card_with_the_same_id() -> None:
    async def scenario():
        hub = _hub()
        hub.call_started("call-1")
        hub.cart_changed("call-1", [TWO_MEDOVIK])
        screen = Screen(hub)
        (snapshot,) = await screen.take()
        hub.submit(_order("confirmed"))
        hub.call_ended("call-1", "confirmed")
        events = await screen.take(2)
        await screen.nothing_more()
        await screen.close()
        later = Screen(hub)
        (again,) = await later.take()
        await later.close()
        return snapshot, events, again

    snapshot, events, again = asyncio.run(scenario())
    assert [c["status"] for c in snapshot.data["calls"]] == ["live"]
    assert snapshot.data["orders"] == []
    assert _names(events) == [("order.update", "new"), ("call.update", "ordered")]
    assert {e.data["id"] for e in events} == {"call-1"}
    assert again.data["calls"] == []  # 주문이 된 통화 카드는 snapshot에 없다
    assert [o["id"] for o in again.data["orders"]] == ["call-1"]


def test_a_handoff_call_becomes_a_contact_the_customer_card_with_the_last_three_lines() -> None:
    async def scenario():
        hub = _hub()
        screen = Screen(hub)
        await screen.take()
        hub.call_started("call-1")
        for text in ("메도빅 두 개요", "아 그리고", "포장 돼요?", "사장님 바꿔 주세요"):
            hub.customer_said("call-1", text)
        hub.submit(_order("handoff"))
        hub.call_ended("call-1", "handoff")
        events = await screen.take(2)
        await screen.nothing_more()
        await screen.close()
        return events

    started, handoff = asyncio.run(scenario())
    assert handoff.name == "call.update"
    card = handoff.data
    assert (card["id"], card["status"], card["reason"]) == ("call-1", "handoff", "handoff")
    assert card["heard"] == ["아 그리고", "포장 돼요?", "사장님 바꿔 주세요"]
    assert [(line["name_ko"], line["quantity"]) for line in card["lines"]] == [("메도빅", 2)]
    assert card["total_label"] == "15,000₩"


def test_a_handoff_without_a_live_card_still_shows_up() -> None:
    async def scenario():
        hub = _hub()
        hub.submit(_order("handoff", "call-7"))
        screen = Screen(hub)
        (snapshot,) = await screen.take()
        await screen.close()
        return snapshot

    (card,) = asyncio.run(scenario()).data["calls"]
    assert (card["id"], card["status"]) == ("call-7", "handoff")


@pytest.mark.parametrize("reason", ["hangup", "cancelled", "no_input", "error"])
def test_a_call_that_ends_without_an_order_leaves_a_missed_card_with_the_reason(reason) -> None:
    async def scenario():
        hub = _hub()
        hub.call_started("call-1")
        hub.cart_changed("call-1", [MEDOVIK])
        screen = Screen(hub)
        await screen.take()
        hub.call_ended("call-1", reason)
        (missed,) = await screen.take()
        await screen.close()
        return missed

    card = asyncio.run(scenario()).data
    assert (card["status"], card["reason"]) == ("missed", reason)
    assert [line["name_ko"] for line in card["lines"]] == ["메도빅"]
    assert card["heard"] == []


def test_the_owner_closes_handoff_and_missed_cards() -> None:
    async def scenario():
        hub = _hub()
        hub.call_started("call-1")
        hub.call_ended("call-1", "hangup")
        hub.submit(_order("handoff", "call-2"))
        screen = Screen(hub)
        await screen.take()
        closed = [hub.dismiss("call-1"), hub.dismiss("call-2")]
        again = hub.dismiss("call-1")  # 두 화면에서 같이 눌러도 된다
        events = await screen.take(2)
        await screen.nothing_more()
        await screen.close()
        later = Screen(hub)
        (snapshot,) = await later.take()
        await later.close()
        return closed, again, events, snapshot

    closed, again, events, snapshot = asyncio.run(scenario())
    assert [c["status"] for c in closed] == ["closed", "closed"]
    assert again["status"] == "closed"
    assert _names(events) == [("call.update", "closed"), ("call.update", "closed")]
    assert [e.data for e in events] == closed
    assert snapshot.data["calls"] == []


def test_a_live_call_or_an_ordered_call_cannot_be_closed() -> None:
    hub = _hub()
    hub.call_started("call-1")
    with pytest.raises(CardBusy):
        hub.dismiss("call-1")
    hub.submit(_order("confirmed"))
    with pytest.raises(CardBusy):
        hub.dismiss("call-1")
    with pytest.raises(UnknownCard):
        hub.dismiss("no-such-call")


def test_calls_at_the_same_time_get_their_own_cards() -> None:
    async def scenario():
        hub = _hub()
        hub.call_started("call-1")
        hub.call_started("call-2")
        hub.cart_changed("call-2", [MEDOVIK])
        hub.call_ended("call-1", "hangup")
        screen = Screen(hub)
        (snapshot,) = await screen.take()
        await screen.close()
        return snapshot

    calls = asyncio.run(scenario()).data["calls"]
    assert [(c["id"], c["status"], len(c["lines"])) for c in calls] == [
        ("call-1", "missed", 0), ("call-2", "live", 1),
    ]


def test_notes_after_a_call_ended_do_not_change_its_card() -> None:
    async def scenario():
        hub = _hub()
        hub.call_started("call-1")
        hub.call_ended("call-1", "hangup")
        screen = Screen(hub)
        await screen.take()
        hub.cart_changed("call-1", [MEDOVIK])
        hub.call_ended("call-1", "error")
        hub.cart_changed("unknown", [MEDOVIK])
        hub.call_ended("unknown", "error")
        await screen.nothing_more()
        await screen.close()

    asyncio.run(scenario())
