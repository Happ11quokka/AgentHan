"""주문 승인 티켓 06: 재료 소진 거절과 오늘 품절 (엔진 이음매, spec "엔진").

통화 중에 재료 소진 거절이 오면 떨어진 메뉴를 이 통화 메뉴에서 품절로 바꾸고, 품절 안내와
"빼고 주문할까요, 다른 메뉴로 바꾸실까요?"를 묻고 주문 받기로 돌아간다. 빼기만 하면 재승인 없이
수락으로 끝나고, 더하거나 바꾸면 다시 복창하고 새 번호로 확인을 요청한다. 오늘 품절 목록은
생성자(`sold_out`)와 `apply_sold_out`으로 받는다(다음 통화·결정 직전).
"""

from __future__ import annotations

import pytest
from test_engine_approval import NOW, TurnResult, make_engine, talk

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.approval import OwnerDecision
from agenthan.voice.dialog.engine import DialogEngine
from agenthan.voice.dialog.states import State
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.store_config import StoreConfig

TWO_ITEMS = ("메도빅 하나랑 나폴레옹 하나요", "그게 다예요", "6시 반이요")
NOTICE = "메도빅은 지금 재료가 떨어졌대요. 빼고 주문할까요, 다른 메뉴로 바꾸실까요?"
CLOSING = "네, 6시 반까지 준비해 둘게요. 감사합니다."
HOLD = "사장님께 주문 확인 중이에요. 잠시만요."


def out_of_stock(engine: DialogEngine, *menu_ids: str, number: int = 1) -> TurnResult:
    return engine.owner_decided(
        OwnerDecision(number, "reject", reason="out_of_stock", menu_ids=menu_ids), NOW)


def waiting_two_items() -> DialogEngine:
    """메도빅 하나·나폴레옹 하나를 복창하고 "네" → 확인 요청 1을 연 엔진."""
    engine = make_engine()
    talk(engine, *TWO_ITEMS)
    r = talk(engine, "네")
    assert r.approval_request is not None and r.approval_request.number == 1
    return engine


def lines(r: TurnResult) -> list[tuple[str, int]]:
    assert r.order is not None
    return [(line.menu_id, line.quantity) for line in r.order.lines]


def test_out_of_stock_names_the_menu_and_asks_to_drop_or_swap() -> None:
    engine = waiting_two_items()
    r = out_of_stock(engine, "m01")

    assert r.utterances == [NOTICE]
    assert r.state is State.ORDERING  # 주문 받기로 돌아간다. 통화는 이어진다
    assert r.order is None
    assert [line.item.id for line in r.cart.lines] == ["m02"]  # 떨어진 줄은 뺐다


@pytest.mark.parametrize("answer", ["빼 주세요", "네", "메도빅은 빼 주세요", "그게 다예요"])
def test_dropping_the_sold_out_menu_is_accepted_without_asking_again(answer) -> None:
    engine = waiting_two_items()
    out_of_stock(engine, "m01")
    r = talk(engine, answer)

    assert r.utterances == [CLOSING]
    assert r.state is State.CLOSING
    assert r.approval_request is None  # 재승인 없음
    assert r.order is not None and r.order.status == "confirmed"
    assert lines(r) == [("m02", 1)]
    assert r.order.pickup_time == "2026-11-08T18:30:00+09:00"


def test_swapping_reads_back_again_and_asks_with_a_new_number() -> None:
    engine = waiting_two_items()
    out_of_stock(engine, "m01")
    r = talk(engine, "블린으로 바꿔 주세요")

    assert r.state is State.CONFIRM and r.readback
    assert r.utterances[-1] == ("그럼 다시 확인할게요. 나폴레옹 하나, 블린 하나, 6시 반에 찾으러 "
                                "오시고, 모두 16,000원이에요. 맞으세요?")
    assert r.order is None

    r = talk(engine, "네")
    assert r.utterances == [HOLD]
    assert r.approval_request is not None and r.approval_request.number == 2
    assert [(line.menu_id, line.quantity) for line in r.approval_request.order.lines] == [
        ("m02", 1), ("m04", 1)]


def test_no_alone_asks_what_to_swap_to() -> None:
    engine = waiting_two_items()
    out_of_stock(engine, "m01")
    r = talk(engine, "아니요")

    assert r.utterances == ["어떤 메뉴로 바꿔 드릴까요?"]
    assert r.state is State.ORDERING and r.order is None

    r = talk(engine, "블린 하나요")
    assert r.state is State.CONFIRM and r.readback  # 바꿨다 → 다시 복창


def test_asking_for_the_sold_out_menu_again_gets_the_sold_out_notice() -> None:
    engine = waiting_two_items()
    out_of_stock(engine, "m01")
    r = talk(engine, "메도빅 하나 주세요")

    assert r.utterances[0] == "죄송해요, 메도빅은 오늘 다 떨어졌어요."
    assert all(line.item.id != "m01" for line in r.cart.lines)


def test_several_sold_out_menus_are_named_together() -> None:
    engine = make_engine()
    talk(engine, "메도빅 하나랑 나폴레옹 하나랑 블린 하나요", "그게 다예요", "6시 반이요")
    talk(engine, "네")
    r = out_of_stock(engine, "m01", "m04")

    assert r.utterances == [
        "메도빅이랑 블린은 지금 재료가 떨어졌대요. 빼고 주문할까요, 다른 메뉴로 바꾸실까요?"]
    r = talk(engine, "네")
    assert lines(r) == [("m02", 1)]


def test_when_everything_ran_out_the_customer_orders_again() -> None:
    engine = make_engine()
    talk(engine, *TWO_ITEMS)
    talk(engine, "네")
    r = out_of_stock(engine, "m01", "m02")

    assert r.utterances == [
        "메도빅이랑 나폴레옹은 지금 재료가 떨어졌대요. 다른 메뉴로 주문하시겠어요?"]
    assert r.cart.is_empty()

    r = talk(engine, "네")
    assert r.order is None and r.state is State.ORDERING  # 빈 주문은 수락하지 않는다
    r = talk(engine, "블린 하나요", "그게 다예요")
    assert r.state is State.CONFIRM and r.readback  # 새 주문 → 다시 복창
    r = talk(engine, "네")
    assert r.approval_request is not None and r.approval_request.number == 2


def test_with_nothing_left_no_asks_whether_to_cancel() -> None:
    engine = make_engine()
    talk(engine, "메도빅 하나요", "그게 다예요", "6시 반이요")
    talk(engine, "네")
    out_of_stock(engine, "m01")

    r = talk(engine, "아니요")
    assert r.utterances == ["주문 취소할까요?"]
    r = talk(engine, "네")
    assert r.state is State.DONE and r.order is None


def test_a_stale_out_of_stock_decision_is_dropped() -> None:
    engine = waiting_two_items()
    talk(engine, "블린 하나 추가요")  # 기다리는 중에 고침 → 요청 1을 거둠

    r = out_of_stock(engine, "m01")
    assert r.utterances == [] and r.state is not State.ORDERING


def test_today_sold_out_from_the_hub_applies_from_the_next_call() -> None:
    engine = DialogEngine(FakeStoreKnowledge.from_fixture(), FakeNlu(),
                          StoreConfig.from_fixture(), order_id="o-next", sold_out=("m01",))
    engine.start(NOW)
    r = talk(engine, "메도빅 하나요")

    assert r.utterances[0] == "죄송해요, 메도빅은 오늘 다 떨어졌어요."
    assert r.cart.is_empty()


def test_undone_sold_out_is_taken_again_when_the_list_is_reapplied() -> None:
    engine = DialogEngine(FakeStoreKnowledge.from_fixture(), FakeNlu(),
                          StoreConfig.from_fixture(), order_id="o-next", sold_out=("m01",))
    engine.start(NOW)
    engine.apply_sold_out(())  # 사장님이 되돌렸다

    r = talk(engine, "메도빅 하나요")
    assert [line.item.id for line in r.cart.lines] == ["m01"]
