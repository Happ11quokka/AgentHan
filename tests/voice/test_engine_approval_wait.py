"""주문 승인 티켓 07: 사장님 확인을 기다리는 중의 손님 말과 재안내 (엔진 이음매, spec "엔진").

기다리는 중에 취소는 평소처럼("주문 취소할까요?" → "네") 처리하고 요청을 닫는다. 메뉴·수량·시각을
고치면 요청을 거두고 다시 복창한다. 그 밖의 말은 "확인 중이에요, 잠시만요."로 받는다. 재안내
("조금만 더 기다려 주세요.")는 세션이 봇 턴으로 넣는다.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.approval import ApprovalWithdrawn, OwnerDecision
from agenthan.voice.dialog.engine import DialogEngine, TurnResult
from agenthan.voice.dialog.states import State
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.store_config import KST, StoreConfig

NOW = datetime(2026, 11, 8, 16, 0, tzinfo=KST)
HOLD = "사장님께 주문 확인 중이에요. 잠시만요."
CHECKING = "확인 중이에요, 잠시만요."
NUDGE = "조금만 더 기다려 주세요."
CLOSING = "네, 6시 반까지 준비해 둘게요. 감사합니다."
READBACK = "메도빅 하나, 6시 반에 찾으러 오시고, 모두 7,500원이에요. 맞으세요?"


def waiting() -> DialogEngine:
    """복창에 "네"까지 가서 확인 요청 1번을 연 엔진."""
    engine = DialogEngine(FakeStoreKnowledge.from_fixture(), FakeNlu(),
                          StoreConfig.from_fixture(), order_id="o-test")
    engine.start(NOW)
    for text in ("메도빅 하나요", "그게 다예요", "6시 반이요", "네"):
        result = engine.handle(text, NOW, approval=True, wait_in_call=True)
    assert result.approval_request is not None
    return engine


def talk(engine: DialogEngine, text: str) -> TurnResult:
    return engine.handle(text, NOW, approval=True, wait_in_call=True)


@pytest.mark.parametrize("text", [
    "메도빅 달아요?",  # 메뉴 질문
    "뭐라고요?",  # 다시 말해 달라
    "아니요",
    "음",  # 못 알아들은 말
    "그게 다예요",
    "메도빅이요",  # 복창의 메뉴 이름만 되풀이
])
def test_other_speech_while_waiting_is_met_with_still_checking(text: str) -> None:
    engine = waiting()
    r = talk(engine, text)

    assert r.utterances == [CHECKING]
    assert (r.state, r.order, r.approval_withdrawn, r.readback) == (
        State.CONFIRM, None, None, False)
    assert r.reask == ""  # 못 알아들음으로 세지 않는다
    assert engine.owner_decided(OwnerDecision(1, "accept"), NOW).utterances == [CLOSING]


def test_other_speech_does_not_ask_the_owner_a_menu_question() -> None:
    engine = DialogEngine(FakeStoreKnowledge.from_fixture(), FakeNlu(),
                          StoreConfig.from_fixture(), order_id="o-test", ask_owner=True)
    engine.start(NOW)
    for text in ("메도빅 하나요", "그게 다예요", "6시 반이요", "네"):
        engine.handle(text, NOW, approval=True, wait_in_call=True)

    r = talk(engine, "메도빅에 견과 들어가요?")

    assert r.owner_question is None and r.knowledge_requests == ()
    assert r.utterances == [CHECKING]


def test_adding_a_menu_while_waiting_withdraws_and_reads_back_the_new_order() -> None:
    engine = waiting()
    r = talk(engine, "블린 하나 추가요")

    assert r.approval_withdrawn == ApprovalWithdrawn(1, "changed")
    assert r.readback is True and r.state is State.CONFIRM
    assert r.utterances[-1].endswith("모두 16,500원이에요. 맞으세요?")
    again = talk(engine, "네")
    assert again.approval_request is not None and again.approval_request.number == 2
    assert again.utterances == [HOLD]


def test_changing_the_pickup_time_while_waiting_also_withdraws() -> None:
    engine = waiting()
    r = talk(engine, "7시로 바꿔 주세요")

    assert r.approval_withdrawn == ApprovalWithdrawn(1, "changed")
    assert r.readback is True


def test_cancel_while_waiting_withdraws_at_the_question_and_cancels_on_yes() -> None:
    engine = waiting()

    asked = talk(engine, "취소할게요")
    # 묻는 사이에 사장님이 수락하지 않게 바로 거둔다(사장님 카드: 고치는 중)
    assert asked.utterances == ["주문 취소할까요?"]
    assert asked.approval_withdrawn == ApprovalWithdrawn(1, "changed")
    assert engine.owner_decided(OwnerDecision(1, "accept"), NOW).utterances == []  # 낡은 결정

    done = talk(engine, "네")
    assert done.utterances == ["네, 주문 취소했어요. 감사합니다."]
    assert done.approval_withdrawn == ApprovalWithdrawn(1, "cancelled")  # 카드는 취소로
    assert done.order is None and done.state is State.DONE


def test_declining_the_cancel_reads_back_and_a_yes_asks_again_with_a_new_number() -> None:
    engine = waiting()
    talk(engine, "취소할게요")

    kept = talk(engine, "아니요")
    assert kept.utterances == ["네, 계속 도와드릴게요.", f"주문 확인할게요. {READBACK}"]
    assert kept.readback is True and kept.approval_withdrawn is None
    again = talk(engine, "네")
    assert again.approval_request is not None and again.approval_request.number == 2
    closed = engine.owner_decided(OwnerDecision(2, "accept"), NOW)
    assert closed.utterances == [CLOSING]
    assert closed.approval_withdrawn is None


def test_no_input_after_the_cancel_question_ends_as_a_cancelled_request() -> None:
    engine = waiting()
    talk(engine, "취소할게요")
    for _ in range(2):
        assert engine.no_input(NOW).approval_withdrawn is None
    gone = engine.no_input(NOW)

    assert gone.state is State.DONE
    assert gone.approval_withdrawn == ApprovalWithdrawn(1, "cancelled")


def test_the_nudge_is_said_only_while_that_request_is_waiting() -> None:
    engine = waiting()

    r = engine.approval_still_waiting(1, NOW)
    assert r.utterances == [NUDGE]
    assert (r.state, r.order, r.readback) == (State.CONFIRM, None, False)
    assert engine.approval_still_waiting(2, NOW).utterances == []  # 다른 번호
    assert engine.owner_decided(OwnerDecision(1, "accept"), NOW).utterances == [CLOSING]
    assert engine.approval_still_waiting(1, NOW).utterances == []  # 결정 뒤


def test_the_nudge_is_dropped_after_the_request_was_withdrawn() -> None:
    engine = waiting()
    talk(engine, "취소할게요")

    assert engine.approval_still_waiting(1, NOW).utterances == []


def test_a_correction_that_cannot_be_made_while_waiting_does_not_read_back_again() -> None:
    """고치려 했지만 못 알아들은 말(리뷰 4번): 복창을 다시 하면 손님이 "네"로 또 답한다. 사과 뒤
    "확인 중"만 말하고 요청은 그대로 기다린다."""
    engine = waiting()
    r = talk(engine, "나폴레옹 0개요")

    assert r.utterances == ["죄송해요, 제가 잘 못 알아들었어요.", CHECKING]
    assert (r.state, r.approval_withdrawn, r.readback) == (State.CONFIRM, None, False)
    assert engine.owner_decided(OwnerDecision(1, "accept"), NOW).utterances == [CLOSING]
