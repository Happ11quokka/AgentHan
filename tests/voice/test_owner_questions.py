"""사장님 허브의 질문 카드(4단계 티켓 05): HTTP 없이 공개 인터페이스만 본다 (spec 이음매 3).

통화 세션이 `OwnerChannel.open_question`으로 질문을 열면 `owner.question` 카드가 가고, 사장님이
버튼으로 답하면(`answer_question`) 카드가 답함으로 바뀌고(`owner.question.update`) 답이 세션의 받는
곳으로 간다. 답은 한 번만 받는다(두 번째는 `AlreadyAnswered`, HTTP 409).

티켓 06: 시간 초과(`question_timed_out`)·통화 끝(`call_ended`)은 카드 상태만 바꾸고 카드는 계속
답을 받는다. 카드 열람(`see_question`)은 세션에 한 번 알린다. 답을 못 받은 질문이 있는 통화의 확정
주문 카드에는 "사장님 확인 필요"(`owner_check`)가 붙는다.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime

import pytest
from test_owner_calls import Screen

from agenthan.core.contracts import Order
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.call.fakes import FakeClock
from agenthan.voice.dialog.owner_question import OwnerQuestion, OwnerReply, OwnerSeen
from agenthan.voice.owner.hub import AlreadyAnswered, OwnerHub, UnknownCard

NOW = datetime.fromisoformat("2026-11-08T16:02:00+09:00")
NUTS = OwnerQuestion(number=1, menu_id="m01", menu_name="메도빅", text="메도빅에 견과류 들어가요?")
CARD = {
    "id": "call-1-q1",
    "call_id": "call-1",
    "number": 1,
    "menu_id": "m01",
    "menu_name": "메도빅",
    "menu_name_owner": "Медовик",  # 티켓 03
    "text": "메도빅에 견과류 들어가요?",
    "text_owner": "",  # 번역기 없음 → 원문만 (티켓 03)
    "sent_at": "2026-11-08T16:02:00+09:00",
    "deadline_at": "2026-11-08T16:02:20+09:00",
    "status": "open",
    "answer": "",
    "seen_at": "",
    "answer_text": "",  # 티켓 07: 자유 입력 답의 사장님 글
    "answer_text_ko": "",
}


def _hub() -> OwnerHub:
    return OwnerHub(store=FakeStoreKnowledge.from_fixture(), clock=FakeClock(NOW))


def test_an_opened_question_shows_a_card_and_a_button_answer_reaches_the_call() -> None:
    async def scenario():
        hub = _hub()
        screen = Screen(hub)
        await screen.take()  # snapshot
        replies: list[OwnerReply] = []
        hub.call_started("call-1")
        await screen.take()  # call.update live
        hub.open_question("call-1", NUTS, replies.append)
        [opened] = await screen.take()
        answered = hub.answer_question("call-1-q1", "yes")
        [updated] = await screen.take()
        await screen.close()
        return opened, answered, updated, replies

    opened, answered, updated, replies = asyncio.run(scenario())
    assert (opened.name, opened.data) == ("owner.question", CARD)
    assert answered == {**CARD, "status": "answered", "answer": "yes"}
    assert (updated.name, updated.data) == ("owner.question.update", answered)
    assert replies == [OwnerReply(question=1, answer="yes")]


def test_a_question_is_answered_only_once() -> None:
    hub = _hub()
    replies: list[OwnerReply] = []
    hub.open_question("call-1", NUTS, replies.append)
    hub.answer_question("call-1-q1", "no")
    with pytest.raises(AlreadyAnswered):
        hub.answer_question("call-1-q1", "yes")
    assert replies == [OwnerReply(question=1, answer="no")]


def test_an_unknown_question_or_answer_is_refused() -> None:
    hub = _hub()
    hub.open_question("call-1", NUTS, lambda reply: None)
    with pytest.raises(UnknownCard):
        hub.answer_question("call-1-q9", "yes")
    with pytest.raises(ValueError):
        hub.answer_question("call-1-q1", "maybe")


def test_a_broken_call_inbox_does_not_break_the_answer() -> None:
    def broken(reply: OwnerReply) -> None:
        raise RuntimeError("세션이 이미 사라짐")

    hub = _hub()
    hub.open_question("call-1", NUTS, broken)
    assert hub.answer_question("call-1-q1", "unsure")["status"] == "answered"


def test_the_snapshot_carries_question_cards() -> None:
    async def scenario():
        hub = _hub()
        hub.open_question("call-1", NUTS, lambda reply: None)
        screen = Screen(hub)
        [snapshot] = await screen.take()
        await screen.close()
        return snapshot

    assert asyncio.run(scenario()).data["questions"] == [CARD]


# ---------------------------------------------------------------- 티켓 06


def snapshot(hub: OwnerHub) -> dict:
    """새 화면이 받는 snapshot. 허브가 이 루프에 묶이니 시험의 마지막에 부른다."""
    async def take() -> dict:
        screen = Screen(hub)
        [snap] = await screen.take()
        await screen.close()
        return snap.data

    return asyncio.run(take())


def test_a_timed_out_question_stays_answerable() -> None:
    hub = _hub()
    replies: list[OwnerReply] = []
    hub.open_question("call-1", NUTS, replies.append)
    hub.question_timed_out("call-1", 1)
    assert snapshot(hub)["questions"][0]["status"] == "timeout"
    late = hub.answer_question("call-1-q1", "yes")
    assert (late["status"], late["answer"]) == ("answered", "yes")
    assert replies == [OwnerReply(question=1, answer="yes")]  # 세션이 번호로 버린다


@pytest.mark.parametrize("reason", ["hangup", "confirmed", "error"])
def test_when_the_call_ends_its_open_questions_say_call_ended(reason: str) -> None:
    async def scenario():
        hub = _hub()
        hub.call_started("call-1")
        hub.open_question("call-1", NUTS, lambda reply: None)
        hub.open_question("call-2", replace(NUTS, number=1), lambda reply: None)
        screen = Screen(hub)
        await screen.take()  # snapshot
        hub.call_ended("call-1", reason)
        events = await screen.take(2)  # 통화 카드(미완료)·질문 카드
        await screen.nothing_more()
        await screen.close()
        return hub, events

    hub, events = asyncio.run(scenario())
    updates = [e.data for e in events if e.name == "owner.question.update"]
    assert [(u["id"], u["status"]) for u in updates] == [("call-1-q1", "call_ended")]
    assert hub.answer_question("call-1-q1", "no")["status"] == "answered"
    assert {q["id"]: q["status"] for q in snapshot(hub)["questions"]} == {
        "call-1-q1": "answered", "call-2-q1": "open"}


def test_a_timed_out_or_answered_question_keeps_its_status_when_the_call_ends() -> None:
    hub = _hub()
    hub.open_question("call-1", NUTS, lambda reply: None)
    hub.open_question("call-1", replace(NUTS, number=2), lambda reply: None)
    hub.question_timed_out("call-1", 1)
    hub.answer_question("call-1-q2", "yes")
    hub.call_ended("call-1", "hangup")
    assert [q["status"] for q in snapshot(hub)["questions"]] == ["timeout", "answered"]


def test_seeing_a_card_tells_the_call_once_and_stamps_the_card() -> None:
    hub = _hub()
    notes: list[object] = []
    hub.open_question("call-1", NUTS, notes.append)
    seen = hub.see_question("call-1-q1")
    again = hub.see_question("call-1-q1")
    assert seen["seen_at"] == "2026-11-08T16:02:00+09:00"
    assert again == seen
    assert notes == [OwnerSeen(question=1)]
    with pytest.raises(UnknownCard):
        hub.see_question("call-1-q9")


def test_seeing_an_answered_card_does_not_tell_the_call() -> None:
    hub = _hub()
    notes: list[object] = []
    hub.open_question("call-1", NUTS, notes.append)
    hub.answer_question("call-1-q1", "yes")
    hub.see_question("call-1-q1")
    assert notes == [OwnerReply(question=1, answer="yes")]


def _order(call_id: str) -> Order:
    return Order(id=call_id, lines=[], pickup_time="2026-11-08T18:30:00+09:00",
                 customer_phone="010-0000-0000", status="confirmed", transcript=[],
                 created_at="2026-11-08T16:05:00+09:00")


@pytest.mark.parametrize(("settle", "check"), [
    (None, True),  # 마무리 때 열린 질문 (문자 약속)
    ("timeout", True),  # 시간 초과 (문자 약속)
    ("answered", False),
])
def test_a_confirmed_order_with_an_unanswered_question_needs_the_owner(settle, check) -> None:
    hub = _hub()
    hub.call_started("call-1")
    hub.open_question("call-1", NUTS, lambda reply: None)
    if settle == "timeout":
        hub.question_timed_out("call-1", 1)
    elif settle == "answered":
        hub.answer_question("call-1-q1", "yes")
    hub.submit(_order("call-1"))
    hub.submit(_order("call-2"))
    assert [(o["id"], o["owner_check"]) for o in snapshot(hub)["orders"]] == [
        ("call-1", check), ("call-2", False)]
