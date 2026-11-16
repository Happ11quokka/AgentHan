"""6단계 품질: 되묻기 루프 방지 (엔진).

- 같은 질문의 두 번째 되묻기부터는 가게 데이터로 만든 보기를 붙인다
- 같은 슬롯 3번 실패(기존) 말고도, 통화 전체의 못 알아들음·되묻기가 `StoreConfig.max_reasks`번이면
  사장님께 넘긴다
- 이미 되물은 통화에서 손님이 짜증을 내면 바로 넘긴다
- 넘긴 이유는 `TurnResult.handoff`, 되물은 슬롯은 `TurnResult.reask`로 나간다(세션이 턴 기록에 남김)
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.engine import DialogEngine, TurnResult
from agenthan.voice.dialog.states import State
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.store_config import KST, StoreConfig

NOW = datetime(2026, 11, 8, 16, 0, tzinfo=KST)
SORRY = "죄송해요, 제가 잘 못 알아들었어요."
GIVE_UP = "죄송해요, 제가 잘 못 알아듣고 있어요."
HANDOFF = "사장님께 메모 남겨 드릴게요."
MORE_Q = "더 필요하신 거 있으세요?"


def make_engine(**config: object) -> DialogEngine:
    engine = DialogEngine(FakeStoreKnowledge.from_fixture(), FakeNlu(),
                          replace(StoreConfig.from_fixture(), **config), order_id="o-test")
    engine.start(NOW)
    return engine


def talk(engine: DialogEngine, *texts: str) -> TurnResult:
    result = None
    for text in texts:
        result = engine.handle(text, NOW)
    assert result is not None
    return result


# ------------------------------------------------ 두 번째 되묻기부터 보기


def test_second_reask_of_the_order_question_lists_menu_names_from_the_store() -> None:
    engine = make_engine()
    first = talk(engine, "음 그러니까")
    assert first.utterances == [SORRY, "어떤 걸로 드릴까요?"]
    second = talk(engine, "어 저기")
    assert second.utterances == [
        SORRY, "저희는 메도빅, 나폴레옹, 아메리카노 같은 메뉴가 있어요.", "어떤 걸로 드릴까요?"]
    assert second.state is State.ORDERING


def test_menu_examples_skip_sold_out_items() -> None:
    store = FakeStoreKnowledge.from_fixture()
    menu = [replace(m, sold_out=m.id == "m01") for m in store.list_menu()]
    engine = DialogEngine(FakeStoreKnowledge(menu), FakeNlu(), StoreConfig.from_fixture(),
                          order_id="o-test")
    engine.start(NOW)
    second = talk(engine, "음 그러니까", "어 저기")
    assert second.utterances[1] == "저희는 나폴레옹, 아메리카노, 블린 같은 메뉴가 있어요."


def test_second_reask_of_anything_else_says_how_to_answer() -> None:
    engine = make_engine()
    second = talk(engine, "메도빅 하나요", "음 그러니까", "어 저기")
    assert second.utterances == [
        SORRY,
        "더 주문하실 게 있으면 메도빅이나 나폴레옹처럼 메뉴 이름을, "
        "없으면 없어요라고 말씀해 주세요.",
        MORE_Q,
    ]
    # 그 보기대로 답하면 주문이 끝난다
    assert talk(engine, "없어요").state is State.ASK_PICKUP


def test_second_reask_of_the_pickup_time_gives_times_this_store_can_do() -> None:
    engine = make_engine()
    second = talk(engine, "메도빅 하나요", "그게 다예요", "음 그러니까", "어 저기")
    # 16:00 + 준비 15분 → 4시 15분, 그 30분 뒤
    assert second.utterances == [
        SORRY, "4시 15분이나 4시 45분처럼 시간으로 말씀해 주세요.", "몇 시쯤 오세요?"]
    assert second.state is State.ASK_PICKUP


def test_second_reask_of_the_readback_says_yes_or_no_and_still_ends_with_the_readback() -> None:
    engine = make_engine()
    first = talk(engine, "메도빅 하나요", "그게 다예요", "6시 반이요", "음 그러니까")
    assert first.readback
    second = talk(engine, "어 저기")
    assert second.utterances[:2] == [SORRY, "맞으면 네, 고칠 게 있으면 아니요라고 말씀해 주세요."]
    assert second.utterances[-1].endswith("맞으세요?")
    assert second.readback and second.state is State.CONFIRM


def test_second_reask_of_a_yes_no_question_says_yes_or_no() -> None:
    engine = make_engine()
    asked = talk(engine, "메도빅 하나요", "그게 다예요", "4시 5분이요")
    assert asked.utterances[-1].endswith("괜찮으세요?")
    second = talk(engine, "음 그러니까", "어 저기")
    assert second.utterances[1] == "네 또는 아니요로 말씀해 주세요."
    assert second.utterances[-1].endswith("괜찮으세요?")


def test_no_hint_when_the_menu_was_just_listed() -> None:
    engine = make_engine()
    second = talk(engine, "짜장면 하나요", "짬뽕 하나요")
    assert "같은 메뉴가 있어요" not in " ".join(second.utterances)
    assert second.utterances[0].startswith("죄송해요, 그 메뉴는 없어요.")


def test_first_reask_has_no_hint_and_progress_resets_the_slot_count() -> None:
    engine = make_engine()
    talk(engine, "음 그러니까")
    talk(engine, "메도빅 하나요")
    again = talk(engine, "음 그러니까")
    assert again.utterances == [SORRY, MORE_Q]


# ------------------------------------------------ 통화 전체 누적


def test_reasks_across_slots_hand_off_at_the_call_limit() -> None:
    engine = make_engine()
    r = talk(engine, "음 그러니까", "어 저기")  # 주문 슬롯 2번
    assert (r.reask, r.reasks, r.handoff) == ("order", 2, "")
    r = talk(engine, "메도빅 하나요", "그게 다예요", "음 그러니까", "어 저기")  # 시각 슬롯 2번
    assert (r.reask, r.reasks, r.state) == ("pickup", 4, State.ASK_PICKUP)
    r = talk(engine, "6시 반이요", "음 그러니까")  # 복창 1번 = 통화 5번째
    assert r.utterances == [GIVE_UP, HANDOFF]
    assert r.state is State.FALLBACK
    assert (r.handoff, r.reask, r.reasks) == ("call_failures", "confirm", 5)
    assert r.order is not None and r.order.status == "handoff"
    assert [line.menu_id for line in r.order.lines] == ["m01"]


def test_same_slot_three_times_still_hands_off_first_with_its_own_reason() -> None:
    engine = make_engine()
    r = talk(engine, "음 그러니까", "어 저기", "음 음")
    assert r.utterances == [GIVE_UP, HANDOFF]
    assert (r.handoff, r.reask, r.reasks) == ("slot_failures", "order", 3)


def test_call_limit_comes_from_the_store_config_and_zero_turns_it_off() -> None:
    engine = make_engine(max_reasks=2)
    r = talk(engine, "음 그러니까", "어 저기")
    assert r.handoff == "call_failures"

    engine = make_engine(max_reasks=0)
    r = talk(engine, "음 그러니까", "어 저기", "메도빅 하나요", "음", "어 저기",
             "그게 다예요", "음", "어 저기")
    assert r.state is State.ASK_PICKUP and r.reasks == 6 and r.handoff == ""


def test_not_heard_counts_toward_the_call_limit() -> None:
    engine = make_engine(max_reasks=4)
    talk(engine, "음 그러니까", "어 저기")
    r = engine.not_heard(NOW)
    assert (r.reask, r.reasks, r.state) == ("not_heard", 3, State.ORDERING)
    assert r.utterances == ["죄송해요, 잘 못 들었어요. 다시 말씀해 주세요."]
    r = engine.not_heard(NOW)
    assert r.utterances == [GIVE_UP, HANDOFF]
    assert (r.handoff, r.state) == ("call_failures", State.FALLBACK)
    assert r.order is not None and r.order.status == "handoff"


def test_turns_that_understood_do_not_count() -> None:
    engine = make_engine()
    r = talk(engine, "메도빅 하나요", "그게 다예요", "6시 반이요")
    assert (r.reask, r.reasks, r.handoff) == ("", 0, "")
    r = talk(engine, "네")
    assert r.order is not None and r.order.status == "confirmed" and r.handoff == ""


# ------------------------------------------------ 짜증·사람 요청


def test_frustration_after_a_reask_hands_off_at_once() -> None:
    engine = make_engine()
    talk(engine, "메도빅 하나요", "음 그러니까")
    r = talk(engine, "아니 몇 번을 말해요")
    assert r.utterances == ["불편을 드려 죄송해요.", HANDOFF]
    assert (r.state, r.handoff, r.route) == (State.FALLBACK, "frustrated", "rule")
    assert r.order is not None and r.order.status == "handoff"
    assert r.order.transcript[-3:] == [
        "손님: 아니 몇 번을 말해요", "AI: 불편을 드려 죄송해요.", f"AI: {HANDOFF}"]


def test_frustration_words_before_any_reask_are_read_as_usual() -> None:
    engine = make_engine()
    r = talk(engine, "메도빅 하나요", "아까 말했잖아요")
    assert r.state is State.ORDERING and r.handoff == ""
    assert r.utterances == [SORRY, MORE_Q]  # 가짜 NLU는 모르는 말이다. 되묻기로 센다
    assert talk(engine, "진짜 답답하네").handoff == "frustrated"


def test_asking_for_a_person_hands_off_with_the_requested_reason() -> None:
    engine = make_engine()
    r = talk(engine, "메도빅 하나요", "사람이랑 얘기할게요")
    assert r.utterances == [HANDOFF]
    assert (r.state, r.handoff, r.route) == (State.FALLBACK, "requested", "rule")


def test_snapshot_restores_the_call_wide_count() -> None:
    engine = make_engine()
    talk(engine, "음 그러니까")
    snap = engine.snapshot()
    assert talk(engine, "어 저기").reasks == 2
    engine.restore(snap)
    assert talk(engine, "어 저기").reasks == 2


# ------------------------------------------------ 경로 표시 (턴 기록용)


def test_route_tells_rule_turns_from_nlu_turns() -> None:
    engine = make_engine()
    assert talk(engine, "메도빅 하나요").route == "nlu"
    assert talk(engine, "아니요 그게 다예요").route == "rule"
    assert talk(engine, "6시 반이요").route == "nlu"
    assert talk(engine, "네").route == "rule"
    assert make_engine().handle("잠깐만요", NOW, wait_request=True).route == "rule"
