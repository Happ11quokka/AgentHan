"""사장님 허브의 손님 문자 미리보기(4단계 티켓 07): HTTP 없이 공개 인터페이스만 본다.

실제 문자는 보내지 않는다. 허브가 통화 id마다 "손님에게 보낸 문자" 목록을 들고, 사장님 카드
(`order.update`·`call.update`)에 싣고, 손님 통화 화면이 받는 문자 스트림(`watch_texts`)으로 보낸다.

- 확정 주문: 메뉴 요약·합계·픽업 시각 고정 문장
- 늦은 버튼 답(시간 초과 뒤·통화 끝 뒤): 고정 한국어 문장. 제시간 버튼 답은 손님이 통화로 들었다
- 자유 입력 답(제시간이든 늦든): 사장님 글(ru)을 ko로 번역해 "사장님 답변(번역): «…»" 틀.
  번역이 안 되면 원문 + "번역 실패"
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from test_owner_calls import Screen
from test_owner_questions import snapshot

from agenthan.core.contracts import Order, OrderLine
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.call.fakes import FakeClock
from agenthan.voice.dialog.owner_question import OwnerQuestion, OwnerReply
from agenthan.voice.owner.fakes import FakeTranslator
from agenthan.voice.owner.hub import AlreadyAnswered, OwnerHub, UnknownCard

NOW = datetime.fromisoformat("2026-11-08T16:02:00+09:00")
LATER = datetime.fromisoformat("2026-11-08T16:07:00+09:00")
NUTS = OwnerQuestion(number=1, menu_id="m01", menu_name="메도빅", text="메도빅에 견과류 들어가요?")
TWO_MEDOVIK = OrderLine("m01", 2, name="메도빅", unit_price=7_500)
AMERICANO = OrderLine("m03", 1, name="아메리카노", unit_price=4_500)


def _hub(**kw) -> tuple[OwnerHub, FakeClock]:
    clock = FakeClock(NOW)
    return OwnerHub(store=FakeStoreKnowledge.from_fixture(), clock=clock, **kw), clock


def _order(call_id: str = "call-1", lines=(TWO_MEDOVIK, AMERICANO), status="confirmed") -> Order:
    return Order(id=call_id, lines=list(lines), pickup_time="2026-11-08T18:30:00+09:00",
                 status=status, created_at="2026-11-08T16:05:00+09:00")


def test_a_confirmed_order_texts_the_customer_a_summary() -> None:
    hub, _ = _hub()
    hub.call_started("call-1")
    hub.submit(_order())
    [text] = hub.customer_texts("call-1")
    assert text == {
        "id": "call-1-s1",
        "call_id": "call-1",
        "kind": "order",
        "text": "주문이 확정됐어요. 메도빅 2개, 아메리카노 1개, 합계 19,500원. "
                "18:30에 준비해 둘게요.",
        "sent_at": "2026-11-08T16:02:00+09:00",
        "sent_label": "16:02",
        "question_id": "",
        "translate_failed": False,
    }


def test_a_button_answer_in_time_sends_no_text() -> None:
    hub, _ = _hub()
    hub.call_started("call-1")
    hub.open_question("call-1", NUTS, lambda note: None)
    hub.answer_question("call-1-q1", "yes")
    assert hub.customer_texts("call-1") == []  # 손님은 통화에서 들었다


def test_a_late_button_answer_after_the_timeout_texts_the_customer_on_the_order_card() -> None:
    async def scenario():
        hub, clock = _hub()
        hub.call_started("call-1")
        hub.open_question("call-1", NUTS, lambda note: None)
        hub.question_timed_out("call-1", 1)
        hub.submit(_order())
        screen = Screen(hub)
        await screen.take()  # snapshot
        clock.set(LATER)
        hub.answer_question("call-1-q1", "no")
        events = await screen.take(2)
        await screen.nothing_more()
        await screen.close()
        return hub, events

    hub, events = asyncio.run(scenario())
    order_text, late = hub.customer_texts("call-1")
    assert late == {
        "id": "call-1-s2", "call_id": "call-1", "kind": "late_answer",
        "text": "메도빅 문의 답변이에요. 사장님께 여쭤보니 안 된다고 하세요.",
        "sent_at": "2026-11-08T16:07:00+09:00", "sent_label": "16:07",
        "question_id": "call-1-q1", "translate_failed": False,
    }
    assert [e.name for e in events] == ["owner.question.update", "order.update"]
    assert events[1].data["sms"] == [order_text, late]


def test_a_button_answer_after_the_call_ended_texts_the_customer_on_the_missed_card() -> None:
    async def scenario():
        hub, _ = _hub()
        hub.call_started("call-1")
        hub.open_question("call-1", NUTS, lambda note: None)
        hub.call_ended("call-1", "hangup")
        screen = Screen(hub)
        await screen.take()  # snapshot
        hub.answer_question("call-1-q1", "unsure")
        events = await screen.take(2)
        await screen.close()
        return events

    question, call = asyncio.run(scenario())
    assert (call.name, call.data["status"]) == ("call.update", "missed")
    assert [t["text"] for t in call.data["sms"]] == [
        "메도빅 문의 답변이에요. 사장님께 여쭤봤는데 사장님도 정확히는 모르신대요."]


ORECHI = "Да, там есть грецкие орехи."
ORECHI_KO = "네, 호두가 들어 있어요."


def test_a_written_answer_goes_to_the_call_as_a_kind_and_to_the_customer_as_a_text() -> None:
    async def scenario():
        translator = FakeTranslator({ORECHI: ORECHI_KO})
        hub, _ = _hub(translator=translator)
        hub.call_started("call-1")
        notes: list[object] = []
        hub.open_question("call-1", NUTS, notes.append)
        screen = Screen(hub)
        await screen.take()  # snapshot
        answered = hub.answer_question("call-1-q1", "free", text=f"  {ORECHI} ")
        events = await screen.take(3)  # 질문 카드(답함) · 번역이 붙은 질문 카드 · 통화 카드(문자)
        await screen.close()
        return hub, translator, notes, answered, events

    hub, translator, notes, answered, events = asyncio.run(scenario())
    assert (answered["status"], answered["answer"]) == ("answered", "free")
    assert (answered["answer_text"], answered["answer_text_ko"]) == (ORECHI, "")
    assert notes == [OwnerReply(question=1, answer="free")]  # 통화에는 종류만 (글은 읽지 않는다)
    assert ((ORECHI,), "ru", "ko") in translator.calls
    [text] = hub.customer_texts("call-1")
    assert (text["kind"], text["question_id"], text["translate_failed"]) == (
        "free_answer", "call-1-q1", False)
    assert text["text"] == "메도빅 문의 답변이에요. 사장님 답변(번역): «네, 호두가 들어 있어요.»"
    assert [e.name for e in events] == [
        "owner.question.update", "owner.question.update", "call.update"]
    assert events[1].data["answer_text_ko"] == ORECHI_KO
    assert events[2].data["sms"] == [text]


@pytest.mark.parametrize("translator", [None, FakeTranslator(fail=True)])
def test_an_untranslated_written_answer_texts_the_original_marked_as_failed(translator) -> None:
    async def scenario():
        hub, _ = _hub(translator=translator)
        hub.open_question("call-1", NUTS, lambda note: None)
        hub.question_timed_out("call-1", 1)  # 늦은 자유 입력도 같은 문자 하나
        hub.answer_question("call-1-q1", "free", text=ORECHI)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return hub

    hub = asyncio.run(scenario())
    [text] = hub.customer_texts("call-1")
    assert text["text"] == (
        "메도빅 문의 답변이에요. 사장님 답변(원문, 번역 실패): «Да, там есть грецкие орехи.»")
    assert text["translate_failed"] is True


def test_a_question_takes_only_one_answer_written_or_button() -> None:
    hub, _ = _hub()
    hub.open_question("call-1", NUTS, lambda note: None)
    hub.answer_question("call-1-q1", "yes")
    with pytest.raises(AlreadyAnswered):
        hub.answer_question("call-1-q1", "free", text=ORECHI)
    with pytest.raises(AlreadyAnswered):
        hub.answer_question("call-1-q1", "no")
    assert hub.customer_texts("call-1") == []


@pytest.mark.parametrize("text", ["", "   "])
def test_a_written_answer_needs_some_text(text: str) -> None:
    hub, _ = _hub()
    hub.open_question("call-1", NUTS, lambda note: None)
    with pytest.raises(ValueError):
        hub.answer_question("call-1-q1", "free", text=text)
    assert hub.answer_question("call-1-q1", "yes")["answer_text"] == ""  # 버튼 답은 글 없음


def test_the_customer_text_stream_starts_with_the_texts_so_far_then_brings_new_ones() -> None:
    async def scenario():
        hub, _ = _hub()
        hub.call_started("call-1")
        hub.open_question("call-1", NUTS, lambda note: None)
        hub.submit(_order())
        hub.call_ended("call-1", "confirmed")
        stream = hub.watch_texts("call-1")
        first = await asyncio.wait_for(anext(stream), 1)
        hub.call_started("call-2")
        hub.submit(_order("call-2"))  # 다른 통화의 문자는 오지 않는다
        hub.answer_question("call-1-q1", "yes")  # 통화 끝 뒤 늦은 답
        second = await asyncio.wait_for(anext(stream), 1)
        await stream.aclose()
        return hub, first, second

    hub, first, second = asyncio.run(scenario())
    order_text, late = hub.customer_texts("call-1")
    assert (first.name, first.data) == ("sms.snapshot", {"call_id": "call-1", "sms": [order_text]})
    assert (second.name, second.data) == ("sms", late)


def test_a_customer_text_stream_for_an_unknown_call_is_refused() -> None:
    hub, _ = _hub()
    with pytest.raises(UnknownCard):
        hub.watch_texts("no-such-call")


def test_a_late_answer_to_a_contains_question_texts_what_the_buttons_meant() -> None:
    # 리뷰 P2: 들어가요?류 질문의 늦은 답 문자도 들어간다/안 들어간다로 쓴다(통화 문장과 같다)
    hub, _ = _hub()
    hub.call_started("call-1")
    hub.open_question("call-1", OwnerQuestion(
        number=1, menu_id="m01", menu_name="메도빅", text="메도빅에 견과류 들어가요?",
        kind="allergen", subject="견과", yes_no=True), lambda note: None)
    hub.question_timed_out("call-1", 1)
    hub.answer_question("call-1-q1", "no")
    [late] = hub.customer_texts("call-1")
    assert late["text"] == (
        "메도빅 문의 답변이에요. 사장님께 여쭤보니 메도빅에 견과는 안 들어간대요.")


# ---------------------------------------------------------------- 리뷰 C1·C9


def test_an_answer_in_time_that_the_call_never_said_is_texted_when_the_call_ends() -> None:
    # 리뷰 C1: 제시간 답이 손님 말 때문에 미뤄진 채 통화가 끝나면 통화는 말하지 못했다. 손님은
    # "확인해서 문자로 알려 드릴게요"를 들었으니 문자로 보낸다
    hub, _ = _hub()
    hub.call_started("call-1")
    hub.open_question("call-1", NUTS, lambda note: None)
    hub.answer_question("call-1-q1", "no")
    hub.submit(_order())
    hub.call_ended("call-1", "confirmed")
    order_text, late = hub.customer_texts("call-1")
    assert (late["kind"], late["text"], late["question_id"]) == (
        "late_answer", "메도빅 문의 답변이에요. 사장님께 여쭤보니 안 된다고 하세요.", "call-1-q1")
    [card] = snapshot(hub)["orders"]
    assert card["sms"] == [order_text, late]


def test_an_answer_the_call_said_is_not_texted() -> None:
    hub, _ = _hub()
    hub.call_started("call-1")
    hub.open_question("call-1", NUTS, lambda note: None)
    hub.answer_question("call-1-q1", "yes")
    hub.answer_said("call-1", 1)
    hub.call_ended("call-1", "hangup")
    assert hub.customer_texts("call-1") == []


def test_a_late_answer_clears_the_owner_check_on_the_order_card() -> None:
    # 리뷰 C9: "사장님 확인 필요"는 답을 받으면 내린다(확정 때 한 번만 재지 않는다)
    async def scenario():
        hub, _ = _hub()
        hub.call_started("call-1")
        hub.open_question("call-1", NUTS, lambda note: None)
        hub.question_timed_out("call-1", 1)
        hub.submit(_order())
        screen = Screen(hub)
        [snapshot] = await screen.take()
        hub.answer_question("call-1-q1", "yes")
        events = await screen.take(2)
        await screen.nothing_more()
        await screen.close()
        return snapshot, events

    snapshot, events = asyncio.run(scenario())
    assert snapshot.data["orders"][0]["owner_check"] is True
    assert [e.name for e in events] == ["owner.question.update", "order.update"]
    assert events[1].data["owner_check"] is False


def test_a_written_answer_clears_the_owner_check_too() -> None:
    hub, _ = _hub()
    hub.call_started("call-1")
    hub.open_question("call-1", NUTS, lambda note: None)
    hub.question_timed_out("call-1", 1)
    hub.submit(_order())
    hub.answer_question("call-1-q1", "free", "Без орехов")
    assert [o["owner_check"] for o in snapshot(hub)["orders"]] == [False]
