"""tracer bullet: 메뉴 하나 주문이 인사부터 확인된 Order까지 간다."""

from datetime import datetime

import pytest

from agenthan.core.contracts import OrderLine
from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.engine import DialogEngine
from agenthan.voice.dialog.states import State
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.store_config import KST, StoreConfig

# 16시: "6시 반"이 3시간 안이라 픽업 확인 질문 없이 복창으로 간다 (티켓 04 규칙)
NOW = datetime(2026, 11, 8, 16, 0, tzinfo=KST)


@pytest.fixture
def engine() -> DialogEngine:
    return DialogEngine(
        FakeStoreKnowledge.from_fixture(),
        FakeNlu(),
        StoreConfig.from_fixture(),
        order_id="o-test",
    )


def test_start_greets_with_store_name(engine: DialogEngine) -> None:
    r = engine.start(NOW)
    assert r.utterances == ["네, 샘플 러시안 베이커리입니다. 주문 도와드릴게요."]
    assert r.state is State.ORDERING
    assert r.cart.is_empty()
    assert r.order is None


def test_single_item_order_end_to_end(engine: DialogEngine) -> None:
    engine.start(NOW)

    r = engine.handle("메도빅 하나요", NOW)
    assert r.state is State.ORDERING
    assert r.utterances == ["메도빅 하나요. 더 필요하신 거 있으세요?"]
    assert [(line.item.id, line.qty) for line in r.cart.lines] == [("m01", 1)]
    assert r.order is None

    r = engine.handle("그게 다예요", NOW)
    assert r.state is State.ASK_PICKUP
    assert r.utterances == ["몇 시쯤 오세요?"]
    assert r.order is None

    r = engine.handle("6시 반이요", NOW)
    assert r.state is State.CONFIRM
    assert len(r.utterances) == 1
    readback = r.utterances[0]
    assert "메도빅 하나" in readback
    assert "6시 반" in readback
    assert "총 7,500원" in readback
    assert readback.endswith("맞으세요?")
    assert r.order is None

    r = engine.handle("네", NOW)
    assert r.state is State.CLOSING
    assert r.utterances == ["6시 반에 준비해 둘게요. 감사합니다."]
    order = r.order
    assert order is not None
    assert order.id == "o-test"
    assert order.status == "confirmed"
    assert order.lines == [OrderLine(menu_id="m01", quantity=1)]
    assert order.pickup_time == "2026-11-08T18:30:00+09:00"
    assert order.customer_phone == StoreConfig.from_fixture().demo_phone


def test_order_is_none_until_yes(engine: DialogEngine) -> None:
    results = [engine.start(NOW)]
    for text in ["메도빅 하나요", "그게 다예요", "6시 반이요"]:
        results.append(engine.handle(text, NOW))
    assert all(r.order is None for r in results)
    assert all(r.state is not State.CLOSING for r in results)


def test_turn_results_keep_their_own_cart(engine: DialogEngine) -> None:
    engine.start(NOW)
    first = engine.handle("메도빅 하나요", NOW)
    engine.handle("메도빅 하나요", NOW)
    assert first.cart.lines[0].qty == 1


def test_after_closing_engine_is_done(engine: DialogEngine) -> None:
    engine.start(NOW)
    for text in ["메도빅 하나요", "그게 다예요", "6시 반이요", "네"]:
        engine.handle(text, NOW)
    r = engine.handle("여보세요?", NOW)
    assert r.state is State.DONE
    assert r.utterances == []
    assert r.order is None


def test_no_at_confirm_goes_back_to_ordering(engine: DialogEngine) -> None:
    engine.start(NOW)
    for text in ["메도빅 하나요", "그게 다예요", "6시 반이요"]:
        engine.handle(text, NOW)
    r = engine.handle("아니요", NOW)
    assert r.state is State.ORDERING
    assert r.order is None
    assert r.utterances == ["어떤 걸 고쳐 드릴까요?"]


def test_done_ordering_with_pickup_already_given_goes_to_confirm(engine: DialogEngine) -> None:
    engine.start(NOW)
    engine.handle("메도빅 하나요", NOW)
    engine.handle("6시에 갈게요", NOW)
    r = engine.handle("그게 다예요", NOW)
    assert r.state is State.CONFIRM
    assert "6시 픽업" in r.utterances[0]


def test_unknown_menu_lists_menu_and_stays_ordering(engine: DialogEngine) -> None:
    engine.start(NOW)
    r = engine.handle("짜장면 하나요", NOW)
    assert r.state is State.ORDERING
    assert r.cart.is_empty()
    assert r.utterances == [
        "죄송해요, 그 메뉴는 없어요. 저희는 메도빅, 나폴레옹, 아메리카노, 블린이 있어요."
    ]


def test_alias_resolves_to_menu(engine: DialogEngine) -> None:
    engine.start(NOW)
    r = engine.handle("꿀케이크 하나요", NOW)
    assert [line.item.id for line in r.cart.lines] == ["m01"]


def test_unintelligible_turn_reasks_last_question(engine: DialogEngine) -> None:
    engine.start(NOW)
    engine.handle("메도빅 하나요", NOW)
    engine.handle("그게 다예요", NOW)
    r = engine.handle("음", NOW)
    assert r.state is State.ASK_PICKUP
    assert r.utterances == ["죄송해요, 잘 못 알아들었어요.", "몇 시쯤 오세요?"]


def test_transcript_records_both_sides(engine: DialogEngine) -> None:
    engine.start(NOW)
    for text in ["메도빅 하나요", "그게 다예요", "6시 반이요"]:
        engine.handle(text, NOW)
    order = engine.handle("네", NOW).order
    assert order is not None
    assert order.transcript[0] == "AI: 네, 샘플 러시안 베이커리입니다. 주문 도와드릴게요."
    assert order.transcript[1] == "손님: 메도빅 하나요"
    assert order.transcript[-1] == "AI: 6시 반에 준비해 둘게요. 감사합니다."


def test_handle_before_start_raises(engine: DialogEngine) -> None:
    with pytest.raises(RuntimeError):
        engine.handle("메도빅 하나요", NOW)
