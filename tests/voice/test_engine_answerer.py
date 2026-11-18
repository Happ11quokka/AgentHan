"""엔진이 손님 질문을 답변기에 넘긴다 (5단계 티켓 11). 실기기 통화에서 틀린 질문들."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.answerer import AnswerReply, AnswerRequest
from agenthan.voice.dialog.engine import DialogEngine
from agenthan.voice.dialog.knowledge import KnowledgeKey, KnowledgeStore
from agenthan.voice.dialog.states import State
from agenthan.voice.nlu.base import Act, NluContext
from agenthan.voice.store_config import KST, StoreConfig

NOW = datetime(2026, 11, 11, 16, 0, tzinfo=KST)
ASK_ORDER = "어떤 걸로 드릴까요?"
SORRY = "죄송해요, 그건 제가 정확히 모르겠어요. 사장님께 전해 둘게요."


class ScriptedNlu:
    def __init__(self, script: dict[str, Sequence[Act]]) -> None:
        self._script = script

    def parse(self, text: str, ctx: NluContext) -> list[Act]:
        return list(self._script.get(text, [Act("unknown", free_text=text)]))


class ScriptedAnswerer:
    def __init__(self, script: dict[str, AnswerReply]) -> None:
        self._script = script
        self.requests: list[AnswerRequest] = []

    def answer(self, request: AnswerRequest) -> AnswerReply:
        self.requests.append(request)
        return self._script.get(request.text, AnswerReply("not_question"))


class BrokenAnswerer:
    def answer(self, request: AnswerRequest) -> AnswerReply:
        raise RuntimeError("boom")


def _ask(text: str, menu: str = "") -> list[Act]:
    return [Act("ask_menu_info", menu_text=menu, menu_guess=menu, free_text=text)]


SWEET = "아메도빅 구매를 할 건데 메도비 많이 타나요."
WHOLE = "올 케이크면 달다는 거야. 아닌 거야."
NUTS = "불린의결과류 들어가"
NLU = {
    SWEET: _ask(SWEET, "메도빅"),
    WHOLE: _ask(WHOLE, "메도빅"),
    "몇 시까지 해요?": [Act("ask_other", free_text="몇 시까지 해요?")],
    "주차 돼요?": [Act("ask_other", free_text="주차 돼요?")],
    "메도빅 얼마예요?": _ask("메도빅 얼마예요?", "메도빅"),
    "메도빅 며칠 가요?": _ask("메도빅 며칠 가요?", "메도빅"),
    "메도빅 하나요": [Act("add_item", menu_text="메도빅", menu_guess="메도빅", qty=1)],
    "이거 양 많아요?": [Act("ask_menu_info", free_text="이거 양 많아요?")],
    "추천 좀 해줘": [Act("set_request", free_text="추천 좀 해줘")],
    "뭐 좋은 거 없나": [Act("set_option", option_text="좋은 거")],
    "홀케이크로 줘": [Act("set_option", option_text="홀케이크")],
    "그럼 많이 달아요?": [Act("ask_menu_info", free_text="그럼 많이 달아요?")],
    "블린 하나요": [Act("add_item", menu_text="블린", menu_guess="블린", qty=1)],
}
SWEET_SAY = "손님들은 너무 달지 않고 촉촉하다고 하세요."
ANSWERS = {
    SWEET: AnswerReply("answer", SWEET_SAY, "메도빅이 너무 달지 않고 촉촉해요", "메도빅"),
    WHOLE: AnswerReply("answer", SWEET_SAY, "메도빅이 너무 달지 않고 촉촉해요", "메도빅"),
    NUTS: AnswerReply("unsure", menu="블린"),
    "블린에는 견과류가 들어가나요": AnswerReply("answer", "블린에는 견과류가 안 들어가요.",
                                     "얇은 러시아식 팬케이크", "블린"),
    "몇 시까지 해요?": AnswerReply("answer", "오후 9시까지 해요.", "오후 9시까지"),
    "주차 돼요?": AnswerReply("unsure"),
    "추천 좀 해줘": AnswerReply("answer", "메도빅, 나폴레옹, 아메리카노, 블린 있어요."),
    "뭐 좋은 거 없나": AnswerReply("answer", "메도빅, 나폴레옹, 아메리카노, 블린 있어요."),
    "그럼 많이 달아요?": AnswerReply("unsure"),
    "이거 양 많아요?": AnswerReply("answer", "손님들은 양이 좀 적다고 하세요.",
                              "블린 양이 생각보다 적었지만", "블린"),
    "메도빅 얼마예요?": AnswerReply("unsure", menu="메도빅"),
    "메도빅 며칠 가요?": AnswerReply("unsure", menu="메도빅"),
}


def _engine(answerer: object = None, *, knowledge: KnowledgeStore | None = None,
            with_answerer: bool = True) -> tuple[DialogEngine, ScriptedAnswerer]:
    scripted = ScriptedAnswerer(ANSWERS)
    engine = DialogEngine(
        FakeStoreKnowledge.from_fixture(), ScriptedNlu(NLU), StoreConfig.from_fixture(),
        order_id="o-1", knowledge=knowledge,
        answerer=(answerer or scripted) if with_answerer else None)  # type: ignore[arg-type]
    engine.start(NOW)
    return engine, scripted


def test_a_misheard_taste_question_is_answered_from_the_store_facts() -> None:
    engine, answerer = _engine()
    result = engine.handle(SWEET, NOW)
    assert result.utterances == [SWEET_SAY, ASK_ORDER]
    assert result.state is State.ORDERING
    (request,) = answerer.requests  # NLU와 같이 미리 부른 한 번뿐이다
    assert any("손님 후기: 메도빅이 너무 달지 않고 촉촉해요." in line for line in request.facts)


def test_a_question_without_a_menu_name_is_about_the_menu_just_ordered() -> None:
    engine, answerer = _engine()
    engine.handle("블린 하나요", NOW)
    result = engine.handle("이거 양 많아요?", NOW)
    assert result.utterances == ["손님들은 양이 좀 적다고 하세요.", "더 필요하신 거 있으세요?"]
    (request,) = answerer.requests
    assert request.focus == "블린"


def test_a_follow_up_question_is_not_answered_with_the_option_price() -> None:
    """예전 규칙은 "홀케이크"만 보고 "홀케이크로도 돼요. 38,000원 더 받아요"라고 했다."""
    engine, answerer = _engine()
    engine.handle(SWEET, NOW)
    result = engine.handle(WHOLE, NOW)
    assert result.utterances == [SWEET_SAY, ASK_ORDER]
    assert answerer.requests[-1].recent[-2:] == (f"AI: {SWEET_SAY}", f"AI: {ASK_ORDER}")


def test_a_question_the_nlu_missed_gets_an_apology_and_goes_to_the_owner() -> None:
    """ "불린의결과류 들어가": 예전에는 "잘 못 알아들었어요". 이제 질문으로 보고 사장님께 남긴다."""
    engine, _ = _engine(knowledge=KnowledgeStore())
    result = engine.handle(NUTS, NOW)
    assert result.utterances == [SORRY, ASK_ORDER]
    (request,) = result.knowledge_requests
    assert (request.key.menu_id, request.menu_name, request.text) == ("m04", "블린", NUTS)
    assert result.answer is not None and result.answer.kind == "unsure"


def test_an_ungrounded_no_from_the_model_is_never_spoken() -> None:
    engine, _ = _engine(knowledge=KnowledgeStore())
    result = engine.handle("블린에는 견과류가 들어가나요", NOW)
    assert result.utterances == [SORRY, ASK_ORDER]
    assert result.answer is not None and result.answer.reason == "denies"


def test_a_store_question_is_answered_and_an_unknown_one_goes_to_the_owner() -> None:
    engine, _ = _engine(knowledge=KnowledgeStore())
    assert engine.handle("몇 시까지 해요?", NOW).utterances == ["오후 9시까지 해요.", ASK_ORDER]
    result = engine.handle("주차 돼요?", NOW)
    assert result.utterances == [SORRY, ASK_ORDER]
    (request,) = result.knowledge_requests
    assert (request.key, request.menu_name) == (KnowledgeKey("", "other", "주차돼요"), "가게")


def test_the_owner_answer_to_a_store_question_reaches_the_next_caller() -> None:
    knowledge = KnowledgeStore()
    knowledge.save_note(KnowledgeKey("", "other", "주차돼요"), "가게 앞에 두 대 댈 수 있어요",
                        question="주차 돼요?")
    engine, answerer = _engine(knowledge=knowledge)
    engine.handle("주차 돼요?", NOW)
    assert ('사장님 메모: 손님 질문 "주차 돼요?"에 사장님 답: 가게 앞에 두 대 댈 수 있어요'
            in answerer.requests[0].facts[0])


def test_when_the_answerer_is_unsure_exact_fields_still_answer() -> None:
    engine, _ = _engine()
    assert engine.handle("메도빅 얼마예요?", NOW).utterances == ["메도빅은 7,500원이에요.",
                                                           ASK_ORDER]
    result = engine.handle("메도빅 며칠 가요?", NOW)
    assert result.utterances == ["그건 제가 정확히 모르겠어요.", ASK_ORDER]


def test_an_option_that_no_menu_has_is_not_an_option() -> None:
    """NLU가 "추천 좀 해줘"를 요청·옵션으로 냈다. 예전에는 "어떤 메뉴에 해 드릴까요?"."""
    engine, _ = _engine()
    result = engine.handle("추천 좀 해줘", NOW)
    assert result.utterances == ["메도빅, 나폴레옹, 아메리카노, 블린 있어요.", ASK_ORDER]
    assert engine.handle("뭐 좋은 거 없나", NOW).utterances == result.utterances
    assert engine.handle("홀케이크로 줘", NOW).utterances[0].startswith("메도빅 홀케이크로 하나요.")


def test_the_menu_last_asked_about_is_the_topic_of_the_next_question() -> None:
    engine, answerer = _engine()
    engine.handle("메도빅 며칠 가요?", NOW)
    engine.handle("그럼 많이 달아요?", NOW)
    assert answerer.requests[-1].focus == "메도빅"


def test_words_that_are_not_a_question_stay_not_understood() -> None:
    engine, answerer = _engine()
    result = engine.handle("어 그 뭐냐 그거 있잖아", NOW)
    assert result.utterances == ["죄송해요, 제가 잘 못 알아들었어요.", ASK_ORDER]
    assert len(answerer.requests) == 1
    engine.handle("메도빅 하나요", NOW)
    engine.handle("음 그", NOW)  # 짧은 말은 답변기에 안 보낸다
    assert len(answerer.requests) == 1


def test_a_broken_answerer_falls_back_to_the_field_rules() -> None:
    engine, _ = _engine(BrokenAnswerer())
    assert engine.handle("메도빅 얼마예요?", NOW).utterances == ["메도빅은 7,500원이에요.",
                                                           ASK_ORDER]


def test_without_an_answerer_the_engine_answers_as_before() -> None:
    engine, _ = _engine(with_answerer=False)
    result = engine.handle(WHOLE, NOW)
    assert result.utterances[0].startswith("네, 메도빅은 홀케이크로도 돼요.")
    assert result.answer is None
