"""4단계 티켓 05: 근거 없는 메뉴 질문 → 사장님께 묻기 (엔진 이음매, spec "ASK_OWNER: 엔진").

엔진 상태는 늘리지 않는다. 열린 사장님 질문 하나를 들고, 턴 결과에 질문을 싣는다.
사장님 답(돼요/안 돼요/모르겠어요)은 `owner_answered`로 들어와 고정 존댓말 문장 + 하던 질문.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.engine import DialogEngine, TurnResult
from agenthan.voice.dialog.owner_question import OwnerQuestion
from agenthan.voice.dialog.states import State
from agenthan.voice.nlu.base import Act
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.store_config import KST, StoreConfig

NOW = datetime(2026, 11, 8, 16, 0, tzinfo=KST)
HOLD_ON = "잠시만요, 사장님께 여쭤볼게요."
DONT_KNOW = "그건 제가 정확히 모르겠어요."
MORE_Q = "더 필요하신 거 있으세요?"
ORDER_Q = "어떤 걸로 드릴까요?"
NUTS = "메도빅에 견과류 들어가요?"


ONE_BREATH = "메도빅 하나요 그리고 메도빅에 견과류 들어가요?"


class _OtherNlu(FakeNlu):
    """가짜 NLU 규칙에 없는 말 둘: 가게 정보 질문(`ask_other`), 담기 + 메뉴 질문을 한 번에."""

    def parse(self, text, context):  # type: ignore[no-untyped-def]
        if text == "주차 돼요?":
            return [Act("ask_other", free_text=text)]
        if text == ONE_BREATH:
            return [Act("add_item", menu_text="메도빅", qty=1),
                    Act("ask_menu_info", menu_text="메도빅", free_text=text)]
        return super().parse(text, context)


def make_engine(*, ask_owner: bool = True) -> DialogEngine:
    engine = DialogEngine(
        FakeStoreKnowledge.from_fixture(), _OtherNlu(), StoreConfig.from_fixture(),
        order_id="o-test", ask_owner=ask_owner,
    )
    engine.start(NOW)
    return engine


def talk(engine: DialogEngine, *texts: str) -> TurnResult:
    result = None
    for text in texts:
        result = engine.handle(text, NOW)
    assert result is not None
    return result


def test_an_ungrounded_menu_question_opens_an_owner_question() -> None:
    engine = make_engine()
    r = talk(engine, NUTS)
    assert r.utterances == [HOLD_ON]
    assert r.owner_question == OwnerQuestion(number=1, menu_id="m01", menu_name="메도빅",
                                             text=NUTS)
    assert r.state is State.ORDERING
    assert r.cart.is_empty()


# ---------------------------------------------------------------- 열림·안 열림 표


@pytest.mark.parametrize(("before", "question", "menu_id"), [
    ((), "메도빅에 견과류 들어가요?", "m01"),  # 목록에 없는 알레르기 성분
    ((), "아메리카노 알레르기 있어요?", "m03"),  # 빈 알레르기 목록
    ((), "아메리카노 재료가 뭐예요?", "m03"),  # 설명이 빈 메뉴의 재료 (06)
    ((), "블린에 시나몬 들어가요?", "m04"),  # 목록 밖 재료·가능 여부
    (("메도빅 하나요",), "글루텐 프리예요?", "m01"),  # 메뉴 이름 없이 방금 담은 메뉴, 그 밖의 질문
])
def test_ungrounded_menu_questions_open(before: tuple[str, ...], question: str,
                                        menu_id: str) -> None:
    engine = make_engine()
    talk(engine, *before) if before else None
    r = talk(engine, question)
    assert r.owner_question is not None
    assert (r.owner_question.menu_id, r.owner_question.text) == (menu_id, question)
    assert r.utterances[-1] == HOLD_ON


@pytest.mark.parametrize(("question", "say"), [
    ("메도빅 우유 들어가요?", "네, 메도빅에는 우유가 들어가요."),  # 목록에 있는 알레르기
    ("나폴레옹 얼마예요?", "나폴레옹은 7,000원이에요."),  # 가격
    ("아메리카노 디카페인 돼요?", "네, 아메리카노는 디카페인 변경 할 수 있어요. 500원 추가예요."),
    ("주차 돼요?", DONT_KNOW),  # 가게 정보 질문
    ("아메리카노 어떤 맛이에요?", DONT_KNOW),  # 설명이 빈 메뉴의 맛: 버튼 셋으로 답하기 어색 (06)
    ("아메리카노 달아요?", DONT_KNOW),
    ("글루텐 프리예요?", DONT_KNOW),  # 담은 메뉴가 없어 어느 메뉴인지 모름
])
def test_grounded_or_store_questions_do_not_open(question: str, say: str) -> None:
    r = talk(make_engine(), question)
    assert r.owner_question is None
    assert r.utterances[0] == say
    assert HOLD_ON not in r.utterances


def test_without_an_owner_side_an_ungrounded_question_stays_dont_know() -> None:
    r = talk(make_engine(ask_owner=False), NUTS)
    assert r.owner_question is None
    assert r.utterances == [DONT_KNOW, ORDER_Q]


def test_a_question_after_an_item_in_one_breath_keeps_the_item_and_holds() -> None:
    engine = make_engine()
    r = talk(engine, ONE_BREATH)
    assert [(line.item.id, line.qty) for line in r.cart.lines] == [("m01", 1)]
    assert r.utterances == ["메도빅 하나요.", HOLD_ON]
    assert r.owner_question is not None


# ---------------------------------------------------------------- 사장님 답 → 대사와 돌아감


@pytest.mark.parametrize(("answer", "say"), [
    ("yes", "사장님께 여쭤보니 네, 된다고 하세요."),
    ("no", "사장님께 여쭤보니 안 된다고 하세요."),
    ("unsure", "사장님께 여쭤봤는데 사장님도 정확히는 모르신대요."),
])
def test_each_owner_answer_has_a_fixed_line_then_the_question_comes_back(answer, say) -> None:
    engine = make_engine()
    talk(engine, "메도빅 하나요")
    asked = talk(engine, NUTS).owner_question
    assert asked is not None
    r = engine.owner_answered(asked.number, answer, NOW)
    assert r.utterances == [say, MORE_Q]
    assert r.owner_question is None
    assert r.state is State.ORDERING
    assert [(line.item.id, line.qty) for line in r.cart.lines] == [("m01", 1)]


def test_an_answer_with_an_empty_cart_goes_back_to_asking_the_order() -> None:
    engine = make_engine()
    asked = talk(engine, NUTS).owner_question
    assert asked is not None
    r = engine.owner_answered(asked.number, "yes", NOW)
    assert r.utterances[-1] == ORDER_Q


def test_an_answer_while_asking_the_pickup_time_asks_the_time_again() -> None:
    engine = make_engine()
    r = talk(engine, "메도빅 하나요", "그게 다예요")
    assert r.state is State.ASK_PICKUP
    pickup_q = r.utterances[-1]
    asked = talk(engine, NUTS).owner_question
    assert asked is not None
    r = engine.owner_answered(asked.number, "no", NOW)
    assert r.utterances[-1] == pickup_q
    assert r.state is State.ASK_PICKUP


def test_a_stale_or_repeated_answer_says_nothing() -> None:
    engine = make_engine()
    asked = talk(engine, NUTS).owner_question
    assert asked is not None
    assert engine.owner_answered(asked.number + 1, "yes", NOW).utterances == []
    assert engine.owner_answered(asked.number, "yes", NOW).utterances != []
    assert engine.owner_answered(asked.number, "yes", NOW).utterances == []  # 이미 답함


def test_questions_are_numbered_within_the_call() -> None:
    engine = make_engine()
    first = talk(engine, NUTS).owner_question
    assert first is not None
    engine.owner_answered(first.number, "yes", NOW)
    second = talk(engine, "블린에 시나몬 들어가요?").owner_question
    assert (first.number, second.number if second else None) == (1, 2)


def test_the_owner_answer_is_kept_in_the_order_transcript() -> None:
    engine = make_engine()
    asked = talk(engine, "메도빅 하나요", NUTS).owner_question
    assert asked is not None
    engine.owner_answered(asked.number, "yes", NOW)
    r = talk(engine, "그게 다예요", "6시 반이요", "네")
    assert r.order is not None
    assert "사장님: 돼요" in r.order.transcript
