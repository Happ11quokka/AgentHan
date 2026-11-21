"""쿠폰 계산 표 테스트 (전화 메뉴·주문 현황·쿠폰 티켓 09, `voice/dialog/coupons.py`).

할인 전 금액·쿠폰 목록·오늘·메뉴 → 적용 / 부족 / 없음. 계산 규칙은 spec "쿠폰"이다.
"""

from __future__ import annotations

from datetime import date

import pytest

from agenthan.core.contracts import MenuItem
from agenthan.voice.dialog.coupons import (
    Applied,
    Coupon,
    NoCoupon,
    Shortfall,
    check_coupons,
)

TODAY = date(2026, 11, 21)


def coupon(cid: str, kind: str = "amount", value: int = 1000, *, min_order: int = 0,
           start: str = "2026-11-01", end: str = "2026-12-31") -> Coupon:
    made = Coupon.from_json({"id": cid, "title": f"쿠폰 {cid}", "discount_type": kind,
                             "discount_value": value, "min_order": min_order,
                             "start_date": start, "end_date": end})
    assert made is not None
    return made


MENU = [
    MenuItem("m1", "아메리카노", 3000),
    MenuItem("m2", "라떼", 3500),
    MenuItem("m3", "메도빅", 9000),
    MenuItem("m4", "블린", 2500, sold_out=True),  # 품절(오늘 품절 포함)은 권하지 않는다
]


# (이름, 할인 전 금액, 쿠폰들, 기대: (종류, 쿠폰 id, 할인액, 낼 금액))
APPLIED_CASES = [
    ("금액 쿠폰", 12000, [coupon("a")], ("a", 1000, 11000)),
    ("비율 10%", 12000, [coupon("p", "percent", 10)], ("p", 1200, 10800)),
    ("비율 10원 내림", 12345, [coupon("p", "percent", 10)], ("p", 1230, 11115)),
    ("비율 15% 내림", 9990, [coupon("p", "percent", 15)], ("p", 1490, 8500)),
    ("할인액 상한(금액)", 800, [coupon("a", value=1000)], ("a", 800, 0)),
    ("할인액 상한(100%)", 4500, [coupon("p", "percent", 100)], ("p", 4500, 0)),
    ("최소 주문 금액 딱", 10000, [coupon("a", min_order=10000)], ("a", 1000, 9000)),
    ("큰 할인 고르기", 20000, [coupon("a", value=1000), coupon("p", "percent", 10)],
     ("p", 2000, 18000)),
    ("같으면 먼저 저장한 것", 10000, [coupon("a", value=1000), coupon("p", "percent", 10)],
     ("a", 1000, 9000)),
    ("모자란 쿠폰보다 쓸 수 있는 쿠폰", 6000,
     [coupon("big", value=3000, min_order=10000), coupon("a", value=500)], ("a", 500, 5500)),
    ("시작일 당일", 12000, [coupon("a", start="2026-11-21")], ("a", 1000, 11000)),
    ("종료일 당일", 12000, [coupon("a", end="2026-11-21")], ("a", 1000, 11000)),
    ("기간 밖은 고르지 않는다", 12000,
     [coupon("old", value=5000, end="2026-11-20"), coupon("a")], ("a", 1000, 11000)),
]


@pytest.mark.parametrize(("subtotal", "coupons", "expected"),
                         [c[1:] for c in APPLIED_CASES], ids=[c[0] for c in APPLIED_CASES])
def test_applied(subtotal, coupons, expected) -> None:
    result = check_coupons(subtotal, coupons, TODAY, MENU)
    assert isinstance(result, Applied)
    assert (result.coupon.id, result.discount, result.total) == expected
    assert result.subtotal == subtotal


# (이름, 할인 전 금액, 쿠폰들, 메뉴, 기대: (쿠폰 id, 부족액, 권할 메뉴 id 또는 None))
SHORT_CASES = [
    ("부족액과 가장 싼 메뉴", 7000, [coupon("a", min_order=10000)], MENU, ("a", 3000, "m1")),
    ("한 개로 못 채우면 그보다 비싼 것", 6600, [coupon("a", min_order=10000)], MENU,
     ("a", 3400, "m2")),
    ("품절 메뉴는 권하지 않는다", 7500, [coupon("a", min_order=10000)], MENU, ("a", 2500, "m1")),
    ("한 개로 채울 메뉴가 없으면 비운다", 1000, [coupon("a", min_order=11000)], MENU,
     ("a", 10000, None)),
    ("메뉴가 다 품절이면 비운다", 7000, [coupon("a", min_order=10000)],
     [MenuItem("x", "아메리카노", 3000, sold_out=True)], ("a", 3000, None)),
    ("같은 값이면 메뉴판 앞", 7000, [coupon("a", min_order=10000)],
     [MenuItem("x", "라떼", 3000), MenuItem("y", "아메리카노", 3000)], ("a", 3000, "x")),
    ("부족액이 가장 작은 쿠폰", 7000,
     [coupon("far", min_order=15000), coupon("near", min_order=9000)], MENU,
     ("near", 2000, "m1")),
    ("부족액이 같으면 먼저 저장한 것", 7000,
     [coupon("first", min_order=9000), coupon("second", value=2000, min_order=9000)], MENU,
     ("first", 2000, "m1")),
    ("기간 밖 쿠폰은 부족 대상도 아니다", 7000,
     [coupon("old", min_order=8000, end="2026-11-20"), coupon("a", min_order=10000)], MENU,
     ("a", 3000, "m1")),
]


@pytest.mark.parametrize(("subtotal", "coupons", "menu", "expected"),
                         [c[1:] for c in SHORT_CASES], ids=[c[0] for c in SHORT_CASES])
def test_shortfall(subtotal, coupons, menu, expected) -> None:
    result = check_coupons(subtotal, coupons, TODAY, menu)
    assert isinstance(result, Shortfall)
    suggest = result.suggest.id if result.suggest else None
    assert (result.coupon.id, result.short, suggest) == expected
    assert (result.discount, result.total) == (0, subtotal)


NONE_CASES = [
    ("쿠폰 없음", 12000, []),
    ("시작 전", 12000, [coupon("a", start="2026-11-22")]),
    ("끝남", 12000, [coupon("a", end="2026-11-20")]),
    ("빈 장바구니", 0, [coupon("a"), coupon("p", "percent", 10)]),
    ("할인액이 0원인 비율 쿠폰", 90, [coupon("p", "percent", 10)]),
]


@pytest.mark.parametrize(("subtotal", "coupons"), [c[1:] for c in NONE_CASES],
                         ids=[c[0] for c in NONE_CASES])
def test_none(subtotal, coupons) -> None:
    result = check_coupons(subtotal, coupons, TODAY, MENU)
    assert result == NoCoupon(subtotal)
    assert (result.discount, result.total) == (0, subtotal)


@pytest.mark.parametrize("raw", [
    {"id": "c", "title": "t", "discount_type": "free", "discount_value": 1,
     "start_date": "2026-11-01", "end_date": "2026-11-30"},
    {"id": "c", "title": "t", "discount_type": "amount", "discount_value": 1,
     "start_date": "언제", "end_date": "2026-11-30"},
    {"id": "c", "title": "t", "discount_type": "amount", "start_date": "2026-11-01",
     "end_date": "2026-11-30"},
])
def test_unreadable_coupon_is_dropped(raw) -> None:
    assert Coupon.from_json(raw) is None


def test_assistant_coupon_without_min_order_has_no_condition() -> None:
    """조건 없는 비서 쿠폰은 `min_order` 키 없이 저장된다(티켓 08) → 0."""
    made = Coupon.from_json({"id": "coupon-2", "title": "첫 주문", "discount_type": "amount",
                             "discount_value": 500, "start_date": "2026-11-01",
                             "end_date": "2026-11-30", "status": "saved_not_published"})
    assert made is not None and made.min_order == 0
    assert isinstance(check_coupons(1000, [made], TODAY), Applied)
