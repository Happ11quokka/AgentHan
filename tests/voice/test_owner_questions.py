"""사장님 허브의 질문 카드(4단계 티켓 05): HTTP 없이 공개 인터페이스만 본다 (spec 이음매 3).

통화 세션이 `OwnerChannel.open_question`으로 질문을 열면 `owner.question` 카드가 가고, 사장님이
버튼으로 답하면(`answer_question`) 카드가 답함으로 바뀌고(`owner.question.update`) 답이 세션의 받는
곳으로 간다. 답은 한 번만 받는다(두 번째는 `AlreadyAnswered`, HTTP 409).
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from test_owner_calls import Screen

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.call.fakes import FakeClock
from agenthan.voice.dialog.owner_question import OwnerQuestion, OwnerReply
from agenthan.voice.owner.hub import AlreadyAnswered, OwnerHub, UnknownCard

NOW = datetime.fromisoformat("2026-11-08T16:02:00+09:00")
NUTS = OwnerQuestion(number=1, menu_id="m01", menu_name="메도빅", text="메도빅에 견과류 들어가요?")
CARD = {
    "id": "call-1-q1",
    "call_id": "call-1",
    "number": 1,
    "menu_id": "m01",
    "menu_name": "메도빅",
    "text": "메도빅에 견과류 들어가요?",
    "sent_at": "2026-11-08T16:02:00+09:00",
    "deadline_at": "2026-11-08T16:02:20+09:00",
    "status": "open",
    "answer": "",
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
