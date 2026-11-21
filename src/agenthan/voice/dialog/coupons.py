"""쿠폰 계산 (전화 메뉴·주문 현황·쿠폰 티켓 09, spec "쿠폰"). 쿠폰 금액을 셈하는 곳은 여기 하나다.

    result = check_coupons(cart.total(), coupons, today, menu)
    result.total  # 낼 금액. 복창·손님 화면·사장님 카드·주문 현황·DB 합계가 모두 이 값을 쓴다

주문서에는 계산 결과의 쿠폰·할인액이 적힌다(`Order.coupon_*`·`discount`). 적힌 할인액으로 낼 금액을
다시 낼 때는 `payable`(사장님 카드·주문 현황·통화 이벤트), 금액 칸 한 벌(할인 전·낼 금액·할인·쿠폰
이름)은 `amount_json`이다. DB(voice를 import하지 않는다)는 계약의 `Order.total`을 쓴다(같은 셈).

할인 전 금액(장바구니 합계)과 가게 쿠폰 목록, 오늘(가게 시각) 날짜를 받아 셋 중 하나를 돌려준다.

- `Applied`(적용): 쓸 쿠폰, 할인액, 낼 금액
- `Shortfall`(부족): 쓸 수 있는 쿠폰은 없고 최소 주문 금액이 모자란 쿠폰이 있다. 그 쿠폰, 부족액,
  권할 메뉴(없으면 None)
- `NoCoupon`(없음): 낼 금액 = 할인 전 금액

규칙 (spec):
- 유효한 쿠폰은 오늘이 시작일~종료일 안(양 끝 날 포함)인 것. 게시 상태는 보지 않는다
- 최소 주문 금액은 할인 전 금액으로 판정한다
- 금액 쿠폰은 그 금액, 비율 쿠폰은 할인 전 금액 × 비율을 10원 단위로 내린 값. 할인액은 할인 전
  금액을 넘지 않는다. 할인액이 0원이면 쓸 수 있는 쿠폰이 아니다(빈 장바구니 등)
- 쓸 수 있는 쿠폰이 여럿이면 할인액이 가장 큰 것 하나. 같으면 먼저 저장한 것(목록 앞)
- 쓸 수 있는 쿠폰이 없고 모자란 쿠폰이 있으면 부족액이 가장 작은 것(같으면 먼저 저장한 것)이
  "부족"의 대상이다. 권할 메뉴는 품절(오늘 품절 포함)이 아닌 메뉴 가운데 한 개로 부족액을 채우는
  가장 싼 메뉴(같은 값이면 메뉴판 앞). 없으면 None

쿠폰은 가게 전체 것이다(손님별 보유 없음). 전화는 통화 시작 때 가게 스냅숏(`StoreSnapshot.coupons`)
으로 받는다. 비서 쿠폰 JSON → `Coupon`은 `Coupon.from_json`이 한다(읽을 수 없는 쿠폰은 버린다).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from agenthan.core.contracts import MenuItem
from agenthan.voice.dialog import templates

DiscountType = Literal["amount", "percent"]


@dataclass(frozen=True)
class Coupon:
    """가게 쿠폰 하나 (비서 쿠폰의 전화 몫)."""

    id: str
    title: str  # 쿠폰 이름. 예: "전화 주문 1,000원 할인"
    discount_type: DiscountType
    discount_value: int  # 원(amount) 또는 %(percent)
    start_date: date
    end_date: date
    min_order: int = 0  # 최소 주문 금액(할인 전). 0 = 조건 없음

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Coupon | None:
        """비서 쿠폰·게시 값 한 개 → 쿠폰. 칸이 빠졌거나 읽을 수 없으면 None."""
        try:
            kind = data["discount_type"]
            if kind not in ("amount", "percent"):
                return None
            return cls(
                id=str(data["id"]),
                title=str(data.get("title") or ""),
                discount_type=kind,
                discount_value=int(data["discount_value"]),
                start_date=date.fromisoformat(str(data["start_date"])),
                end_date=date.fromisoformat(str(data["end_date"])),
                min_order=int(data.get("min_order") or 0),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def valid_on(self, today: date) -> bool:
        return self.start_date <= today <= self.end_date

    def discount_for(self, subtotal: int) -> int:
        """할인 전 금액에 대한 할인액(최소 주문 금액은 보지 않는다). 할인 전 금액을 넘지 않는다."""
        if self.discount_type == "percent":
            amount = subtotal * self.discount_value // 1000 * 10  # × 비율, 10원 단위 내림
        else:
            amount = self.discount_value
        return max(0, min(amount, subtotal))


@dataclass(frozen=True)
class Applied:
    """적용: 이 쿠폰으로 `discount`원 빼고 `total`원."""

    coupon: Coupon
    discount: int
    subtotal: int  # 할인 전 금액

    @property
    def total(self) -> int:
        return payable(self.subtotal, self.discount)


@dataclass(frozen=True)
class Shortfall:
    """부족: `short`원을 더 담으면 이 쿠폰을 쓸 수 있다. 낼 금액은 할인 전 금액 그대로."""

    coupon: Coupon
    short: int  # 부족액 = 최소 주문 금액 - 할인 전 금액
    suggest: MenuItem | None  # 한 개로 부족액을 채우는 가장 싼 메뉴. 없으면 None
    subtotal: int

    discount = 0

    @property
    def total(self) -> int:
        return self.subtotal


@dataclass(frozen=True)
class NoCoupon:
    """없음: 쓸 쿠폰도 모자란 쿠폰도 없다."""

    subtotal: int

    discount = 0

    @property
    def total(self) -> int:
        return self.subtotal


CouponResult = Applied | Shortfall | NoCoupon


def payable(subtotal: int, discount: int) -> int:
    """낼 금액 = 할인 전 금액 - 할인액 (할인액은 계산이 할인 전 금액을 넘지 않게 정했다)."""
    return subtotal - discount


def amount_json(subtotal: int, discount: int = 0, coupon_name: str = "") -> dict[str, Any]:
    """금액 칸 한 벌 (통화 `state`·`order` 이벤트, 주문 현황 `amount`): `subtotal`(할인 전)·
    `total`(낼 금액). 쿠폰을 쓴 주문만 `discount`·`coupon_name`·`coupon_label`(화면 쿠폰 줄 이름)이
    붙는다(없으면 쿠폰 전과 같다)."""
    amount: dict[str, Any] = {"subtotal": subtotal, "total": payable(subtotal, discount)}
    if discount:
        # 화면의 쿠폰 줄 이름은 통화가 부르는 이름과 같다("… 쿠폰", `templates.coupon_phrase`)
        amount |= {"discount": discount, "coupon_name": coupon_name,
                   "coupon_label": templates.coupon_phrase(coupon_name)}
    return amount


def check_coupons(
    subtotal: int, coupons: Iterable[Coupon], today: date, menu: Sequence[MenuItem] = ()
) -> CouponResult:
    """할인 전 금액 + 가게 쿠폰(저장한 순서) + 오늘 + 이 통화 메뉴(오늘 품절 반영) → 계산 결과."""
    best: Applied | None = None
    near: tuple[Coupon, int] | None = None
    for coupon in coupons:
        if not coupon.valid_on(today):
            continue
        if subtotal < coupon.min_order:
            short = coupon.min_order - subtotal
            if near is None or short < near[1]:
                near = (coupon, short)
            continue
        discount = coupon.discount_for(subtotal)
        if discount > 0 and (best is None or discount > best.discount):
            best = Applied(coupon, discount, subtotal)
    if best is not None:
        return best
    if near is not None:
        coupon, short = near
        return Shortfall(coupon, short, suggest_menu(short, menu), subtotal)
    return NoCoupon(subtotal)


def suggest_menu(short: int, menu: Sequence[MenuItem]) -> MenuItem | None:
    """품절이 아닌 메뉴 가운데 한 개로 `short`원을 채우는 가장 싼 메뉴. 같은 값이면 메뉴판 앞."""
    enough = [m for m in menu if not m.sold_out and m.price >= short]
    return min(enough, key=lambda m: m.price) if enough else None
