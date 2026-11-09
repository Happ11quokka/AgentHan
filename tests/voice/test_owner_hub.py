"""사장님 허브: HTTP 없이 공개 인터페이스(`submit`·`accept`·`events`)만 본다 (spec 이음매 3).

허브가 내보내는 이벤트(이름·카드 내용)와 접수 결과를 본다. 허브 안 자료 구조는 보지 않는다.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime

import pytest

from agenthan.core.contracts import Order, OrderLine
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.call.fakes import FakeClock
from agenthan.voice.owner.hub import OwnerHub, UnknownCard

NOW = datetime.fromisoformat("2026-11-08T16:02:00+09:00")
WAIT = 2.0


def _hub() -> OwnerHub:
    return OwnerHub(store=FakeStoreKnowledge.from_fixture(), clock=FakeClock(NOW))


def _order(call_id: str = "call-1", **kw) -> Order:
    base = dict(
        id=call_id,
        lines=[
            OrderLine("m01", 2, request="초는 빼 주세요"),
            OrderLine("m03", 1, options=("디카페인 변경",)),
        ],
        pickup_time="2026-11-08T18:30:00+09:00",
        status="confirmed",
    )
    return Order(**(base | kw))


async def _next(stream):
    return await asyncio.wait_for(anext(stream), WAIT)


def test_a_confirmed_order_becomes_a_new_order_card_for_the_owner() -> None:
    async def scenario():
        hub = _hub()
        stream = hub.events()
        first = await _next(stream)
        hub.submit(_order())
        second = await _next(stream)
        await stream.aclose()
        return first, second

    first, second = asyncio.run(scenario())

    assert first.name == "snapshot"
    assert first.data == {"orders": []}
    assert second.name == "order.update"
    card = second.data
    assert card["id"] == "call-1"
    assert card["status"] == "new"
    assert card["received_at"] == "2026-11-08T16:02:00+09:00"
    assert card["pickup_time"] == "2026-11-08T18:30:00+09:00"
    assert card["pickup_label"] == "18:30"
    assert card["lines"] == [
        {"menu_id": "m01", "name_ko": "메도빅", "quantity": 2, "options": [],
         "request": "초는 빼 주세요", "subtotal": 15_000},
        {"menu_id": "m03", "name_ko": "아메리카노", "quantity": 1, "options": ["디카페인 변경"],
         "request": "", "subtotal": 5_000},
    ]
    assert card["total"] == 20_000
    assert card["total_label"] == "20,000₩"
    assert second.id > first.id


def test_the_owner_accepts_a_new_order_and_every_screen_sees_it() -> None:
    async def scenario():
        hub = _hub()
        stream = hub.events()
        await _next(stream)
        hub.submit(_order())
        await _next(stream)
        returned = hub.accept("call-1")
        update = await _next(stream)
        await stream.aclose()
        return returned, update

    returned, update = asyncio.run(scenario())

    assert update.name == "order.update"
    assert update.data["id"] == "call-1"
    assert update.data["status"] == "accepted"
    assert returned == update.data


def test_a_screen_that_connects_later_starts_from_a_snapshot_of_every_card() -> None:
    async def scenario():
        hub = _hub()
        hub.submit(_order("call-1"))
        hub.submit(_order("call-2"))
        hub.accept("call-1")
        stream = hub.events()
        first = await _next(stream)
        await stream.aclose()
        return first

    first = asyncio.run(scenario())

    assert first.name == "snapshot"
    assert [(c["id"], c["status"]) for c in first.data["orders"]] == [
        ("call-1", "accepted"), ("call-2", "new"),
    ]


def test_the_card_shows_what_the_customer_heard_not_todays_menu() -> None:
    """계약 v1 제안 6·7: 주문에 담긴 이름·한 개 가격·만든 시각을 쓴다. 통화 뒤 메뉴가 바뀌어도
    (여기서는 메도빅이 7,500원 → 주문 때 7,000원) 카드는 손님이 들은 값이다."""
    order = _order(
        lines=[
            OrderLine("m01", 2, name="메도빅 조각", unit_price=7_000),
            OrderLine("m03", 1, options=("디카페인 변경",), name="아메리카노", unit_price=5_000),
        ],
        created_at="2026-11-08T15:58:30+09:00",
    )

    async def scenario():
        hub = _hub()
        hub.submit(order)
        stream = hub.events()
        first = await _next(stream)
        await stream.aclose()
        return first

    (card,) = asyncio.run(scenario()).data["orders"]

    assert [(x["name_ko"], x["subtotal"]) for x in card["lines"]] == [
        ("메도빅 조각", 14_000), ("아메리카노", 5_000),
    ]
    assert card["total"] == 19_000
    assert card["received_at"] == "2026-11-08T15:58:30+09:00"


def test_accepting_an_unknown_order_fails() -> None:
    with pytest.raises(UnknownCard):
        _hub().accept("no-such-call")


def test_an_order_submitted_from_another_thread_reaches_the_screen() -> None:
    async def scenario():
        hub = _hub()
        stream = hub.events()
        await _next(stream)
        worker = threading.Thread(target=hub.submit, args=(_order("call-9"),))
        worker.start()
        worker.join()
        update = await _next(stream)
        await stream.aclose()
        return update

    update = asyncio.run(scenario())

    assert (update.name, update.data["id"]) == ("order.update", "call-9")
