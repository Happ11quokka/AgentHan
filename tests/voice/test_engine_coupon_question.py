"""쿠폰 문의 (쿠폰 티켓 11). 손님이 "쿠폰 돼요?"라고 물으면 지금 장바구니의 쿠폰 계산으로 답한다.

대화 흐름은 대본 시나리오(s60~s63)가 본다. 여기서는 상태별 답과 이어 묻는 질문, 권유 한 번,
쿠폰 물음이 사장님 지식 요청·질문 카드로 가지 않는 것(주차·배달은 그대로 간다)을 본다.
"""

from __future__ import annotations

from datetime import date, datetime

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.answerer import AnswerReply, AnswerRequest
from agenthan.voice.dialog.coupons import Coupon
from agenthan.voice.dialog.engine import DialogEngine, TurnResult
from agenthan.voice.dialog.knowledge import KnowledgeStore
from agenthan.voice.dialog.states import State
from agenthan.voice.nlu.base import Act, NluContext
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.store_config import KST, StoreConfig

NOW = datetime(2026, 11, 8, 17, 10, tzinfo=KST)
COUPON = Coupon("coupon-1", "전화 주문 1,000원 할인", "amount", 1000, date(2026, 11, 2),
                date(2026, 12, 31), min_order=10_000)
PLAIN = Coupon("coupon-2", "단골 500원 할인", "amount", 500, date(2026, 11, 2),
               date(2026, 12, 31))
EXPIRED = Coupon("coupon-0", "지난 행사", "amount", 1000, date(2026, 10, 1), date(2026, 10, 31))

APPLIES = "네, 전화 주문 1,000원 할인 쿠폰이 있어서 1,000원 할인돼요."
OFFER = ("전화 주문 1,000원 할인 쿠폰이 있는데 2,500원이 부족해요. 아메리카노 하나 더 하시면 "
         "쓸 수 있어요. 다른 메뉴도 괜찮아요. 더 담으실까요?")
NONE = "지금 쓸 수 있는 쿠폰은 없어요."
ORDER_Q = "어떤 걸로 드릴까요?"
MORE_Q = "더 필요하신 거 있으세요?"
PASS_ON = "죄송해요, 그건 제가 정확히 모르겠어요. 사장님께 전해 둘게요."

# HCX가 쿠폰 말을 다른 말과 섞어 질문으로 낸 경우(규칙에 안 걸린 말)
LONG = "쿠폰 받으려면 얼마 더 사야 해요?"
MENU_DISCOUNT = "메도빅도 할인돼요?"
NLU = {
    LONG: [Act("ask_other", free_text=LONG)],
    MENU_DISCOUNT: [Act("ask_menu_info", menu_text="메도빅", menu_guess="메도빅",
                        free_text=MENU_DISCOUNT)],
    "주차 돼요?": [Act("ask_other", free_text="주차 돼요?")],
    "배달 돼요?": [Act("ask_other", free_text="배달 돼요?")],
}


class _Nlu(FakeNlu):
    def parse(self, text: str, ctx: NluContext) -> list[Act]:
        return list(NLU[text]) if text in NLU else super().parse(text, ctx)


class _Answerer:
    """가게 자료에 없는 질문엔 모른다고 한다. 쿠폰 물음은 여기 오지 않아야 한다."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def answer(self, request: AnswerRequest) -> AnswerReply:
        self.asked.append(request.text)
        return AnswerReply("unsure")


def _engine(*coupons: Coupon, answerer: _Answerer | None = None,
            knowledge: KnowledgeStore | None = None) -> DialogEngine:
    engine = DialogEngine(FakeStoreKnowledge.from_fixture(), _Nlu(), StoreConfig.from_fixture(),
                          order_id="o-coupon-q", coupons=coupons, answerer=answerer,
                          knowledge=knowledge)
    engine.start(NOW)
    return engine


def talk(engine: DialogEngine, *texts: str) -> TurnResult:
    result = None
    for text in texts:
        result = engine.handle(text, NOW)
    assert result is not None
    return result


def _clean(result: TurnResult) -> None:
    """쿠폰 물음은 사장님 화면(손님이 궁금해한 것·질문 카드)으로 가지 않는다."""
    assert result.knowledge_requests == ()
    assert result.owner_question is None


# ---------------------------------------------------------------- 적용


def test_an_applicable_cart_hears_the_discount_and_the_pickup_question_again() -> None:
    engine = _engine(COUPON)
    r = talk(engine, "블린 2개요", "그게 다예요")
    assert r.state is State.ASK_PICKUP
    pickup_q = r.utterances[-1]
    r = talk(engine, "쿠폰 돼요?")
    assert r.utterances == [APPLIES, pickup_q]
    assert r.state is State.ASK_PICKUP
    _clean(r)


def test_while_ordering_the_answer_goes_back_to_anything_else() -> None:
    r = talk(_engine(COUPON), "블린 2개요", "할인 되나요")
    assert r.utterances == [APPLIES, MORE_Q]


def test_at_the_readback_the_answer_reads_back_again() -> None:
    engine = _engine(COUPON)
    readback = talk(engine, "블린 2개요", "그게 다예요", "6시 반이요").utterances[-1]
    assert "1,000원 빼고" in readback
    r = talk(engine, "쿠폰 쓸게요")
    assert r.utterances == [APPLIES, readback]
    assert r.state is State.CONFIRM and r.readback


# ---------------------------------------------------------------- 부족: 권유 문장, 통화당 한 번


def test_a_short_cart_hears_the_offer_and_no_says_no_more() -> None:
    engine = _engine(COUPON)
    talk(engine, "메도빅 하나요")
    r = talk(engine, "쿠폰 돼요?")
    assert r.utterances == [OFFER]
    _clean(r)
    assert r.state is State.ORDERING
    assert talk(engine, "아니요").state is State.ASK_PICKUP  # 더 담지 않는다 = 주문 끝
    readback = talk(engine, "6시 반이요").utterances
    assert readback == ["주문 확인할게요. 메도빅 하나, 6시 반에 찾으러 오시고, "
                        "모두 7,500원이에요. 맞으세요?"]


def test_the_offer_counts_as_this_calls_one_offer() -> None:
    engine = _engine(COUPON)
    talk(engine, "메도빅 하나요")
    assert not engine._coupon_offered
    talk(engine, "쿠폰 돼요?")
    assert engine._coupon_offered


def test_yes_to_the_offer_adds_the_suggested_menu() -> None:
    engine = _engine(COUPON)
    talk(engine, "메도빅 하나요", "쿠폰 돼요?")
    r = talk(engine, "네")
    assert [line.item.id for line in r.cart.lines] == ["m01", "m03"]


def test_a_menu_named_after_the_offer_is_added() -> None:
    engine = _engine(COUPON)
    talk(engine, "메도빅 하나요", "쿠폰 돼요?")
    r = talk(engine, "나폴레옹 하나요")
    assert [line.item.id for line in r.cart.lines] == ["m01", "m02"]
    assert r.utterances[-1].endswith(MORE_Q)


def test_a_short_cart_at_the_readback_goes_back_to_ordering_with_the_offer() -> None:
    engine = _engine(COUPON)
    assert talk(engine, "메도빅 하나요", "그게 다예요", "6시 반이요").state is State.CONFIRM
    r = talk(engine, "할인 돼요?")
    assert r.utterances == [OFFER]
    assert r.state is State.ORDERING
    r = talk(engine, "아니요")  # 쿠폰 없이 다시 복창
    assert r.state is State.CONFIRM
    assert r.utterances[-1].endswith("모두 7,500원이에요. 맞으세요?")


def test_a_short_cart_while_a_cancel_question_waits_hears_the_facts_only() -> None:
    """다른 답(취소 확인 등)을 기다리는 중이면 권유 질문 없이 부족액만 알리고 그 질문을 다시."""
    engine = _engine(COUPON)
    talk(engine, "메도빅 하나요", "주문 취소할게요")
    r = talk(engine, "쿠폰 돼요?")
    assert r.utterances == [OFFER.removesuffix(" 더 담으실까요?"), "주문 취소할까요?"]
    assert engine._coupon_offered


# ---------------------------------------------------------------- 빈 장바구니 · 쿠폰 없음


def test_an_empty_cart_hears_the_coupon_name_and_its_minimum() -> None:
    r = talk(_engine(EXPIRED, COUPON), "쿠폰 있어요?")
    assert r.utterances == [
        "전화 주문 1,000원 할인 쿠폰이 있어요. 10,000원 이상 주문하시면 돼요.", ORDER_Q]
    _clean(r)


def test_an_empty_cart_hears_a_coupon_without_a_minimum() -> None:
    r = talk(_engine(PLAIN), "할인 돼요?")
    assert r.utterances == ["단골 500원 할인 쿠폰이 있어요.", ORDER_Q]


def test_no_valid_coupon_says_so() -> None:
    assert talk(_engine(), "할인 돼요?").utterances == [NONE, ORDER_Q]
    assert talk(_engine(EXPIRED), "메도빅 하나요", "쿠폰 돼요?").utterances == [NONE, MORE_Q]


# ---------------------------------------------------------------- 사장님 지식 요청으로 가지 않는다


def test_coupon_questions_from_the_nlu_are_not_passed_on_but_parking_still_is() -> None:
    answerer = _Answerer()
    engine = _engine(COUPON, answerer=answerer, knowledge=KnowledgeStore())
    talk(engine, "블린 2개요")
    for text in (LONG, MENU_DISCOUNT, "쿠폰 돼요?"):
        r = talk(engine, text)
        assert r.utterances == [APPLIES, MORE_Q], text
        _clean(r)
    assert "쿠폰 돼요?" not in answerer.asked  # 규칙이 잡은 말은 답변기를 부르지 않는다
    for text in ("주차 돼요?", "배달 돼요?"):
        r = talk(engine, text)
        assert r.utterances == [PASS_ON, MORE_Q]
        (request,) = r.knowledge_requests
        assert request.text == text
