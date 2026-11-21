"""사장님 허브의 손님 주문 현황 (전화 메뉴·주문 현황·쿠폰 티켓 04): 공개 인터페이스·가짜 시계.

통화 뒤 확인 요청(통화 중 대기 0)은 처음부터 통화 뒤 단계로 열리고, 결정은 통화로 가지 않고 바로
굳는다. 주문 현황 단계는 사장님 결정을 따른다: 확인 대기 → 가게 확인 중, 수락 → 확정(늦춘 수락은
바뀐 시각과 원래 시각), 거절·자동 취소 → 주문 취소.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from agenthan.core.contracts import Order, OrderLine
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.call.fakes import FakeClock
from agenthan.voice.dialog.approval import ApprovalRequest, OwnerDecision
from agenthan.voice.owner import sms
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


# ---------------------------------------------------------------- 결과·사유 (티켓 05)


def test_a_placed_order_has_no_outcome_yet_and_can_be_cancelled() -> None:
    hub, _, _ = _placed()
    status = hub.order_status(CALL)

    assert (status["outcome"], status["reason"], status["sold_out"]) == (None, None, [])
    assert status["can_cancel"] is True  # 가게 확인 중이면 손님이 취소할 수 있다 (티켓 07)


def test_an_accept_on_time_or_later_says_which_kind_of_confirmation() -> None:
    hub, _, _ = _placed()
    hub.accept(CALL)
    assert hub.order_status(CALL)["outcome"] == "accepted"

    hub, _, _ = _placed()
    hub.accept(CALL, delay_min=30)
    status = hub.order_status(CALL)
    assert (status["stage"], status["outcome"]) == ("confirmed", "delayed")
    assert (status["pickup"]["label"], status["pickup"]["original_label"]) == ("19:00", "18:30")


def test_an_order_without_an_approval_request_is_confirmed_automatically() -> None:
    clock = FakeClock(NOW)
    hub = OwnerHub(store=FakeStoreKnowledge.from_fixture(), clock=clock)
    hub.submit(Order(id=CALL, lines=[OrderLine("m01", 1, name="메도빅", unit_price=7_500)],
                     pickup_time="2026-11-08T18:30:00+09:00", status="confirmed"))
    status = hub.order_status(CALL)

    assert (status["stage"], status["outcome"], status["reason"]) == ("confirmed", "auto", None)


@pytest.mark.parametrize(("reason", "text"), [
    ("busy", "죄송해요, 가게가 바빠서 주문을 받기 어려워 주문이 취소됐어요. "
             "다음에 다시 주문해 주세요."),
    ("closed", "죄송해요, 가게 사정으로 주문을 받을 수 없어 주문이 취소됐어요."),
])
def test_a_reject_shows_the_one_line_reason_the_customer_text_used(reason, text) -> None:
    hub, _, _ = _placed()
    hub.reject(CALL, reason)
    status = hub.order_status(CALL)

    assert (status["stage"], status["outcome"]) == ("cancelled", "rejected")
    assert status["reason"] == {"code": reason, "text": text}
    assert status["sold_out"] == []


class _Translator:
    """사장님 글 → 한국어. `done`을 풀기 전까지 번역을 붙잡는다(번역 중인 화면을 본다)."""

    def __init__(self) -> None:
        self.done = asyncio.Event()

    async def translate(self, texts, source: str, target: str) -> list[str]:
        await self.done.wait()
        return ["오늘은 일찍 닫아요" for _ in texts]


def test_a_written_reject_shows_the_owners_words_in_korean() -> None:
    async def scenario() -> tuple[dict, dict]:
        translator = _Translator()
        hub = OwnerHub(store=FakeStoreKnowledge.from_fixture(), clock=FakeClock(NOW),
                       translator=translator)
        order = Order(id=CALL, lines=[OrderLine("m01", 1, name="메도빅", unit_price=7_500)],
                      pickup_time="2026-11-08T18:30:00+09:00", status="confirmed")
        hub.open_approval(CALL, ApprovalRequest(1, order, in_call=False), lambda _: None)
        hub.reject(CALL, "free", text="Сегодня закрываемся рано")
        translating = hub.order_status(CALL)
        translator.done.set()
        for _ in range(10):
            await asyncio.sleep(0)
        return translating, hub.order_status(CALL)

    translating, translated = asyncio.run(scenario())

    assert (translating["outcome"], translating["reason"]) == (
        "rejected", {"code": "free", "text": ""})  # 번역 전에는 사장님 글을 아직 보이지 않는다
    assert translated["reason"] == {"code": "free", "text": "오늘은 일찍 닫아요"}


def test_a_written_reject_that_could_not_be_translated_shows_the_original() -> None:
    hub, _, _ = _placed()  # 번역기 없음 → 원문
    hub.reject(CALL, "free", text="Сегодня закрываемся рано")

    assert hub.order_status(CALL)["reason"] == {"code": "free", "text": "Сегодня закрываемся рано"}


def test_out_of_stock_names_the_menu_that_ran_out_and_sells_it_out_today() -> None:
    hub, _, _ = _placed()
    hub.reject(CALL, "out_of_stock", menu_ids=["m01"])
    status = hub.order_status(CALL)

    assert (status["stage"], status["outcome"]) == ("cancelled", "out_of_stock")
    assert status["sold_out"] == ["메도빅"]
    assert status["reason"] == {
        "code": "out_of_stock",
        "text": ("죄송해요, 메도빅은 지금 재료가 떨어져 주문이 취소됐어요. "
                 "다른 메뉴로 다시 주문해 주세요.")}
    assert hub.sold_out_today() == {"m01"}


def test_five_silent_minutes_cancel_with_the_expired_outcome() -> None:
    hub, clock, _ = _placed()
    clock.set(NOW + timedelta(minutes=5))
    status = hub.order_status(CALL)

    assert (status["stage"], status["outcome"], status["reason"]) == (
        "cancelled", "expired", None)


def test_the_line_under_the_headline_reuses_the_customer_text_sentences() -> None:
    """제목 밑 한 줄(`detail`)은 서버가 손님 문자 문장(`sms.py`)으로 준다 (리뷰: 웹이 문장을 다시
    쓰지 않는다). 확인 중 안내, 확정 픽업 문장, 늦춘 수락, 자동 취소, 거절 사유."""
    hub, clock, _ = _placed()
    assert hub.order_status(CALL)["detail"] == "확인되면 이 화면에서 알려 드려요."
    hub.accept(CALL)
    assert hub.order_status(CALL)["detail"] == sms.ready_line("18:30") == "18:30에 준비해 둘게요."

    hub, _, _ = _placed()
    hub.accept(CALL, delay_min=15)
    assert hub.order_status(CALL)["detail"] == (
        "픽업 시각이 18:30에서 18:45로 바뀌었어요. 18:45에 준비돼요. 어려우시면 전화 주세요.")

    hub, clock, _ = _placed()
    clock.set(NOW + timedelta(minutes=5))
    assert hub.order_status(CALL)["detail"] == sms.expired_text()

    hub, _, _ = _placed()
    hub.reject(CALL, "busy")
    status = hub.order_status(CALL)
    assert status["detail"] == status["reason"]["text"]

    hub, _, _ = _placed()
    hub.reject(CALL, "free", text="Сегодня закрываемся рано")
    assert hub.order_status(CALL)["detail"] == sms.REJECTED_FREE  # 사장님 글은 reason.text


# ---------------------------------------------------------------- 실시간 갱신 (티켓 05)


async def _next(stream) -> dict:
    event = await asyncio.wait_for(anext(stream), 1)
    assert event.name == "order.status"
    return event.data


def test_the_order_stream_starts_with_the_status_and_follows_each_decision() -> None:
    hub, _, _ = _placed()

    async def scenario() -> tuple[dict, dict]:
        stream = hub.watch_order(CALL)
        first = await _next(stream)
        hub.accept(CALL, delay_min=15)
        second = await _next(stream)
        await stream.aclose()
        return first, second

    first, second = asyncio.run(scenario())

    assert (first["stage"], first["outcome"]) == ("checking", None)
    assert (second["stage"], second["outcome"]) == ("confirmed", "delayed")
    assert second == hub.order_status(CALL)  # 조회 API와 같은 모양


def test_the_order_stream_shows_the_auto_cancel() -> None:
    hub, clock, _ = _placed()

    async def scenario() -> dict:
        stream = hub.watch_order(CALL)
        await _next(stream)
        clock.set(NOW + timedelta(minutes=5))
        hub.expire_due()  # 허브 마감 타이머가 부르는 것
        expired = await _next(stream)
        await stream.aclose()
        return expired

    expired = asyncio.run(scenario())

    assert (expired["stage"], expired["outcome"]) == ("cancelled", "expired")


def test_the_order_stream_does_not_repeat_an_unchanged_status() -> None:
    hub, _, _ = _placed()

    async def scenario() -> tuple[dict, bool]:
        stream = hub.watch_order(CALL)
        await _next(stream)
        hub.reject(CALL, "busy")  # 카드는 거절 + 문자로 두 번 바뀌지만 손님 현황은 한 번
        rejected = await _next(stream)
        try:
            await asyncio.wait_for(anext(stream), 0.05)
            repeated = True
        except TimeoutError:
            repeated = False
        await stream.aclose()
        return rejected, repeated

    rejected, repeated = asyncio.run(scenario())

    assert rejected["outcome"] == "rejected"
    assert not repeated


def test_an_order_stream_for_an_unknown_order_is_refused() -> None:
    hub, _, _ = _placed()
    with pytest.raises(UnknownCard):
        hub.watch_order("call-nope")
