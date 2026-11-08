"""티켓 05: 규칙 단계·정정·메뉴 질문·실패 카운터·FALLBACK·취소 (엔진)."""

from datetime import datetime

import pytest

from agenthan.core.contracts import MenuItem, OrderLine
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog import templates
from agenthan.voice.dialog.engine import DialogEngine, TurnResult
from agenthan.voice.dialog.states import State
from agenthan.voice.nlu.base import Act, NluContext
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.store_config import KST, StoreConfig

NOW = datetime(2026, 11, 8, 16, 0, tzinfo=KST)
TEMP_Q = "아메리카노는 따뜻하게 드릴까요, 차갑게 드릴까요?"
MORE_Q = "더 필요하신 거 있으세요?"
ORDER_Q = "어떤 걸로 드릴까요?"
SORRY = "죄송해요, 잘 못 알아들었어요."
HANDOFF = "사장님께 메모 남겨 드릴게요."
MEDOVIK_DESC = "메도빅은 꿀을 넣은 얇은 시트를 여러 겹 쌓은 러시아 꿀 케이크예요."


def make_engine(menu: list[MenuItem] | None = None, nlu: object | None = None) -> DialogEngine:
    store = FakeStoreKnowledge(menu) if menu is not None else FakeStoreKnowledge.from_fixture()
    engine = DialogEngine(
        store,
        nlu or FakeNlu(),  # type: ignore[arg-type]
        StoreConfig.from_fixture(),
        order_id="o-test",
    )
    engine.start(NOW)
    return engine


@pytest.fixture
def engine() -> DialogEngine:
    return make_engine()


def talk(engine: DialogEngine, *texts: str) -> TurnResult:
    result = None
    for text in texts:
        result = engine.handle(text, NOW)
    assert result is not None
    return result


def lines(r: TurnResult) -> list[tuple[str, int]]:
    return [(line.item.id, line.qty) for line in r.cart.lines]


def to_confirm(engine: DialogEngine, *items: str) -> TurnResult:
    """항목을 담고 6시 반 픽업으로 최종 복창까지 간다."""
    r = talk(engine, *items, "그게 다예요", "6시 반이요")
    assert r.state is State.CONFIRM
    return r


class ScriptedNlu:
    """정해 둔 발화만 act를 직접 주고, 나머지는 FakeNlu."""

    def __init__(self, script: dict[str, list[Act]]) -> None:
        self._script = script

    def parse(self, text: str, ctx: NluContext) -> list[Act]:
        return list(self._script.get(text, FakeNlu().parse(text, ctx)))


# ---------------------------------------------------------------- 규칙 단계


class CountingNlu:
    """NLU를 몇 번 불렀는지 센다. 규칙 단계가 걸리면 부르지 않아야 한다."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def parse(self, text: str, ctx: NluContext) -> list[Act]:
        self.calls.append(text)
        return FakeNlu().parse(text, ctx)


def test_rule_step_skips_nlu() -> None:
    nlu = CountingNlu()
    engine = make_engine(nlu=nlu)
    talk(engine, "메도빅 하나요", "그게 다예요", "6시 반이요")
    r = talk(engine, "네 맞아요")
    assert r.state is State.CLOSING
    assert nlu.calls == ["메도빅 하나요", "그게 다예요", "6시 반이요"]


def test_no_with_content_goes_to_nlu() -> None:
    nlu = CountingNlu()
    engine = make_engine(nlu=nlu)
    talk(engine, "메도빅 하나요", "그게 다예요", "6시 반이요", "아니요, 두 개요")
    assert nlu.calls[-1] == "아니요, 두 개요"


# ---------------------------------------------------------------- 정정 (CONFIRM)


def test_correction_reads_back_changed_item_then_full_readback(engine: DialogEngine) -> None:
    r = to_confirm(engine, "메도빅 하나요")
    assert r.utterances == ["메도빅 하나, 6시 반 픽업, 총 7,500원이에요. 맞으세요?"]

    r = talk(engine, "아니요, 두 개요")
    assert r.state is State.CONFIRM
    assert r.order is None
    assert r.utterances == [
        "메도빅 두 개로 바꿨어요.",
        "그럼 메도빅 두 개, 6시 반 픽업, 총 15,000원이에요. 맞으세요?",
    ]

    r = talk(engine, "네")
    assert r.state is State.CLOSING
    assert r.order is not None
    assert r.order.status == "confirmed"
    assert r.order.lines == [OrderLine(menu_id="m01", quantity=2)]


def test_correction_names_menu_among_several(engine: DialogEngine) -> None:
    to_confirm(engine, "메도빅 하나랑 아메리카노 하나요", "차갑게요")
    r = talk(engine, "아니요, 아메리카노 두 잔이요")
    assert lines(r) == [("m01", 1), ("m03", 2)]
    assert r.utterances == [
        "아메리카노 차갑게 두 잔으로 바꿨어요.",
        "그럼 메도빅 하나, 아메리카노 차갑게 두 잔, 6시 반 픽업, 총 16,500원이에요. 맞으세요?",
    ]


def test_correction_remove_item(engine: DialogEngine) -> None:
    to_confirm(engine, "메도빅 하나랑 블린 하나요")
    r = talk(engine, "블린 빼 주세요")
    assert lines(r) == [("m01", 1)]
    assert r.utterances == [
        "블린 뺐어요.",
        "그럼 메도빅 하나, 6시 반 픽업, 총 7,500원이에요. 맞으세요?",
    ]


def test_correction_removing_everything_goes_back_to_ordering(engine: DialogEngine) -> None:
    to_confirm(engine, "메도빅 하나요")
    r = talk(engine, "메도빅 빼 주세요")
    assert r.state is State.ORDERING
    assert r.utterances == ["메도빅 뺐어요.", ORDER_Q]


def test_correction_adding_menu_with_required_option(engine: DialogEngine) -> None:
    to_confirm(engine, "메도빅 하나요")
    r = talk(engine, "아메리카노도 하나 주세요")
    assert r.state is State.CLARIFY
    assert r.utterances == ["아메리카노 한 잔이요.", TEMP_Q]  # 새로 담은 건 "바꿨어요"가 아니다
    r = talk(engine, "따뜻하게요")
    assert r.state is State.CONFIRM
    assert r.utterances[-1] == (
        "메도빅 하나, 아메리카노 따뜻하게 한 잔, 6시 반 픽업, 총 12,000원이에요. 맞으세요?"
    )


def test_correction_pickup_time(engine: DialogEngine) -> None:
    to_confirm(engine, "메도빅 하나요")
    r = talk(engine, "아니요, 7시요")
    assert r.state is State.CONFIRM
    assert r.utterances == [
        "픽업 시간 7시로 바꿨어요.",
        "그럼 메도빅 하나, 7시 픽업, 총 7,500원이에요. 맞으세요?",
    ]
    order = talk(engine, "네").order
    assert order is not None
    assert order.pickup_time == "2026-11-08T19:00:00+09:00"


def test_correction_pickup_too_early_offers_time(engine: DialogEngine) -> None:
    to_confirm(engine, "메도빅 하나요")
    r = talk(engine, "아니요, 4시 5분이요")
    assert r.state is State.ASK_PICKUP
    assert r.utterances == ["준비에 15분 정도 걸려요. 4시 15분 괜찮으세요?"]
    r = talk(engine, "네")
    assert r.state is State.CONFIRM
    assert "4시 15분 픽업" in r.utterances[-1]


def test_plain_no_then_fix_reads_back_again(engine: DialogEngine) -> None:
    to_confirm(engine, "메도빅 하나요")
    r = talk(engine, "아니요")
    assert r.state is State.ORDERING
    assert r.utterances == ["어떤 걸 고쳐 드릴까요?"]
    r = talk(engine, "두 개로 해 주세요")
    assert r.state is State.CONFIRM
    assert r.utterances == [
        "메도빅 두 개로 바꿨어요.",
        "그럼 메도빅 두 개, 6시 반 픽업, 총 15,000원이에요. 맞으세요?",
    ]


def test_plain_no_then_new_time(engine: DialogEngine) -> None:
    to_confirm(engine, "메도빅 하나요")
    talk(engine, "아니요")
    r = talk(engine, "7시로 해 주세요")
    assert r.state is State.CONFIRM
    assert r.utterances == [
        "픽업 시간 7시로 바꿨어요.",
        "그럼 메도빅 하나, 7시 픽업, 총 7,500원이에요. 맞으세요?",
    ]


def test_unreadable_correction_reasks_readback(engine: DialogEngine) -> None:
    readback = to_confirm(engine, "메도빅 하나요").utterances[0]
    r = talk(engine, "음")
    assert r.state is State.CONFIRM
    assert r.utterances == [SORRY, readback]


# ---------------------------------------------------------------- 요청사항 복창 (03에서 넘어온 것)


def test_final_readback_includes_requests(engine: DialogEngine) -> None:
    r = to_confirm(engine, "블린 하나요", "블린은 소스 따로 주세요")
    assert r.utterances == [
        "블린 하나, 6시 반 픽업, 총 9,000원이에요. "
        "블린에 소스 따로 주세요라고 적어 뒀어요. 맞으세요?"
    ]


# ---------------------------------------------------------------- 주문 중 시각만 말한 턴 (04)


def test_time_only_turn_gets_reply(engine: DialogEngine) -> None:
    talk(engine, "메도빅 하나요")
    r = talk(engine, "6시에 갈게요")
    assert r.state is State.ORDERING
    assert r.utterances == ["6시 픽업으로 알아 둘게요.", MORE_Q]


def test_time_only_turn_with_empty_cart(engine: DialogEngine) -> None:
    r = talk(engine, "6시에 갈게요")
    assert r.utterances == ["6시 픽업으로 알아 둘게요.", ORDER_Q]


def test_time_needing_confirmation_is_kept_quietly(engine: DialogEngine) -> None:
    talk(engine, "메도빅 하나요")
    r = talk(engine, "4시 5분에 갈게요")  # 준비 시간보다 이르다. 주문을 마칠 때 다시 묻는다
    assert r.utterances == [MORE_Q]
    r = talk(engine, "그게 다예요")
    assert r.utterances == ["준비에 15분 정도 걸려요. 4시 15분 괜찮으세요?"]


def test_item_and_time_in_one_turn(engine: DialogEngine) -> None:
    r = talk(engine, "메도빅 하나, 6시에 갈게요")
    assert r.utterances == ["메도빅 하나요.", "6시 픽업으로 알아 둘게요.", MORE_Q]


# ---------------------------------------------------------------- 메뉴 질문


def test_menu_question_answers_from_description_and_keeps_state(engine: DialogEngine) -> None:
    r = talk(engine, "메도빅 많이 달아요?")
    assert r.state is State.ORDERING
    assert r.cart.is_empty()
    assert r.utterances == [MEDOVIK_DESC, ORDER_Q]


def test_ungrounded_question_says_dont_know(engine: DialogEngine) -> None:
    talk(engine, "메도빅 하나요")
    r = talk(engine, "글루텐 프리예요?")  # 메뉴 이름이 없으면 방금 담은 메뉴를 묻는 것으로 본다
    assert r.utterances == ["그건 제가 정확히 모르겠어요.", MORE_Q]
    assert lines(r) == [("m01", 1)]


def test_menu_question_during_option_question(engine: DialogEngine) -> None:
    talk(engine, "아메리카노 하나요")
    r = talk(engine, "아메리카노 디카페인 돼요?")
    assert r.state is State.CLARIFY
    assert r.utterances == ["네, 아메리카노는 디카페인 변경 할 수 있어요. 500원 추가예요.", TEMP_Q]
    r = talk(engine, "차갑게요")
    assert r.state is State.ORDERING


def test_menu_question_at_confirm_reasks_readback(engine: DialogEngine) -> None:
    readback = to_confirm(engine, "메도빅 하나요").utterances[0]
    r = talk(engine, "나폴레옹 얼마예요?")
    assert r.state is State.CONFIRM
    assert r.utterances == ["나폴레옹은 7,000원이에요.", readback]
    assert talk(engine, "네").state is State.CLOSING


def test_question_about_unknown_menu(engine: DialogEngine) -> None:
    nlu = ScriptedNlu({"짜장면 맛있어요?": [Act("ask_menu_info", menu_text="짜장면")]})
    engine = make_engine(nlu=nlu)
    r = talk(engine, "짜장면 맛있어요?")
    assert r.utterances == [templates.not_on_menu(["메도빅", "나폴레옹", "아메리카노", "블린"]),
                            ORDER_Q]


def test_ask_other_says_dont_know() -> None:
    engine = make_engine(nlu=ScriptedNlu({"몇 시까지 해요?": [Act("ask_other")]}))
    r = talk(engine, "몇 시까지 해요?")
    assert r.utterances == ["그건 제가 정확히 모르겠어요.", ORDER_Q]


def test_menu_question_does_not_count_as_failure(engine: DialogEngine) -> None:
    for _ in range(4):
        r = talk(engine, "글루텐 프리예요?")
    assert r.state is State.ORDERING


# ---------------------------------------------------------------- 실패 카운터 → FALLBACK


def test_three_failures_on_same_question_hand_off(engine: DialogEngine) -> None:
    talk(engine, "메도빅 하나요", "그게 다예요")
    r = talk(engine, "음")
    assert r.utterances == [SORRY, "몇 시쯤 오세요?"]
    r = talk(engine, "어")
    assert r.state is State.ASK_PICKUP
    r = talk(engine, "글쎄")
    assert r.state is State.FALLBACK
    assert r.utterances == ["죄송해요, 제가 잘 못 알아듣고 있어요.", HANDOFF]
    assert r.order is not None
    assert r.order.status == "handoff"
    assert r.order.lines == [OrderLine(menu_id="m01", quantity=1)]
    assert r.order.transcript[-3:] == [
        "손님: 글쎄",
        "AI: 죄송해요, 제가 잘 못 알아듣고 있어요.",
        f"AI: {HANDOFF}",
    ]
    assert talk(engine, "여보세요").state is State.DONE


def test_unreadable_times_count_with_unknowns(engine: DialogEngine) -> None:
    talk(engine, "메도빅 하나요", "그게 다예요")
    talk(engine, "이따 갈게요")  # 시각을 못 읽음 (거절)
    talk(engine, "음")
    r = talk(engine, "이따가요")
    assert r.state is State.FALLBACK


def test_success_resets_failure_count(engine: DialogEngine) -> None:
    talk(engine, "음", "어")
    talk(engine, "메도빅 하나요")
    r = talk(engine, "음")
    assert r.state is State.ORDERING
    r = talk(engine, "어")
    assert r.state is State.ORDERING


def test_unknown_menu_three_times_hands_off(engine: DialogEngine) -> None:
    talk(engine, "짜장면 하나요", "짬뽕 하나요")
    r = talk(engine, "탕수육 하나요")
    assert r.state is State.FALLBACK
    assert r.order is not None
    assert r.order.lines == []


def test_yes_to_choose_question_counts_as_failure() -> None:
    cakes = [
        MenuItem("c1", "치즈 케이크", 7000, category="케이크"),
        MenuItem("c2", "초코 케이크", 7500, category="케이크"),
    ]
    engine = make_engine(cakes)
    talk(engine, "초즈 케이크 하나요")
    talk(engine, "네", "네")
    r = talk(engine, "네")
    assert r.state is State.FALLBACK


def test_failures_on_different_questions_do_not_add_up(engine: DialogEngine) -> None:
    talk(engine, "메도빅 하나요", "음", "어")  # ORDERING 질문에 두 번
    talk(engine, "그게 다예요")  # 성공 (픽업 질문으로)
    r = talk(engine, "음")
    assert r.state is State.ASK_PICKUP


# ---------------------------------------------------------------- 사장님 연결


@pytest.mark.parametrize("state_turns", [[], ["메도빅 하나요"], ["아메리카노 하나요"]])
def test_want_owner_hands_off_from_any_state(state_turns: list[str]) -> None:
    engine = make_engine()
    for text in state_turns:
        engine.handle(text, NOW)
    r = talk(engine, "사장님 바꿔 주세요")
    assert r.state is State.FALLBACK
    assert r.utterances == [HANDOFF]
    assert r.order is not None
    assert r.order.status == "handoff"
    assert r.order.transcript[-2:] == ["손님: 사장님 바꿔 주세요", f"AI: {HANDOFF}"]


def test_want_owner_at_confirm(engine: DialogEngine) -> None:
    to_confirm(engine, "메도빅 하나요")
    r = talk(engine, "사람이랑 통화할게요")
    assert r.state is State.FALLBACK
    assert r.order is not None
    assert r.order.pickup_time == "2026-11-08T18:30:00+09:00"


# ---------------------------------------------------------------- 취소


def test_cancel_confirmed_ends_without_order(engine: DialogEngine) -> None:
    talk(engine, "메도빅 하나요")
    r = talk(engine, "주문 취소할게요")
    assert r.state is State.ORDERING
    assert r.utterances == ["주문 취소할까요?"]
    r = talk(engine, "네")
    assert r.state is State.DONE
    assert r.order is None
    assert r.utterances == ["네, 주문 취소했어요. 감사합니다."]
    assert talk(engine, "여보세요").utterances == []


def test_cancel_declined_resumes_last_question(engine: DialogEngine) -> None:
    talk(engine, "아메리카노 하나요")
    r = talk(engine, "취소할게요")  # "~ 취소"지만 메뉴가 없으니 주문 취소로 읽는다
    assert r.utterances == ["주문 취소할까요?"]
    assert lines(r) == [("m03", 1)]
    r = talk(engine, "아니요")
    assert r.state is State.CLARIFY
    assert r.utterances == ["네, 계속 도와드릴게요.", TEMP_Q]
    r = talk(engine, "차갑게요")
    assert r.state is State.ORDERING


def test_cancel_at_confirm_is_not_confirmed(engine: DialogEngine) -> None:
    to_confirm(engine, "메도빅 하나요")
    talk(engine, "주문 취소해 주세요")
    r = talk(engine, "네")
    assert r.state is State.DONE
    assert r.order is None


def test_ordering_after_cancel_question_continues(engine: DialogEngine) -> None:
    talk(engine, "메도빅 하나요", "주문 취소할게요")
    r = talk(engine, "블린 하나요")
    assert r.state is State.ORDERING
    assert lines(r) == [("m01", 1), ("m04", 1)]
    assert r.utterances == [f"블린 하나요. {MORE_Q}"]


def test_menu_cancel_is_remove_not_order_cancel(engine: DialogEngine) -> None:
    talk(engine, "메도빅 하나랑 블린 하나요")
    r = talk(engine, "블린 취소할게요")
    assert lines(r) == [("m01", 1)]
    assert r.utterances == ["블린 뺐어요.", MORE_Q]


# ---------------------------------------------------------------- 다시 말해 주세요


def test_repeat_please_reasks_last_question(engine: DialogEngine) -> None:
    talk(engine, "아메리카노 하나요")
    r = talk(engine, "다시 말해 주세요")
    assert r.state is State.CLARIFY
    assert r.utterances == [TEMP_Q]


# ---------------------------------------------------------------- transcript


def test_transcript_alternates_customer_and_ai(engine: DialogEngine) -> None:
    to_confirm(engine, "메도빅 하나요")
    talk(engine, "아니요, 두 개요")
    order = talk(engine, "네").order
    assert order is not None
    assert order.transcript == [
        "AI: 네, 샘플 러시안 베이커리입니다. 주문 도와드릴게요.",
        "손님: 메도빅 하나요",
        f"AI: 메도빅 하나요. {MORE_Q}",
        "손님: 그게 다예요",
        "AI: 몇 시쯤 오세요?",
        "손님: 6시 반이요",
        "AI: 메도빅 하나, 6시 반 픽업, 총 7,500원이에요. 맞으세요?",
        "손님: 아니요, 두 개요",
        "AI: 메도빅 두 개로 바꿨어요.",
        "AI: 그럼 메도빅 두 개, 6시 반 픽업, 총 15,000원이에요. 맞으세요?",
        "손님: 네",
        "AI: 6시 반에 준비해 둘게요. 감사합니다.",
    ]
