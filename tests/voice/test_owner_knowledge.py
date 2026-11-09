"""사장님 허브의 보충 지식(4단계 티켓 13): HTTP 없이 공개 인터페이스만 본다.

- "손님이 궁금해한 것" 카드(`knowledge.update`): 보충 요청(통화 세션)·모르겠어요 버튼·시간 초과가
  만든다. 같은 메뉴·같은 종류(·같은 대상) 질문은 열린 카드 하나로 묶고 횟수를 센다
- 사장님 답: 미리보기(사장님 글 ru → ko 번역) → 저장(번역문이나 고친 한국어). 저장하면 카드는 채움,
  보충 지식 저장소에 들어간다
- 질문 카드 버튼 답 돼요/안 돼요는 "들어가요?"류 질문이면 저장소에 사장님 확인으로 들어간다
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from test_owner_calls import Screen

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.call.fakes import FakeClock
from agenthan.voice.dialog.engine import DialogEngine, TurnResult
from agenthan.voice.dialog.knowledge import KnowledgeKey, KnowledgeRequest, KnowledgeStore, Known
from agenthan.voice.dialog.owner_question import OwnerQuestion
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.owner.fakes import FakeTranslator
from agenthan.voice.owner.hub import OwnerHub, UnknownCard
from agenthan.voice.store_config import StoreConfig

NOW = datetime.fromisoformat("2026-11-10T16:02:00+09:00")
LATER = datetime.fromisoformat("2026-11-10T17:30:00+09:00")
TASTE_KEY = KnowledgeKey("m03", "taste")
TASTE = KnowledgeRequest(TASTE_KEY, menu_name="아메리카노", text="아메리카노 어떤 맛이에요?")
SWEET = KnowledgeRequest(TASTE_KEY, menu_name="아메리카노", text="아메리카노 달아요?")
NUTS_KEY = KnowledgeKey("m01", "allergen", "견과", yes_no=True)
NUTS = OwnerQuestion(number=1, menu_id="m01", menu_name="메도빅", text="메도빅에 견과류 들어가요?",
                     kind="allergen", subject="견과", yes_no=True)
VEGAN = OwnerQuestion(number=1, menu_id="m01", menu_name="메도빅", text="메도빅 비건이에요?",
                      kind="other", subject="비건이에요")


def _hub(**kw) -> tuple[OwnerHub, FakeClock]:
    clock = FakeClock(NOW)
    return OwnerHub(store=FakeStoreKnowledge.from_fixture(), clock=clock, **kw), clock


def _cards(events) -> list[dict]:
    return [e.data for e in events if e.name == "knowledge.update"]


# ---------------------------------------------------------------- 카드: 묶기·횟수


def test_a_passed_on_question_becomes_a_knowledge_card() -> None:
    async def scenario():
        hub, _ = _hub()
        screen = Screen(hub)
        [snapshot] = await screen.take()
        assert snapshot.data["knowledge"] == []
        hub.knowledge_wanted("call-1", TASTE)
        [event] = await screen.take()
        await screen.close()
        return event

    event = asyncio.run(scenario())
    assert event.name == "knowledge.update"
    assert event.data == {
        "id": "k1",
        "menu_id": "m03",
        "menu_name": "아메리카노",
        "menu_name_owner": "아메리카노",  # 원어 이름이 빈 메뉴는 한국어
        "kind": "taste",
        "subject": "",
        "yes_no": False,
        "call_id": "call-1",
        "text": "아메리카노 어떤 맛이에요?",
        "text_owner": "",
        "count": 1,
        "asked_at": "2026-11-10T16:02:00+09:00",
        "last_asked_at": "2026-11-10T16:02:00+09:00",
        "status": "open",
        "answer_text": "",
        "answer_ko": "",
        "saved_at": "",
    }


def test_the_same_kind_of_question_on_the_same_menu_is_counted_on_one_card() -> None:
    async def scenario():
        hub, clock = _hub()
        screen = Screen(hub)
        await screen.take()
        hub.knowledge_wanted("call-1", TASTE)
        clock.set(LATER)
        hub.knowledge_wanted("call-2", SWEET)
        hub.knowledge_wanted("call-2", KnowledgeRequest(KnowledgeKey("m03", "ingredient"),
                                                        menu_name="아메리카노",
                                                        text="아메리카노 재료가 뭐예요?"))
        got = _cards(await screen.take(3))
        await screen.close()
        return got

    first, again, other = asyncio.run(scenario())
    assert (again["id"], again["count"], again["text"], again["call_id"]) == (
        first["id"], 2, "아메리카노 달아요?", "call-2")  # 문구는 마지막 질문
    assert (again["asked_at"], again["last_asked_at"]) == (
        "2026-11-10T16:02:00+09:00", "2026-11-10T17:30:00+09:00")
    assert (other["id"], other["kind"], other["count"]) == ("k2", "ingredient", 1)


def test_the_question_on_a_card_is_translated_for_the_owner() -> None:
    async def scenario():
        hub, _ = _hub(translator=FakeTranslator({TASTE.text: "Какой вкус у американо?"}))
        screen = Screen(hub)
        await screen.take()
        hub.knowledge_wanted("call-1", TASTE)
        got = _cards(await screen.take(2))
        await screen.close()
        return got

    original, translated = asyncio.run(scenario())
    assert original["text_owner"] == ""  # 원문으로 먼저, 번역이 오면 같은 카드를 다시
    assert (translated["id"], translated["text_owner"]) == ("k1", "Какой вкус у американо?")


def test_the_card_shows_the_native_menu_name() -> None:
    hub, _ = _hub()
    hub.knowledge_wanted("call-1", KnowledgeRequest(NUTS_KEY, menu_name="메도빅", text=NUTS.text))
    [card] = hub.knowledge_cards()
    assert (card["menu_name"], card["menu_name_owner"]) == ("메도빅", "Медовик")


# ---------------------------------------------------------------- 미리보기 → 저장


def test_the_owner_answer_is_previewed_in_korean_and_saved_as_knowledge() -> None:
    translator = FakeTranslator({"Не сладкий, с орехами": "달지 않고 고소해요"})
    store = KnowledgeStore()

    async def scenario():
        hub, clock = _hub(translator=translator, knowledge=store)
        hub.knowledge_wanted("call-1", TASTE)
        preview = await hub.preview_knowledge("k1", "  Не сладкий, с орехами ")
        clock.set(LATER)
        saved = hub.save_knowledge("k1", "달지 않고 고소한 편이에요", "Не сладкий, с орехами")
        return preview, saved

    preview, saved = asyncio.run(scenario())
    assert preview == {"text": "Не сладкий, с орехами", "text_ko": "달지 않고 고소해요",
                       "translate_failed": False}
    assert (("Не сладкий, с орехами",), "ru", "ko") in translator.calls
    assert (saved["status"], saved["answer_ko"], saved["answer_text"], saved["saved_at"]) == (
        "filled", "달지 않고 고소한 편이에요", "Не сладкий, с орехами", "2026-11-10T17:30:00+09:00")
    # 사장님이 고친 한국어가 그대로 저장소에 (다듬지 않는다)
    assert store.lookup(TASTE_KEY) == Known(note="달지 않고 고소한 편이에요")


@pytest.mark.parametrize("translator", [None, FakeTranslator(fail=True)])
def test_a_preview_without_a_translation_says_so(translator) -> None:
    async def scenario():
        hub, _ = _hub(translator=translator)
        hub.knowledge_wanted("call-1", TASTE)
        return await hub.preview_knowledge("k1", "Не сладкий")

    assert asyncio.run(scenario()) == {"text": "Не сладкий", "text_ko": "",
                                       "translate_failed": True}


def test_a_saved_card_is_shown_on_the_screen_and_a_new_question_opens_a_new_card() -> None:
    async def scenario():
        hub, _ = _hub()
        hub.knowledge_wanted("call-1", TASTE)
        hub.save_knowledge("k1", "고소해요")
        screen = Screen(hub)
        [snapshot] = await screen.take()
        hub.knowledge_wanted("call-2", SWEET)  # 저장 사이에 이미 연 질문 등: 채운 카드는 그대로
        [event] = await screen.take()
        await screen.close()
        return snapshot, event

    snapshot, event = asyncio.run(scenario())
    assert [(c["id"], c["status"]) for c in snapshot.data["knowledge"]] == [("k1", "filled")]
    assert (event.data["id"], event.data["status"], event.data["count"]) == ("k2", "open", 1)


def test_saving_again_replaces_the_note() -> None:
    store = KnowledgeStore()
    hub, _ = _hub(knowledge=store)
    hub.knowledge_wanted("call-1", TASTE)
    hub.save_knowledge("k1", "고소해요")
    hub.save_knowledge("k1", "산미가 적어요")
    assert store.lookup(TASTE_KEY) == Known(note="산미가 적어요")


def test_unknown_cards_and_empty_answers_are_refused() -> None:
    hub, _ = _hub()
    hub.knowledge_wanted("call-1", TASTE)
    with pytest.raises(UnknownCard):
        hub.save_knowledge("k9", "고소해요")
    with pytest.raises(ValueError):
        hub.save_knowledge("k1", "   ")
    with pytest.raises(UnknownCard):
        asyncio.run(hub.preview_knowledge("k9", "Не сладкий"))
    with pytest.raises(ValueError):
        asyncio.run(hub.preview_knowledge("k1", " "))


# ---------------------------------------------------------------- 질문 카드 버튼 답 (Q20)


@pytest.mark.parametrize(("button", "contains"), [("yes", True), ("no", False)])
def test_a_yes_or_no_on_a_contains_question_is_saved_as_an_owner_check(button, contains) -> None:
    store = KnowledgeStore()
    hub, _ = _hub(knowledge=store)
    hub.open_question("call-1", NUTS, lambda note: None)
    hub.answer_question("call-1-q1", button)
    assert store.lookup(NUTS_KEY) == Known(contains=contains)


def test_a_late_yes_is_saved_too() -> None:
    store = KnowledgeStore()
    hub, _ = _hub(knowledge=store)
    hub.open_question("call-1", NUTS, lambda note: None)
    hub.call_ended("call-1", "hangup")
    hub.answer_question("call-1-q1", "yes")
    assert store.lookup(NUTS_KEY) == Known(contains=True)


def test_a_yes_on_a_question_that_is_not_about_contents_is_not_saved() -> None:
    store = KnowledgeStore()
    hub, _ = _hub(knowledge=store)
    hub.open_question("call-1", VEGAN, lambda note: None)
    hub.answer_question("call-1-q1", "yes")
    assert store.lookup(VEGAN.key) is None


def test_unsure_is_not_saved_and_goes_to_a_knowledge_card() -> None:
    async def scenario():
        store = KnowledgeStore()
        hub, _ = _hub(knowledge=store)
        screen = Screen(hub)
        await screen.take()
        hub.open_question("call-1", NUTS, lambda note: None)
        hub.answer_question("call-1-q1", "unsure")
        got = _cards(await screen.take(3))
        await screen.close()
        return store, got

    store, [card] = asyncio.run(scenario())
    assert store.lookup(NUTS_KEY) is None
    assert (card["menu_id"], card["kind"], card["subject"], card["text"], card["call_id"]) == (
        "m01", "allergen", "견과", NUTS.text, "call-1")


def test_a_timeout_goes_to_a_knowledge_card() -> None:
    async def scenario():
        hub, _ = _hub()
        screen = Screen(hub)
        await screen.take()
        hub.open_question("call-1", NUTS, lambda note: None)
        hub.question_timed_out("call-1", 1)
        got = _cards(await screen.take(3))
        await screen.close()
        return got

    [card] = asyncio.run(scenario())
    assert (card["kind"], card["subject"], card["count"]) == ("allergen", "견과", 1)


def test_free_answers_and_unanswered_question_cards_make_no_knowledge() -> None:
    store = KnowledgeStore()
    hub, _ = _hub(knowledge=store)
    hub.open_question("call-1", NUTS, lambda note: None)
    hub.answer_question("call-1-q1", "free", "Нет орехов")  # 자유 입력은 그 손님 문자로만
    hub.open_question("call-2", NUTS, lambda note: None)
    hub.call_ended("call-2", "confirmed")  # 통화 끝(문자 약속) 카드는 늦은 답을 기다린다
    assert store.lookup(NUTS_KEY) is None
    assert hub.knowledge_cards() == []


# ------------------------------------------------- 허브가 쓰고 다음 통화 엔진이 읽는다


def _next_call(hub: OwnerHub, text: str) -> TurnResult:
    engine = DialogEngine(FakeStoreKnowledge.from_fixture(), FakeNlu(), StoreConfig.from_fixture(),
                          order_id="call-2", ask_owner=True, knowledge=hub.knowledge)
    engine.start(NOW)
    return engine.handle(text, NOW)


def test_a_yes_on_the_question_card_answers_the_next_caller_without_a_card() -> None:
    hub, _ = _hub()
    hub.open_question("call-1", NUTS, lambda note: None)
    hub.answer_question("call-1-q1", "yes")
    r = _next_call(hub, NUTS.text)
    assert r.owner_question is None
    assert r.utterances[0] == "사장님 확인으로는 메도빅에 견과가 들어간대요."


def test_unsure_is_not_reused_so_the_next_caller_opens_a_card_again() -> None:
    hub, _ = _hub()
    hub.open_question("call-1", NUTS, lambda note: None)
    hub.answer_question("call-1-q1", "unsure")
    r = _next_call(hub, NUTS.text)
    assert r.owner_question is not None
