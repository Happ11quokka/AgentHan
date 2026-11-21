"""L2 대본 시나리오 (티켓 06, spec §9). 러너는 `scenario_runner.py`, 대본은 `scenarios/*.json`."""

from __future__ import annotations

from datetime import datetime

import pytest
from scenario_runner import SCENARIO_DIR, Scenario, TurnView, forbidden, load_all, run

from agenthan.voice.dialog.cart import Cart
from agenthan.voice.dialog.engine import TurnResult
from agenthan.voice.dialog.states import State
from agenthan.voice.store_config import KST

SCENARIOS = load_all()

REQUIRED = [  # 티켓 06의 시나리오 10개
    "s01-single-item",
    "s02-multi-items",
    "s03-change-qty",
    "s04-remove-item",
    "s05-required-option",
    "s06-option-and-request",
    "s07-ambiguous-clarify",
    "s08-unknown-and-sold-out",
    "s09-correction",
    "s10-menu-question",
]

APPROVAL = [  # 주문 승인 티켓 11: 가게 승인 시나리오 4개 (엔진 + 사장님 허브)
    "s27-approval-accept-in-call",
    "s28-approval-out-of-stock-drop",
    "s29-approval-delay-yes",
    "s30-approval-timeout-auto-cancel",
    # 전화 메뉴·주문 현황 티켓 04: 통화 중 대기 0(기본). "네"에 끊고 통화 뒤 결정을 주문 현황으로
    "s31-no-wait-accept-after-call",
    # 티켓 05: 통화 뒤 늦춘 수락·거절·재료 소진·자동 취소가 주문 현황에 남는 모습
    "s32-no-wait-delay-after-call",
    "s33-no-wait-reject-after-call",
    "s34-no-wait-out-of-stock-after-call",
    "s35-no-wait-auto-cancel",
    # 티켓 07: 손님 취소(확인 중·늦춘 수락 뒤)와 제시간 수락 뒤 거부, 한 주문에 결정 한 번
    "s40-customer-cancel-while-checking",
    "s41-customer-cancel-after-delay",
    "s42-customer-cancel-refused-after-accept",
    "s49-coupon-order-status",  # 쿠폰 티켓 09: 쿠폰 주문의 사장님 카드·주문 현황 금액
    # 티켓 13: 비서 메뉴(전화용 가게, 옵션 없음)로 대기 0 주문, 쿠폰을 넣은 시연 흐름
    "s70-assistant-menu-option-as-request",
    "s71-assistant-menu-coupon-demo",
]
# 통화 중 대기를 켠 승인 시나리오 (통화 중 결정 경로가 깨지지 않았는지, 티켓 04)
IN_CALL = APPROVAL[:4]

COUPON = [  # 쿠폰 티켓 09: 적용 복창·모자람(권유 거절, 티켓 10)·기간 밖·고쳐서 빠짐·주문 현황
    "s45-coupon-applied-readback",
    "s46-coupon-short-plain-readback",
    "s47-coupon-out-of-period",
    "s48-coupon-dropped-after-fix",
    "s49-coupon-order-status",
    # 쿠폰 티켓 10: 금액이 모자랄 때 권유 (수락·다른 메뉴로 채움·거절·채울 메뉴 없음·복창 뒤 빠짐)
    "s50-coupon-offer-accepted",
    "s51-coupon-offer-other-menu",
    "s52-coupon-offer-declined-once",
    "s53-coupon-offer-no-menu-fills",
    "s54-coupon-dropped-after-offer",
    # 쿠폰 티켓 11: 쿠폰 문의(적용·부족·빈 장바구니). 쿠폰 없는 가게 문의(s63)는 아래 목록에만
    "s60-coupon-question-applied",
    "s61-coupon-question-short",
    "s62-coupon-question-empty-cart",
    "s71-assistant-menu-coupon-demo",  # 티켓 13: 비서 메뉴·비서 쿠폰 시연 흐름
]
COUPON_QUESTION = [  # 쿠폰 티켓 11: 쿠폰 문의 4가지
    "s60-coupon-question-applied",
    "s61-coupon-question-short",
    "s62-coupon-question-empty-cart",
    "s63-coupon-question-no-coupon",
]
ASSISTANT_MENU = [  # 티켓 13: 비서가 게시한 메뉴(옵션 없음)로 도는 시나리오
    "s70-assistant-menu-option-as-request",
    "s71-assistant-menu-coupon-demo",
]


def _param(scenario: Scenario) -> object:
    marks = [pytest.mark.xfail(reason=scenario.xfail, strict=True)] if scenario.xfail else []
    return pytest.param(scenario, id=scenario.id, marks=marks)


@pytest.mark.parametrize("scenario", [_param(s) for s in SCENARIOS])
def test_scenario(scenario: Scenario) -> None:
    result = run(scenario)
    transcript = "\n".join(result.transcript)
    assert not result.failures, "\n".join([*result.failures, "", transcript])


def test_scenario_files_are_consistent() -> None:
    ids = [s.id for s in SCENARIOS]
    assert len(ids) == len(set(ids))
    assert all(s.path.stem == s.id for s in SCENARIOS)
    assert set(REQUIRED) <= set(ids)
    assert set(COUPON) <= set(ids)
    assert set(COUPON_QUESTION) <= set(ids)
    # 쿠폰이 없는 시나리오는 쿠폰 전 그대로다(고치지 않고 통과한다)
    assert sorted(s.id for s in SCENARIOS if s.coupons) == COUPON
    assert sorted(s.id for s in SCENARIOS if s.menus) == ASSISTANT_MENU
    required = [s for s in SCENARIOS if s.id in REQUIRED]
    assert not any(s.xfail for s in required), "필수 시나리오 10개는 xfail이면 안 된다"
    approval = [s for s in SCENARIOS if s.approval]
    assert sorted(s.id for s in approval) == APPROVAL
    assert not any(s.xfail for s in approval)
    assert sorted(s.id for s in approval if s.approval_wait_s > 0) == IN_CALL
    assert SCENARIO_DIR.is_dir()


# ---------------------------------------------------------------- 금지 규칙이 실제로 잡는지


NOW = datetime(2026, 11, 8, 17, 10, tzinfo=KST)


def _view(utterances: list[str], *, guest: str = "네", state: State = State.ORDERING,
          before: TurnResult | None = None, order_status: str | None = None) -> TurnView:
    from agenthan.core.contracts import Order

    order = Order("o", [], status=order_status) if order_status else None  # type: ignore[arg-type]
    result = TurnResult(utterances, state, Cart(), order)
    return TurnView(guest, result, before or TurnResult([], State.ORDERING, Cart(), None))


def test_forbidden_passes_a_normal_turn() -> None:
    assert forbidden(_view(["어떤 걸로 드릴까요?"])) == []


@pytest.mark.parametrize(
    ("utterances", "rule"),
    [
        ([], "대답 없는 턴"),
        (["메도빅 하나요. 더 필요하신 거 있으세요?", "몇 시쯤 오세요?"], "한 턴 두 질문"),
        (["주문 확인할게요. 메도빅 하나, 6시 반에 찾으러 오시고, 모두 7,500원이에요. 맞으세요?"],
         "복창 총액"),
        (["가" * 150 + "."], "151자 문장"),
        (["메도빅(꿀 케이크) 하나요."], "괄호·이모지·마크다운"),
        (["메도빅 하나요 🍰"], "괄호·이모지·마크다운"),
        (["**메도빅** 하나요."], "괄호·이모지·마크다운"),
    ],
)
def test_forbidden_catches(utterances: list[str], rule: str) -> None:
    problems = forbidden(_view(utterances))
    assert any(rule in p for p in problems), problems


def test_forbidden_checks_the_readback_against_the_coupon_result() -> None:
    """쿠폰 티켓 09: 복창 총액은 쿠폰 계산의 낼 금액, "○원 빼고"는 그 할인액이어야 한다."""
    from datetime import date

    from agenthan.core.contracts import MenuItem
    from agenthan.voice.dialog.coupons import Coupon, check_coupons

    cart = Cart()
    cart.add(MenuItem("m01", "메도빅", 12_000), 1)
    coupon = Coupon("c1", "전화 주문 1,000원 할인", "amount", 1000, date(2026, 11, 1),
                    date(2026, 11, 30), min_order=10_000)
    result = check_coupons(12_000, [coupon], date(2026, 11, 8))
    head = ("주문 확인할게요. 메도빅 하나, 6시 반에 찾으러 오시고, "
            "전화 주문 1,000원 할인 쿠폰이 있어서 ")

    def problems(text: str) -> list[str]:
        turn = TurnResult([text], State.CONFIRM, cart, None, readback=True, coupon=result)
        return forbidden(TurnView("6시 반이요", turn, TurnResult([], State.ASK_PICKUP, cart, None)))

    assert problems(head + "1,000원 빼고 모두 11,000원이에요. 맞으세요?") == []
    wrong_total = problems(head + "1,000원 빼고 모두 12,000원이에요. 맞으세요?")
    assert any("복창 총액" in p for p in wrong_total)
    wrong_discount = problems(head + "500원 빼고 모두 11,000원이에요. 맞으세요?")
    assert any("복창 할인액" in p for p in wrong_discount)


def test_forbidden_catches_confirm_turn_without_readback() -> None:
    """리뷰 C1: CONFIRM으로 끝난 턴은 복창(또는 취소 확인)을 말해야 한다."""
    problems = forbidden(_view(["몇 시쯤 오세요?"], state=State.CONFIRM))
    assert any("복창 없는 CONFIRM" in p for p in problems), problems
    assert forbidden(_view(["주문 취소할까요?"], state=State.CONFIRM)) == []


def test_forbidden_catches_confirmed_without_readback() -> None:
    problems = forbidden(_view(["감사합니다."], state=State.CLOSING, order_status="confirmed"))
    assert any("확인 전 confirmed" in p for p in problems)


def test_forbidden_catches_confirmed_on_content_after_readback() -> None:
    before = TurnResult(["…, 총 0원이에요. 맞으세요?"], State.CONFIRM, Cart(), None)
    view = _view(["감사합니다."], guest="네, 6시 반이요", state=State.CLOSING, before=before,
                 order_status="confirmed")
    assert any("확인 전 confirmed" in p for p in forbidden(view))


def test_runner_checks_cart_on_turns_without_expect_cart() -> None:
    """리뷰 P5: 기대 장바구니가 없는 턴은 직전 턴과 같아야 한다."""
    scenario = Scenario(id="t", title="", now=NOW, turns=[{"guest": "메도빅 하나요"}],
                        expect_order=None)
    failures = run(scenario).failures
    assert any("장바구니" in f for f in failures), failures


def test_forbidden_allows_confirmed_after_readback_and_yes() -> None:
    before = TurnResult(["…, 총 0원이에요. 맞으세요?"], State.CONFIRM, Cart(), None)
    view = _view(["감사합니다."], state=State.CLOSING, before=before, order_status="confirmed")
    assert forbidden(view) == []


def test_forbidden_with_approval_wants_the_readback_yes_on_the_request_turn() -> None:
    """가게 승인: 복창 뒤 "네"가 확인 요청을 연다. 사장님이 받아들인 주문만 확정할 수 있다."""
    from agenthan.core.contracts import Order
    from agenthan.voice.dialog.approval import ApprovalRequest

    readback = TurnResult(["…, 총 0원이에요. 맞으세요?"], State.CONFIRM, Cart(), None)
    hold = TurnResult(["사장님께 주문 확인 중이에요. 잠시만요."], State.CONFIRM, Cart(), None,
                      approval_request=ApprovalRequest(1, Order("o", [])))
    assert forbidden(TurnView("네", hold, readback, approval=True)) == []
    problems = forbidden(TurnView("6시 반이요", hold, readback, approval=True))
    assert any("확인 전 확인 요청" in p for p in problems), problems

    closing = TurnResult(["감사합니다."], State.CLOSING, Cart(),
                         Order("o", [], status="confirmed"))
    problems = forbidden(TurnView("", closing, hold, approval=True))
    assert any("승인 전 confirmed" in p for p in problems), problems
    assert forbidden(TurnView("", closing, hold, approval=True, approved=[])) == []
