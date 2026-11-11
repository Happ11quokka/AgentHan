import json
from datetime import date

import pytest

from agenthan.agent import value_source as vs
from agenthan.agent.owner_assistant import tool_params
from agenthan.tools.dummy_store import SEED_PATH

TODAY = date(2026, 11, 10)
STORE = json.loads(SEED_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("만 오천 원", 15000),
        ("1만 2천원", 12000),
        ("삼천오백 원이요", 3500),
        ("12,000원", 12000),
        ("10% 할인", 10),
        ("가격은 5000원", 5000),
    ],
)
def test_korean_numbers(text, value):
    assert value in vs.numbers(text)


def test_bare_syllables_are_not_numbers():
    assert vs.numbers("일요일 이름 사진") == set()


def check(feature, args, said):
    return vs.check(tool_params(feature), args, said, STORE, today=TODAY)


def test_values_the_owner_said_pass():
    assert (
        check("business_info", {"field": "phone", "value": "02-777-8888"}, "새 번호는 02-777-8888")[
            1
        ]
        == []
    )
    hours = {"day": "sat", "closed": False, "open_time": "10:00", "close_time": "20:00"}
    assert check("business_hours", hours, "토요일은 10시부터 저녁 8시까지 할게요")[1] == []
    coupon = {
        "title": "겨울 할인 쿠폰",
        "discount_type": "amount",
        "discount_value": 2000,
        "start_date": "2026-11-15",
        "end_date": "2026-11-30",
    }
    said = "이름은 겨울 할인 쿠폰, 2000원 할인이요\n11월 15일부터 30일까지요"
    assert check("coupon", coupon, said)[1] == []
    assert check("menu", {"name": "블린", "price": 15000}, "블린 만 오천 원으로")[1] == []
    sources, missing = check(
        "reservation",
        {"reservation_id": "resv-2", "status": "confirmed"},
        "예약 확정해 주세요\n내일 점심 4명 예약이요",
    )
    assert missing == [] and sources["reservation_id"] == "lookup"
    reply = {"inquiry": "talk-2", "reply_text": "안녕하세요. 10개 이상 단체 주문 가능합니다."}
    assert (
        check(
            "talktalk", reply, "단체 주문 물어본 거요\n답장은 '10개 이상 단체 주문 가능합니다'로"
        )[1]
        == []
    )


def test_values_the_owner_did_not_say_are_refused():
    # The store's own number or a neighbour of it is not the owner's new number.
    assert check(
        "business_info", {"field": "phone", "value": "02-000-0001"}, "전화번호 바꿔 주세요"
    )[1]
    assert check("menu", {"name": "피로시키", "price": 3000}, "피로시키 추가해 주세요")[1] == [
        "가격 (원)"
    ]
    # Two new orders and no description: the model must not pick one.
    assert check("order", {"order_id": "order-1", "status": "accepted"}, "주문 접수해 주세요")[1]
    assert check(
        "reservation", {"reservation_id": "resv-1", "status": "confirmed"}, "예약 처리해 줘"
    )[1]
    assert check("onsite_payment", {"enabled": True}, "매장결제 어떻게 해요?")[1]
