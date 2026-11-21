"""늦춘 수락(+15분·+30분, 주문 승인 티켓 04): 사장님 허브 공개 인터페이스 (이음매 3).

- 통화 뒤: 바로 accepted + 바뀐 픽업 시각 + "{시각}에 준비돼요. 어려우시면 전화 주세요." 문자
- 통화 중: 결정을 세션에 넘기고, 통화가 바뀐 시각을 묻기 시작하면(`approval_said`) 손님 답을
  기다린다(문자 없음). "네"면 확정 주문이 오고 문자,
  "아니요"면 세션이 그 요청을 취소로 거둬 카드가 cancelled, 답 없이 끝나면 통화 뒤와 같다
- 결정 기록(티켓 10)에는 `owner_pickup_time`이 실린다
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import pytest

from agenthan.core.contracts import Order, OrderLine
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.call.fakes import FakeClock
from agenthan.voice.dialog.approval import ApprovalRequest, OwnerDecision
from agenthan.voice.owner.decisions import DecisionRecord
from agenthan.voice.owner.hub import AlreadyDecided, OwnerHub

NOW = datetime.fromisoformat("2026-11-08T16:02:00+09:00")
WAIT = 2.0
MEDOVIK = OrderLine("m01", 2, name="메도빅", unit_price=7_500)
DELAYED_TEXT = ("주문이 확정됐어요. 메도빅 2개, 합계 15,000원. "
                "18:45에 준비돼요. 어려우시면 전화 주세요.")


class Recorded:
    def __init__(self) -> None:
        self.decisions: list[DecisionRecord] = []

    def record(self, decision: DecisionRecord) -> None:
        self.decisions.append(decision)


def _hub(sink: Recorded | None = None) -> OwnerHub:
    return OwnerHub(store=FakeStoreKnowledge.from_fixture(), clock=FakeClock(NOW),
                    approval_wait_s=20.0, approval_expire_s=300.0, decision_sink=sink)


def _order(pickup: str = "2026-11-08T18:30:00+09:00") -> Order:
    return Order(id="call-1", lines=[MEDOVIK], pickup_time=pickup, status="confirmed",
                 created_at="2026-11-08T16:02:00+09:00")


async def _cards(hub: OwnerHub) -> list[dict[str, Any]]:
    """새로 연 화면이 받는 주문 카드들 (snapshot)."""
    stream = hub.events()
    snapshot = await asyncio.wait_for(anext(stream), WAIT)
    await stream.aclose()
    return snapshot.data["orders"]


def _texts(hub: OwnerHub) -> list[tuple[str, str]]:
    return [(t["kind"], t["text"]) for t in hub.customer_texts("call-1")]


def test_after_the_call_a_delayed_accept_texts_the_new_time_at_once() -> None:
    sink = Recorded()
    hub = _hub(sink)
    hub.open_approval("call-1", ApprovalRequest(1, _order()), lambda d: None)
    hub.call_ended("call-1", "confirmed")  # 시간 초과 문자 약속 → 통화 뒤 대기

    card = hub.accept("call-1", delay_min=15)

    assert (card["status"], card["delay_min"], card["delay_reply"]) == ("accepted", 15, "")
    assert (card["owner_pickup_time"], card["owner_pickup_label"]) == (
        "2026-11-08T18:45:00+09:00", "18:45")
    assert card["pickup_label"] == "18:30"  # 손님이 처음 말한 시각은 그대로
    assert _texts(hub) == [("delayed", DELAYED_TEXT)]
    (record,) = sink.decisions
    assert (record.owner_status, record.owner_pickup_time) == (
        "accepted", "2026-11-08T18:45:00+09:00")
    with pytest.raises(AlreadyDecided):
        hub.accept("call-1", delay_min=30)  # 결정은 한 번


def test_in_call_the_decision_goes_to_the_call_and_the_text_waits_for_the_customer() -> None:
    decisions: list[OwnerDecision] = []

    async def scenario() -> tuple[dict[str, Any], list[dict[str, Any]]]:
        hub = _hub()
        hub.open_approval("call-1", ApprovalRequest(1, _order()), decisions.append)
        card = hub.accept("call-1", delay_min=30)
        await asyncio.sleep(0)  # 결정은 루프 다음 차례에 세션으로
        hub.approval_said("call-1", 1)  # 통화가 바뀐 시각을 묻기 시작했다
        return card, await _cards(hub)

    card, (shown,) = asyncio.run(scenario())
    assert decisions == [OwnerDecision(1, "delay", 30)]
    assert (card["status"], card["owner_pickup_label"]) == ("accepted", "19:00")
    assert shown["delay_reply"] == "asking"
    assert shown["sms"] == []  # 손님이 답하기 전에는 문자 없음


def test_the_customer_saying_yes_brings_the_order_at_the_new_time_and_the_text() -> None:
    sink = Recorded()
    hub = _hub(sink)
    hub.open_approval("call-1", ApprovalRequest(1, _order()), lambda d: None)
    hub.accept("call-1", delay_min=15)
    hub.approval_said("call-1", 1)  # 통화가 바뀐 시각을 묻는다
    hub.submit(_order("2026-11-08T18:45:00+09:00"))  # 엔진이 바뀐 시각으로 확정
    hub.call_ended("call-1", "confirmed")

    (card,) = asyncio.run(_cards(hub))
    assert (card["status"], card["delay_reply"]) == ("accepted", "yes")
    assert _texts(hub) == [("delayed", DELAYED_TEXT)]  # 한 번만
    assert [(r.owner_status, r.owner_pickup_time) for r in sink.decisions] == [
        ("accepted", "2026-11-08T18:45:00+09:00")]


def test_the_customer_saying_no_cancels_the_card_without_a_text() -> None:
    sink = Recorded()
    hub = _hub(sink)
    hub.open_approval("call-1", ApprovalRequest(1, _order()), lambda d: None)
    hub.accept("call-1", delay_min=15)
    hub.approval_said("call-1", 1)
    hub.withdraw_approval("call-1", 1, "cancelled")  # 엔진이 "아니요"에 요청을 취소로 거둠
    hub.call_ended("call-1", "cancelled")

    (card,) = asyncio.run(_cards(hub))
    assert (card["status"], card["delay_reply"], card["owner_pickup_label"]) == (
        "cancelled", "no", "18:45")
    assert _texts(hub) == []
    assert [r.owner_status for r in sink.decisions] == ["accepted", "cancelled"]
    with pytest.raises(AlreadyDecided):
        hub.accept("call-1")


@pytest.mark.parametrize("handed_over", [True, False])
def test_a_call_that_ends_without_an_answer_becomes_a_post_call_delayed_accept(
        handed_over: bool) -> None:
    """손님이 끊거나 말이 없어 끝났다. 세션이 원래 주문서를 넘기든(끊음) 안 넘기든 문자는 한 번."""
    hub = _hub()
    hub.call_started("call-1")
    hub.open_approval("call-1", ApprovalRequest(1, _order()), lambda d: None)
    hub.accept("call-1", delay_min=15)
    hub.approval_said("call-1", 1)  # 손님이 바뀐 시각을 들었다
    if handed_over:
        hub.submit(_order())  # 원래 시각 그대로 (손님이 "네"라고 하지 않았다)
    hub.call_ended("call-1", "hangup")

    async def screen() -> dict[str, Any]:
        stream = hub.events()
        snapshot = (await asyncio.wait_for(anext(stream), WAIT)).data
        await stream.aclose()
        return snapshot

    snapshot = asyncio.run(screen())
    (card,) = snapshot["orders"]
    assert (card["status"], card["delay_reply"]) == ("accepted", "")
    assert _texts(hub) == [("delayed", DELAYED_TEXT)]
    assert snapshot["calls"] == []  # 통화 카드는 주문 카드가 대신한다(미완료로 남지 않음)


def test_a_late_decision_on_a_delayed_card_is_refused() -> None:
    hub = _hub()
    hub.open_approval("call-1", ApprovalRequest(1, _order()), lambda d: None)
    hub.accept("call-1", delay_min=15)
    with pytest.raises(AlreadyDecided):
        hub.accept("call-1")


def test_a_stale_withdraw_does_not_cancel_a_delayed_accept() -> None:
    hub = _hub()
    hub.open_approval("call-1", ApprovalRequest(2, _order()), lambda d: None)

    hub.accept("call-1", delay_min=15)
    hub.approval_said("call-1", 2)
    hub.withdraw_approval("call-1", 1, "cancelled")  # 앞 번호
    (card,) = asyncio.run(_cards(hub))
    assert (card["status"], card["delay_reply"]) == ("accepted", "asking")
