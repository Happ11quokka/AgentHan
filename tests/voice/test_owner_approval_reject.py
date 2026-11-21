"""사장님 허브의 거절 (주문 승인 티켓 05, 이음매 3): 공개 인터페이스·가짜 시계·가짜 번역기.

[거절] → 사유(바쁨·가게 사정·직접 쓰기) → 카드 rejected + 사유, 손님에게 사유별 취소 문자, 통화
중이면 통화 세션에 결정. 직접 쓰기 글은 번역 포트로 한국어로 옮겨 인용 틀에 넣는다. 결정은 한
번만(409), 자동 취소된 카드도 409. 결정 기록(티켓 10)은 카드 칸 그대로 간다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from test_owner_decisions import Recorded

from agenthan.core.contracts import Order, OrderLine
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.call.fakes import FakeClock
from agenthan.voice.dialog.approval import ApprovalRequest, OwnerDecision
from agenthan.voice.owner import sms
from agenthan.voice.owner.fakes import FakeTranslator
from agenthan.voice.owner.hub import AlreadyDecided, CardBusy, OwnerHub, UnknownCard
from agenthan.voice.owner.translate import Translator

NOW = datetime.fromisoformat("2026-11-08T16:02:00+09:00")
LATER = datetime.fromisoformat("2026-11-08T16:02:07+09:00")
MEDOVIK = OrderLine("m01", 2, name="메도빅", unit_price=7_500)
BUSY_TEXT = ("죄송해요, 가게가 바빠서 주문을 받기 어려워 주문이 취소됐어요. "
             "다음에 다시 주문해 주세요.")
CLOSED_TEXT = "죄송해요, 가게 사정으로 주문을 받을 수 없어 주문이 취소됐어요."
WRITTEN = "Сегодня закрываемся раньше"


def _hub(clock: FakeClock | None = None, *, translator: Translator | None = None,
         sink: Recorded | None = None) -> OwnerHub:
    return OwnerHub(store=FakeStoreKnowledge.from_fixture(), clock=clock or FakeClock(NOW),
                    approval_wait_s=20.0, approval_expire_s=300.0, translator=translator,
                    decision_sink=sink)


def _order(call_id: str = "call-1") -> Order:
    return Order(id=call_id, lines=[MEDOVIK], pickup_time="2026-11-08T18:30:00+09:00",
                 status="confirmed", created_at="2026-11-08T16:02:00+09:00")


def _requested(hub: OwnerHub, decisions: list[OwnerDecision] | None = None) -> None:
    hub.call_started("call-1")
    hub.open_approval("call-1", ApprovalRequest(1, _order()),
                      (decisions if decisions is not None else []).append)


async def _snapshot(hub: OwnerHub) -> dict:
    """새로 연 사장님 화면이 받는 snapshot (카드를 바깥에서 본다)."""
    stream = hub.events()
    event = await asyncio.wait_for(anext(stream), 2.0)
    await stream.aclose()
    assert event.name == "snapshot"
    return event.data


@pytest.mark.parametrize(("reason", "text"), [("busy", BUSY_TEXT), ("closed", CLOSED_TEXT)])
def test_an_in_call_rejection_texts_the_reason_and_tells_the_call(reason, text) -> None:
    decisions: list[OwnerDecision] = []
    clock = FakeClock(NOW)
    hub = _hub(clock)
    _requested(hub, decisions)
    clock.set(LATER)

    card = hub.reject("call-1", reason)

    assert (card["status"], card["reject_reason"], card["decided_at"]) == (
        "rejected", reason, LATER.isoformat())
    assert [(t["kind"], t["text"]) for t in card["sms"]] == [("rejected", text)]
    assert decisions == [OwnerDecision(1, "reject", reason=reason)]
    assert [t["kind"] for t in hub.customer_texts("call-1")] == ["rejected"]  # 확정 문자 없음


def test_the_rejected_card_stands_in_for_the_call_that_ends_cancelled() -> None:
    hub = _hub()
    _requested(hub)
    hub.reject("call-1", "busy")
    hub.call_ended("call-1", "cancelled")  # 손님이 사과를 듣고 통화가 끝남

    snap = asyncio.run(_snapshot(hub))
    assert [(c["id"], c["status"]) for c in snap["orders"]] == [("call-1", "rejected")]
    assert snap["calls"] == []  # 미완료 통화 카드가 따로 뜨지 않는다


def test_an_after_call_rejection_only_texts_the_customer() -> None:
    decisions: list[OwnerDecision] = []
    hub = _hub()
    _requested(hub, decisions)
    hub.call_ended("call-1", "confirmed")  # 시간 초과 문자 약속 → 통화 뒤 대기

    card = hub.reject("call-1", "closed")

    assert (card["status"], card["stage"]) == ("rejected", "after_call")
    assert [t["text"] for t in card["sms"]] == [CLOSED_TEXT]
    assert decisions == []  # 통화는 끝났다


def test_a_decision_is_taken_once_across_accept_and_reject() -> None:
    hub = _hub()
    _requested(hub)
    hub.reject("call-1", "busy")
    with pytest.raises(AlreadyDecided):
        hub.reject("call-1", "closed")  # 두 화면에서 같이 눌러도 한 번
    with pytest.raises(AlreadyDecided):
        hub.accept("call-1")

    accepted = _hub()
    _requested(accepted)
    accepted.accept("call-1")
    with pytest.raises(AlreadyDecided):
        accepted.reject("call-1", "busy")


def test_an_expired_card_cannot_be_rejected() -> None:
    clock = FakeClock(NOW)
    hub = _hub(clock)
    _requested(hub)
    hub.call_ended("call-1", "hangup")
    clock.set(NOW + timedelta(minutes=5))  # 타이머가 늦어도 결정 앞에서 자동 취소한다

    with pytest.raises(AlreadyDecided):
        hub.reject("call-1", "busy")
    assert [t["kind"] for t in hub.customer_texts("call-1")] == ["expired"]


def test_a_withdrawn_request_or_an_unknown_card_cannot_be_rejected() -> None:
    hub = _hub()
    _requested(hub)
    hub.withdraw_approval("call-1", 1, "changed")  # 손님이 고치는 중
    with pytest.raises(CardBusy):
        hub.reject("call-1", "busy")
    with pytest.raises(UnknownCard):
        hub.reject("nope", "busy")


def test_an_empty_written_reason_and_out_of_stock_are_refused_for_now() -> None:
    hub = _hub()
    _requested(hub)
    with pytest.raises(ValueError):
        hub.reject("call-1", "free", text="   ")
    with pytest.raises(ValueError):
        hub.reject("call-1", "out_of_stock", menu_ids=["m01"])  # 재료 소진은 티켓 06
    with pytest.raises(ValueError):
        hub.reject("call-1", "rain")  # type: ignore[arg-type]
    assert hub.customer_texts("call-1") == []  # 거절되지 않았다
    hub.reject("call-1", "busy")  # 아직 결정 전이라 받는다


def test_a_written_reason_is_translated_into_the_quote_frame() -> None:
    decisions: list[OwnerDecision] = []
    translator = FakeTranslator({WRITTEN: "오늘 일찍 문을 닫아요"})

    async def scenario() -> tuple[dict, dict]:
        hub = _hub(translator=translator)
        _requested(hub, decisions)
        first = hub.reject("call-1", "free", text=f"  {WRITTEN} ")
        for _ in range(20):  # 번역은 비동기다
            await asyncio.sleep(0)
        return first, (await _snapshot(hub))["orders"][0]

    first, card = asyncio.run(scenario())
    assert (first["reject_reason"], first["reject_text"], first["sms"]) == ("free", WRITTEN, [])
    assert decisions == [OwnerDecision(1, "reject", reason="free")]  # 글은 통화에 안 간다
    assert card["reject_text_ko"] == "오늘 일찍 문을 닫아요"
    (sent,) = card["sms"]
    assert (sent["kind"], sent["translate_failed"]) == ("rejected", False)
    assert sent["text"] == ("죄송해요, 가게 사정으로 주문이 취소됐어요. "
                           "사장님 말씀(번역): «오늘 일찍 문을 닫아요»")
    assert translator.calls == [((WRITTEN,), "ru", "ko")]


def test_a_written_reason_that_fails_to_translate_is_texted_as_written() -> None:
    async def scenario() -> dict:
        hub = _hub(translator=FakeTranslator(fail=True))
        _requested(hub)
        hub.reject("call-1", "free", text=WRITTEN)
        for _ in range(20):
            await asyncio.sleep(0)
        return (await _snapshot(hub))["orders"][0]

    card = asyncio.run(scenario())
    assert card["reject_text_ko"] == ""
    (sent,) = card["sms"]
    assert sent["translate_failed"] is True
    assert sent["text"] == ("죄송해요, 가게 사정으로 주문이 취소됐어요. "
                            f"사장님 말씀(원문, 번역 실패): «{WRITTEN}»")


def test_a_rejection_is_recorded_with_its_reason() -> None:
    sink, clock = Recorded(), FakeClock(NOW)
    hub = _hub(clock, sink=sink)
    _requested(hub)
    clock.set(LATER)
    hub.reject("call-1", "busy")
    hub.call_ended("call-1", "cancelled")

    (record,) = sink.decisions
    assert (record.owner_status, record.reject_reason, record.decided_at) == (
        "rejected", "busy", LATER.isoformat())
    assert record.order == _order()  # 통화가 취소로 끝나 주문 기록이 없어도 행을 채운다


def test_the_out_of_stock_text_names_the_menus_for_ticket_06() -> None:
    assert sms.rejected_text("out_of_stock", ["김치찌개", "메도빅"]) == (
        "죄송해요, 김치찌개, 메도빅은 지금 재료가 떨어져 주문이 취소됐어요. "
        "다른 메뉴로 다시 주문해 주세요.")
