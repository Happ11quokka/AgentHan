"""4단계 티켓 13: 모르는 메뉴 질문 → 사장님 보충 → 다음 손님부터 답 (엔진 이음매).

엔진은 보충 지식 저장소를 읽기 포트(`KnowledgeReader`)로만 본다. 사장님께 바로 묻지 않는 근거 없는
메뉴 질문에는 Q17 문장을 말하고 그 질문을 턴 결과에 "보충 요청"으로 싣는다(쓰는 쪽은 허브).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.engine import DialogEngine, TurnResult
from agenthan.voice.dialog.knowledge import KnowledgeKey, KnowledgeRequest, KnowledgeStore
from agenthan.voice.nlu.base import Act
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.store_config import KST, StoreConfig

NOW = datetime(2026, 11, 10, 16, 0, tzinfo=KST)
PASS_ON = "죄송해요, 그건 제가 정확히 모르겠어요. 사장님께 전해 둘게요."
DONT_KNOW = "그건 제가 정확히 모르겠어요."
HOLD_ON = "잠시만요, 사장님께 여쭤볼게요."
ORDER_Q = "어떤 걸로 드릴까요?"
MORE_Q = "더 필요하신 거 있으세요?"
TASTE = "아메리카노 어떤 맛이에요?"
NUTS = "메도빅에 견과류 들어가요?"
CINNAMON = "블린에 시나몬 들어가요?"


class _OtherNlu(FakeNlu):
    def parse(self, text, context):  # type: ignore[no-untyped-def]
        if text == "주차 돼요?":
            return [Act("ask_other", free_text=text)]
        return super().parse(text, context)


def make_engine(store: KnowledgeStore | None = None, *, ask_owner: bool = True,
                knowledge: bool = True) -> DialogEngine:
    engine = DialogEngine(
        FakeStoreKnowledge.from_fixture(), _OtherNlu(), StoreConfig.from_fixture(),
        order_id="o-test", ask_owner=ask_owner,
        knowledge=(store if store is not None else KnowledgeStore()) if knowledge else None,
    )
    engine.start(NOW)
    return engine


def talk(engine: DialogEngine, *texts: str) -> TurnResult:
    result = None
    for text in texts:
        result = engine.handle(text, NOW)
    assert result is not None
    return result


# ---------------------------------------------------------------- 보충 요청 (Q17)


def test_an_ungrounded_taste_question_is_passed_on_to_the_owner() -> None:
    r = talk(make_engine(), TASTE)
    assert r.utterances == [PASS_ON, ORDER_Q]
    assert r.knowledge_requests == (
        KnowledgeRequest(KnowledgeKey("m03", "taste"), menu_name="아메리카노", text=TASTE),
    )
    assert r.owner_question is None


def test_a_store_question_is_not_passed_on() -> None:
    r = talk(make_engine(), "주차 돼요?")  # 가게 정보 질문(ask_other)은 대상이 아니다
    assert r.utterances == [DONT_KNOW, ORDER_Q]
    assert r.knowledge_requests == ()


def test_without_the_knowledge_loop_the_first_stage_line_stays() -> None:
    r = talk(make_engine(knowledge=False), TASTE)
    assert r.utterances == [DONT_KNOW, ORDER_Q]
    assert r.knowledge_requests == ()


def test_a_question_the_owner_can_answer_by_button_still_opens_a_question_card() -> None:
    r = talk(make_engine(), NUTS)
    assert r.utterances == [HOLD_ON]
    assert r.knowledge_requests == ()
    assert r.owner_question is not None
    assert (r.owner_question.kind, r.owner_question.subject, r.owner_question.yes_no) == (
        "allergen", "견과", True)


def test_a_blocked_owner_question_is_passed_on_instead() -> None:
    engine = make_engine()
    asked = talk(engine, NUTS).owner_question
    assert asked is not None
    engine.owner_answered(asked.number, "timeout", NOW)  # 시간 초과 뒤로는 질문을 열지 않는다
    r = talk(engine, CINNAMON)
    assert r.owner_question is None
    assert r.utterances == [PASS_ON, ORDER_Q]
    assert r.knowledge_requests == (
        KnowledgeRequest(KnowledgeKey("m04", "ingredient", "시나몬", yes_no=True),
                         menu_name="블린",
                         text=CINNAMON),
    )


# ---------------------------------------------------------------- 저장 뒤: 보충 지식으로 답


def test_a_saved_owner_note_answers_the_same_kind_of_question_in_a_quote() -> None:
    store = KnowledgeStore()
    store.save_note(KnowledgeKey("m03", "taste"), "산미가 적고 고소한 편이에요")
    engine = make_engine(store)
    r = talk(engine, "아메리카노 달아요?")  # 같은 메뉴, 같은 종류(맛·설명)
    assert r.utterances == ["사장님 말씀으로는 산미가 적고 고소한 편이에요.", ORDER_Q]
    assert r.knowledge_requests == ()
    assert r.owner_question is None


def test_a_note_for_one_menu_does_not_answer_another() -> None:
    store = KnowledgeStore()
    store.save_note(KnowledgeKey("m03", "taste"), "고소해요.")
    r = talk(make_engine(store), "아메리카노 재료가 뭐예요?")  # 같은 메뉴, 다른 종류
    assert r.utterances == [HOLD_ON]


@pytest.mark.parametrize(("contains", "say"), [
    (True, "사장님 확인으로는 메도빅에 견과가 들어간대요."),
    (False, "사장님 확인으로는 메도빅에 견과는 안 들어간대요."),
])
def test_an_owner_check_answers_the_next_caller_without_asking(contains: bool, say: str) -> None:
    store = KnowledgeStore()
    store.save_check(KnowledgeKey("m01", "allergen", "견과", yes_no=True), contains)
    r = talk(make_engine(store), NUTS)
    assert r.owner_question is None
    assert r.utterances == [say, ORDER_Q]


def test_an_owner_check_on_a_named_ingredient_is_reused() -> None:
    store = KnowledgeStore()
    store.save_check(KnowledgeKey("m04", "ingredient", "시나몬", yes_no=True), False)
    r = talk(make_engine(store), "블린에 시나몬이 들어있어요?")
    assert r.utterances == ["사장님 확인으로는 블린에 시나몬은 안 들어간대요.", ORDER_Q]


def test_a_check_is_not_used_for_a_question_that_is_not_yes_no() -> None:
    store = KnowledgeStore()
    store.save_check(KnowledgeKey("m01", "allergen", "땅콩", yes_no=True), False)
    r = talk(make_engine(store), "메도빅 땅콩 알레르기 있는데 먹어도 돼요?")
    assert r.owner_question is not None  # 확인 대신 사장님께 다시 묻는다


def test_a_note_for_a_remove_question_does_not_answer_a_contains_question() -> None:
    # 리뷰 C4: "땅콩 빼 주실 수 있어요?"에 저장한 설명이 "땅콩 있어요?"의 답으로 나가면 안 된다
    # (알레르기 안전). 열쇠가 예/아니오 질문인지까지 가른다
    store = KnowledgeStore()
    store.save_note(KnowledgeKey("m01", "allergen", "땅콩"), "빼 드릴 수 있어요")
    r = talk(make_engine(store), "메도빅 땅콩 있어요?")
    assert r.owner_question is not None
    assert r.utterances == [HOLD_ON]
