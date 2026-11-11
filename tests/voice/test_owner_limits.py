"""사장님 허브 상한 (6단계): 끝난 통화 카드와 주문 카드를 수·시간으로 정리한다.

장난 통화 수십 건이 사장님 화면과 서버 메모리를 채우지 않게. 오래된 것부터 지우고,
확정 주문 카드는 더 오래 둔다. 통화 중인 카드는 건드리지 않는다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from agenthan.core.contracts import Order, OrderLine
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.call.fakes import FakeClock
from agenthan.voice.owner.hub import HubLimits, OwnerEvent, OwnerHub
from agenthan.voice.settings import VoiceSettings

NOW = datetime.fromisoformat("2026-11-08T16:02:00+09:00")
MEDOVIK = OrderLine("m01", 1, name="메도빅", unit_price=7_500)


def _hub(clock: FakeClock | None = None, **limits: float) -> OwnerHub:
    return OwnerHub(store=FakeStoreKnowledge.from_fixture(), clock=clock or FakeClock(NOW),
                    limits=HubLimits(**limits))


def _order(call_id: str) -> Order:
    return Order(id=call_id, lines=[MEDOVIK], pickup_time="2026-11-08T18:30:00+09:00",
                 status="confirmed", created_at="2026-11-08T16:05:00+09:00")


async def _snapshot(hub: OwnerHub) -> dict:
    stream = hub.events()
    event = await anext(stream)
    await stream.aclose()
    return event.data


def _missed(hub: OwnerHub, call_id: str) -> None:
    hub.call_started(call_id)
    hub.call_ended(call_id, "hangup")


def _ordered(hub: OwnerHub, call_id: str) -> None:
    hub.call_started(call_id)
    hub.submit(_order(call_id))
    hub.call_ended(call_id, "confirmed")


def test_only_the_newest_ended_call_cards_are_kept() -> None:
    async def scenario() -> dict:
        hub = _hub(max_call_cards=3)
        for n in range(1, 7):
            _missed(hub, f"call-{n}")
        return await _snapshot(hub)

    assert [c["id"] for c in asyncio.run(scenario())["calls"]] == ["call-4", "call-5", "call-6"]


def test_a_card_dropped_for_the_limit_also_leaves_an_open_owner_screen() -> None:
    async def scenario() -> list[OwnerEvent]:
        hub = _hub(max_call_cards=1)
        stream = hub.events()
        await anext(stream)  # snapshot
        _missed(hub, "call-1")
        _missed(hub, "call-2")
        got = [await asyncio.wait_for(anext(stream), 2.0) for _ in range(5)]
        await stream.aclose()
        return got

    events = asyncio.run(scenario())
    assert [(e.data["id"], e.data["status"]) for e in events] == [
        ("call-1", "live"), ("call-1", "missed"), ("call-2", "live"), ("call-2", "missed"),
        ("call-1", "closed"),  # 화면에서도 빠진다
    ]


def test_live_calls_are_never_dropped() -> None:
    async def scenario() -> dict:
        hub = _hub(max_call_cards=1)
        hub.call_started("live-1")
        hub.call_started("live-2")
        _missed(hub, "call-1")
        _missed(hub, "call-2")
        return await _snapshot(hub)

    statuses = {c["id"]: c["status"] for c in asyncio.run(scenario())["calls"]}
    assert statuses == {"live-1": "live", "live-2": "live", "call-2": "missed"}


def test_ended_call_cards_expire_and_order_cards_stay_longer() -> None:
    async def scenario() -> tuple[dict, dict, dict]:
        clock = FakeClock(NOW)
        hub = _hub(clock, call_card_hours=24, order_card_hours=72)
        _missed(hub, "missed-1")
        _ordered(hub, "order-1")
        clock.set(NOW + timedelta(hours=25))
        _missed(hub, "missed-2")  # 정리는 통화가 끝날 때 돈다
        day_later = await _snapshot(hub)
        clock.set(NOW + timedelta(hours=73))
        _missed(hub, "missed-3")
        three_days = await _snapshot(hub)
        return day_later, three_days, hub.stats()

    day_later, three_days, stats = asyncio.run(scenario())
    assert [c["id"] for c in day_later["calls"]] == ["missed-2"]
    assert [o["id"] for o in day_later["orders"]] == ["order-1"]  # 주문은 아직 남는다
    assert [o["id"] for o in three_days["orders"]] == []
    assert [c["id"] for c in three_days["calls"]] == ["missed-3"]
    assert stats == {"calls": 1, "orders": 0, "questions": 0, "texts": 0}


def test_only_the_newest_order_cards_are_kept_and_their_texts_go_with_them() -> None:
    async def scenario() -> tuple[dict, OwnerHub]:
        hub = _hub(max_order_cards=2)
        for n in range(1, 5):
            _ordered(hub, f"call-{n}")
        return await _snapshot(hub), hub

    snapshot, hub = asyncio.run(scenario())
    assert [o["id"] for o in snapshot["orders"]] == ["call-3", "call-4"]
    assert hub.customer_texts("call-1") == [] and hub.customer_texts("call-4")
    assert hub.stats()["texts"] == 2


def test_zero_means_no_limit_and_the_defaults_are_roomy() -> None:
    async def scenario() -> dict:
        hub = _hub(max_call_cards=0, call_card_hours=0, max_order_cards=0, order_card_hours=0)
        for n in range(120):
            _missed(hub, f"call-{n}")
        return await _snapshot(hub)

    assert len(asyncio.run(scenario())["calls"]) == 120
    assert HubLimits() == HubLimits(max_call_cards=50, call_card_hours=24,
                                    max_order_cards=200, order_card_hours=72)


def test_hub_limits_come_from_the_environment() -> None:
    assert VoiceSettings.from_env({}, env_file=None).hub_limits() == HubLimits()
    settings = VoiceSettings.from_env({
        "PHONE_OWNER_MAX_CALL_CARDS": "20", "PHONE_OWNER_CALL_CARD_HOURS": "6",
        "PHONE_OWNER_MAX_ORDER_CARDS": "0", "PHONE_OWNER_ORDER_CARD_HOURS": "48",
    }, env_file=None)
    assert settings.hub_limits() == HubLimits(max_call_cards=20, call_card_hours=6,
                                              max_order_cards=0, order_card_hours=48)


def test_an_unhandled_missed_card_is_not_pushed_out_by_later_ordered_calls() -> None:
    """화면에 떠 있는 카드와 내려간 카드는 따로 센다 (6단계 리뷰 4번)."""
    async def scenario() -> dict:
        hub = _hub(max_call_cards=3)
        _missed(hub, "call-missed")
        for n in range(1, 6):
            _ordered(hub, f"call-{n}")
        return await _snapshot(hub)

    assert "call-missed" in [c["id"] for c in asyncio.run(scenario())["calls"]]
