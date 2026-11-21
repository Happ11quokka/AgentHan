"""금액이 모자랄 때 쿠폰 권유 (쿠폰 티켓 10).

대화 흐름(수락·다른 메뉴·거절·채울 메뉴 없음·복창 뒤 빠짐)은 대본 시나리오 s46·s50~s54가 본다.
여기서는 시나리오로 보기 어려운 것만: 주문 한도, 못 알아들음·무응답, 통화당 한 번, 오늘 품절,
되감기.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog import templates
from agenthan.voice.dialog.coupons import Coupon
from agenthan.voice.dialog.engine import DialogEngine, TurnResult
from agenthan.voice.dialog.states import State
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.store_config import KST, StoreConfig

NOW = datetime(2026, 11, 8, 17, 10, tzinfo=KST)
COUPON = Coupon("coupon-1", "전화 주문 1,000원 할인", "amount", 1000, date(2026, 11, 2),
                date(2026, 12, 31), min_order=10_000)
OFFER = templates.coupon_offer("전화 주문 1,000원 할인", 2_500, "아메리카노")
# 되물을 때(못 알아들음·무응답·질문에 답한 뒤)는 긴 권유를 되풀이하지 않는다 (실통화 2026-11-22)
OFFER_AGAIN = templates.coupon_offer_again("아메리카노")
PLAIN = "주문 확인할게요. 메도빅 하나, 6시 반에 찾으러 오시고, 모두 7,500원이에요. 맞으세요?"


def _engine(config: StoreConfig | None = None, *, sold_out: tuple[str, ...] = ()) -> DialogEngine:
    engine = DialogEngine(FakeStoreKnowledge.from_fixture(), FakeNlu(),
                          config or StoreConfig.from_fixture(), order_id="o-offer",
                          coupons=[COUPON], sold_out=sold_out)
    engine.start(NOW)
    return engine


def _to_pickup(engine: DialogEngine) -> TurnResult:
    """메도빅 하나(7,500원, 2,500원 모자람) → 주문 끝 → 6시 반. 복창 직전 턴의 결과."""
    engine.handle("메도빅 하나요", NOW)
    engine.handle("그게 다예요", NOW)
    return engine.handle("6시 반이요", NOW)


def test_the_offer_sentence() -> None:
    # 실통화(2026-11-22): 네 문장 권유가 길고 되물을 때마다 다 읽었다. 두 문장으로, 되물을 때는 짧게
    assert OFFER == ("2,500원만 더 담으시면 전화 주문 1,000원 할인 쿠폰을 쓸 수 있어요. "
                     "아메리카노 하나 더 담으실까요?")
    assert templates.coupon_offer("전화 주문 1,000원 할인", 3_000, None) == (
        "3,000원만 더 담으시면 전화 주문 1,000원 할인 쿠폰을 쓸 수 있어요. 더 담으실까요?")
    assert templates.coupon_offer("가을 쿠폰", 500, "블린").startswith(
        "500원만 더 담으시면 가을 쿠폰을")
    assert templates.coupon_offer_again("아메리카노") == "아메리카노 하나 더 담으실까요?"
    assert templates.coupon_offer_again(None) == "더 담으실까요?"


def test_the_offer_is_not_a_readback() -> None:
    result = _to_pickup(_engine())
    assert (result.state, result.utterances, result.readback) == (State.ORDERING, [OFFER], False)


def test_no_offer_without_a_shortfall() -> None:
    engine = DialogEngine(FakeStoreKnowledge.from_fixture(), FakeNlu(),
                          StoreConfig.from_fixture(), order_id="o")
    engine.start(NOW)
    assert _to_pickup(engine).utterances == [PLAIN]


# ---------------------------------------------------------------- 주문 한도


def test_a_suggestion_over_the_order_total_drops_the_menu_name() -> None:
    """아메리카노를 더하면 12,000원 > 한도 10,000원. 2,500원은 더 담을 수 있어 이름 없이 권한다."""
    result = _to_pickup(_engine(replace(StoreConfig.from_fixture(), max_order_total=10_000)))
    assert result.utterances == [templates.coupon_offer("전화 주문 1,000원 할인", 2_500, None)]


def test_no_offer_when_filling_the_shortfall_breaks_the_order_total() -> None:
    result = _to_pickup(_engine(replace(StoreConfig.from_fixture(), max_order_total=9_000)))
    assert (result.state, result.utterances) == (State.CONFIRM, [PLAIN])


def test_no_offer_when_one_more_item_breaks_the_order_qty() -> None:
    result = _to_pickup(_engine(replace(StoreConfig.from_fixture(), max_order_qty=1)))
    assert (result.state, result.utterances) == (State.CONFIRM, [PLAIN])


def test_the_suggestion_skips_menus_sold_out_today() -> None:
    """오늘 품절 아메리카노는 권하지 않는다. 한 개로 2,500원을 채우는 다음 메뉴는 나폴레옹."""
    result = _to_pickup(_engine(sold_out=("m03",)))
    assert result.utterances == [
        templates.coupon_offer("전화 주문 1,000원 할인", 2_500, "나폴레옹")]


# ---------------------------------------------------------------- 못 알아들음·무응답


def test_an_unclear_answer_asks_once_more_then_reads_back_without_the_coupon() -> None:
    engine = _engine()
    _to_pickup(engine)
    again = engine.handle("음", NOW)
    assert (again.state, again.utterances) == (State.ORDERING,
                                              [templates.not_understood(), OFFER_AGAIN])
    second = engine.handle("어", NOW)
    assert (second.state, second.utterances) == (State.CONFIRM, [templates.not_understood(), PLAIN])
    assert second.readback and second.handoff == ""
    closing = engine.handle("네", NOW)
    assert closing.order is not None and closing.order.discount == 0


def test_silence_asks_once_more_then_reads_back_without_the_coupon() -> None:
    engine = _engine()
    _to_pickup(engine)
    again = engine.no_input(NOW)
    assert again.utterances == [templates.hello_again(), OFFER_AGAIN]
    second = engine.no_input(NOW)
    assert (second.state, second.utterances) == (State.CONFIRM, [templates.hello_again(), PLAIN])
    assert second.readback


def test_a_question_is_answered_and_the_offer_asked_again() -> None:
    engine = _engine()
    _to_pickup(engine)
    result = engine.handle("아메리카노 얼마예요?", NOW)
    assert result.state is State.ORDERING and result.utterances[-1] == OFFER_AGAIN
    assert engine.handle("아니요", NOW).utterances == [PLAIN]


def test_yes_to_an_offer_without_a_menu_asks_what_to_add_then_reads_back() -> None:
    engine = _engine(replace(StoreConfig.from_fixture(), max_order_total=10_000))
    _to_pickup(engine)
    assert engine.handle("네", NOW).utterances == [templates.ask_order()]
    result = engine.handle("아니요", NOW)
    assert (result.state, result.utterances) == (State.CONFIRM, [PLAIN])


# ---------------------------------------------------------------- 통화당 한 번


def test_no_second_offer_after_a_fix_that_is_still_short() -> None:
    """권유를 거절하고 복창을 들은 뒤 "아니요" → 시각만 고쳐도 다시 권하지 않는다."""
    engine = _engine()
    _to_pickup(engine)
    engine.handle("아니요", NOW)
    engine.handle("아니요", NOW)  # 복창에 "아니요": 무엇을 고칠지 묻는다
    result = engine.handle("6시로 할게요", NOW)
    assert result.state is State.CONFIRM and "모두 7,500원이에요" in result.utterances[-1]
    assert not any("쿠폰" in u for u in result.utterances)


def test_no_offer_once_a_readback_went_out() -> None:
    """첫 복창에 쿠폰이 들었고 고쳐서 모자라지면 권하지 않고 쿠폰 빠진 금액으로 복창한다."""
    engine = _engine()
    engine.handle("메도빅 하나랑 나폴레옹 하나요", NOW)
    engine.handle("그게 다예요", NOW)
    assert "1,000원 빼고" in engine.handle("6시 반이요", NOW).utterances[-1]
    result = engine.handle("나폴레옹은 빼 주세요", NOW)
    assert result.state is State.CONFIRM and "모두 7,500원이에요" in result.utterances[-1]


def test_no_offer_when_the_call_already_heard_it() -> None:
    """쿠폰 문의 답(티켓 11)으로 이미 권했으면 복창 전에 다시 권하지 않는다."""
    engine = _engine()
    engine._coupon_offered = True  # 티켓 11이 권유 문장을 말할 때 세우는 표시
    assert _to_pickup(engine).utterances == [PLAIN]


# ---------------------------------------------------------------- 되감기


def test_rewinding_the_answer_turn_brings_the_offer_back() -> None:
    engine = _engine()
    _to_pickup(engine)
    snap = engine.snapshot()
    engine.handle("아니요", NOW)
    engine.restore(snap)
    result = engine.handle("네", NOW)
    assert [(line.item.id, line.qty) for line in result.cart.lines] == [("m01", 1), ("m03", 1)]
