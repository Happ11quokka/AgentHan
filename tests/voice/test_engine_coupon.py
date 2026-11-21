"""엔진의 쿠폰 (쿠폰 티켓 09). 복창·주문서·턴 결과가 같은 쿠폰 계산 결과를 쓴다.

대화 흐름은 대본 시나리오(s45~s49)가 본다. 여기서는 시나리오로 보기 어려운 것만: 넘김 주문서,
턴 결과의 계산 결과, 오늘 날짜를 가게 시각으로 셈하는지.
"""

from __future__ import annotations

from datetime import date, datetime

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.coupons import Applied, Coupon, NoCoupon, Shortfall
from agenthan.voice.dialog.engine import DialogEngine
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.store_config import KST, StoreConfig

NOW = datetime(2026, 11, 8, 17, 10, tzinfo=KST)
COUPON = Coupon("coupon-1", "전화 주문 1,000원 할인", "amount", 1000, date(2026, 11, 2),
                date(2026, 12, 31), min_order=10_000)


def _engine(*coupons: Coupon) -> DialogEngine:
    engine = DialogEngine(FakeStoreKnowledge.from_fixture(), FakeNlu(), StoreConfig.from_fixture(),
                          order_id="o-coupon", coupons=coupons)
    engine.start(NOW)
    return engine


def test_a_handoff_order_carries_no_coupon() -> None:
    """사장님께 넘긴 통화의 주문서에는 쿠폰이 없다(금액이 조건을 채워도)."""
    engine = _engine(COUPON)
    assert isinstance(engine.handle("블린 2개요", NOW).coupon, Applied)
    order = engine.handle("사장님 바꿔 주세요", NOW).order
    assert order is not None and order.status == "handoff"
    assert (order.coupon_id, order.coupon_name, order.discount) == ("", "", 0)


def test_each_turn_reports_the_coupon_result_of_its_cart() -> None:
    engine = _engine(COUPON)
    assert engine.handle("메도빅 하나요", NOW).coupon == Shortfall(
        COUPON, 2_500, FakeStoreKnowledge.from_fixture().get_menu("m03"), 7_500)
    assert engine.handle("나폴레옹 하나요", NOW).coupon == Applied(COUPON, 1_000, 14_500)
    assert _engine().handle("메도빅 하나요", NOW).coupon == NoCoupon(7_500)


def test_the_coupon_day_is_the_store_date() -> None:
    """UTC로 11/1 23시는 가게 시각(KST) 11/2 8시다. 11/2 시작 쿠폰이 쓰인다."""
    utc = datetime.fromisoformat("2026-11-01T23:00:00+00:00")
    engine = DialogEngine(FakeStoreKnowledge.from_fixture(), FakeNlu(),
                          StoreConfig.from_fixture(), order_id="o", coupons=[COUPON])
    engine.start(utc)
    assert isinstance(engine.handle("블린 2개요", utc).coupon, Applied)
